from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.repository import get_shared_background_context_rows
from ai_stock_sentinel.db.models import SharedBackgroundContext


WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE = "weekly_major_holders"
TDCC_CHIP_STABILITY_SOURCE = "tdcc_weekly_major_holders"
WEEKLY_HOLDER_RATIO_KEYS = (
    "thousand_lot_holder_ratio",
    "large_holder_400_lot_plus_ratio",
    "retail_100_lot_or_less_ratio",
)


def weekly_major_holders_projection_by_symbol(
    db: Session,
    *,
    symbols: list[str],
    consumer: str,
    reference_date: date,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}

    current_rows = get_shared_background_context_rows(
        db,
        symbols=symbols,
        context_types=[WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE],
        consumer=consumer,
        reference_date=reference_date,
        point_in_time=True,
    )
    if not current_rows:
        return {}

    historical_rows = db.scalars(
        select(SharedBackgroundContext).where(
            SharedBackgroundContext.symbol.in_(symbols),
            SharedBackgroundContext.context_type == WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE,
        )
    ).all()

    projection_by_symbol: dict[str, dict[str, Any]] = {}
    for current in current_rows:
        previous = _previous_weekly_major_holders_row(current, historical_rows, consumer=consumer)
        previous_previous = (
            _previous_weekly_major_holders_row(previous, historical_rows, consumer=consumer)
            if previous is not None
            else None
        )
        projection = weekly_major_holders_projection(
            current,
            previous=previous,
            previous_previous=previous_previous,
        )
        if projection is not None:
            projection_by_symbol[current.symbol] = projection
    return projection_by_symbol


def weekly_major_holders_projection(
    current: SharedBackgroundContext,
    *,
    previous: SharedBackgroundContext | None,
    previous_previous: SharedBackgroundContext | None = None,
) -> dict[str, Any] | None:
    if current.freshness == "missing":
        return None
    payload = _mapping(current.payload)
    if not any(_float_value(payload.get(key)) is not None for key in WEEKLY_HOLDER_RATIO_KEYS):
        return None

    previous_payload = _mapping(previous.payload) if previous is not None else {}
    previous_previous_payload = _mapping(previous_previous.payload) if previous_previous is not None else {}
    projection: dict[str, Any] = {
        "status": current.freshness,
        "as_of_date": current.as_of_date.isoformat() if current.as_of_date is not None else None,
        "previous_as_of_date": previous.as_of_date.isoformat()
        if previous is not None and previous.as_of_date is not None
        else None,
    }
    for key in WEEKLY_HOLDER_RATIO_KEYS:
        current_value = _float_value(payload.get(key))
        previous_value = _float_value(previous_payload.get(key))
        previous_previous_value = _float_value(previous_previous_payload.get(key))
        delta_key = f"{key}_delta_pp"
        projection[key] = current_value
        projection[delta_key] = (
            round(current_value - previous_value, 4)
            if current_value is not None and previous_value is not None
            else None
        )
        if key == "thousand_lot_holder_ratio":
            projection["previous_thousand_lot_holder_ratio_delta_pp"] = (
                round(previous_value - previous_previous_value, 4)
                if previous_value is not None and previous_previous_value is not None
                else None
            )
            projection["consecutive_thousand_lot_holder_ratio_increase_count"] = (
                2
                if projection[delta_key] is not None
                and projection[delta_key] > 0
                and projection["previous_thousand_lot_holder_ratio_delta_pp"] is not None
                and projection["previous_thousand_lot_holder_ratio_delta_pp"] > 0
                else 1
                if projection[delta_key] is not None and projection[delta_key] > 0
                else 0
            )
    return projection


def chip_stability_context_from_weekly_major_holders(
    weekly_major_holders: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not weekly_major_holders:
        return None

    status = str(weekly_major_holders.get("status") or "unknown")
    ratio = _float_value(weekly_major_holders.get("thousand_lot_holder_ratio"))
    delta = _float_value(weekly_major_holders.get("thousand_lot_holder_ratio_delta_pp"))
    consecutive_increase_count = int(weekly_major_holders.get("consecutive_thousand_lot_holder_ratio_increase_count") or 0)
    caveats = [
        {
            "code": "weekly_chip_stability_companion_only",
            "message": "TDCC 週頻籌碼穩定性補充，不納入 technical score、Daily Radar ranking 或 portfolio risk 分數。",
        }
    ]

    if status != "fresh":
        caveats.append({
            "code": "tdcc_weekly_holders_not_fresh",
            "message": "TDCC 週頻資料不是 fresh，僅保留資料狀態，不產生籌碼穩定性強化判斷。",
        })
        state = "unknown"
        trend = "unavailable"
        summary = "TDCC 週頻資料不是 fresh，暫不判讀籌碼穩定性趨勢。"
    elif delta is None:
        caveats.append({
            "code": "tdcc_previous_period_missing",
            "message": "缺少上一期有效 TDCC snapshot，僅顯示當期千張大戶持股比例。",
        })
        state = "unknown"
        trend = "unavailable"
        summary = "缺少上一期資料，暫不判讀千張大戶持股比例趨勢。"
    elif delta > 0 and consecutive_increase_count >= 2:
        state = "stable"
        trend = "strengthening"
        summary = "千張大戶持股比例連續增加，籌碼愈加穩定。"
    elif delta > 0:
        state = "stable"
        trend = "improving"
        summary = "千張大戶持股比例增加，籌碼穩定性提升。"
    elif delta < 0:
        caveats.append({
            "code": "tdcc_decline_not_standalone_bearish",
            "message": "千張大戶持股比例下降代表籌碼穩定性轉弱或集中度下降，但不能單獨判定看空。",
        })
        state = "weakening"
        trend = "weakening"
        summary = "千張大戶持股比例下降，籌碼穩定性轉弱或集中度下降。"
    else:
        state = "stable"
        trend = "flat"
        summary = "千張大戶持股比例持平，籌碼穩定性未見明顯變化。"

    return {
        "source": TDCC_CHIP_STABILITY_SOURCE,
        "status": status,
        "as_of_date": weekly_major_holders.get("as_of_date"),
        "previous_as_of_date": weekly_major_holders.get("previous_as_of_date"),
        "thousand_lot_holder_ratio": ratio,
        "thousand_lot_holder_ratio_delta_pp": delta,
        "state": state,
        "trend": trend,
        "summary": summary,
        "caveats": caveats,
    }


def _previous_weekly_major_holders_row(
    current: SharedBackgroundContext,
    rows: list[SharedBackgroundContext],
    *,
    consumer: str,
) -> SharedBackgroundContext | None:
    if current.as_of_date is None:
        return None
    previous_rows = [
        row
        for row in rows
        if row.id != current.id
        and row.symbol == current.symbol
        and row.context_type == WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE
        and row.freshness != "missing"
        and _context_applies_to_consumer(row, consumer)
        and row.as_of_date is not None
        and row.as_of_date < current.as_of_date
        and _has_weekly_holder_ratio(row.payload)
    ]
    if not previous_rows:
        return None
    return max(previous_rows, key=_shared_context_sort_key)


def _context_applies_to_consumer(row: SharedBackgroundContext, consumer: str) -> bool:
    consumers = row.applicable_consumers or []
    return "*" in consumers or consumer in consumers


def _has_weekly_holder_ratio(payload: Any) -> bool:
    mapping = _mapping(payload)
    return any(_float_value(mapping.get(key)) is not None for key in WEEKLY_HOLDER_RATIO_KEYS)


def _shared_context_sort_key(row: SharedBackgroundContext) -> tuple[Any, ...]:
    return (
        row.as_of_date or date.min,
        (row.updated_at or row.created_at).isoformat() if row.updated_at or row.created_at else "",
        row.id or 0,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
