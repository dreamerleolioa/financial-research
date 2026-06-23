from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.clock import today_taipei
from ai_stock_sentinel.daily_radar.repository import get_shared_background_context_rows
from ai_stock_sentinel.db.models import SharedBackgroundContext
from ai_stock_sentinel.portfolio.repository import (
    latest_final_raw_data_by_symbol,
    list_active_portfolios,
    list_lifecycle_plans_for_groups,
)
from ai_stock_sentinel.portfolio.risk_summary import build_portfolio_risk_summary
from ai_stock_sentinel.phase1_avwap.projection import read_phase1_position_states_for_portfolio


_WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE = "weekly_major_holders"
_PORTFOLIO_DIAGNOSIS_CONSUMER = "portfolio_diagnosis"
_WEEKLY_HOLDER_RATIO_KEYS = (
    "thousand_lot_holder_ratio",
    "large_holder_400_lot_plus_ratio",
    "retail_100_lot_or_less_ratio",
)


def build_user_portfolio_risk_summary(
    db: Session,
    *,
    user_id: int,
    symbol_name_resolver: Callable[[str], str | None],
    as_of_date: date | None = None,
) -> dict:
    rows = list_active_portfolios(db, user_id=user_id)
    group_ids = [row.position_group_id for row in rows]
    plans = list_lifecycle_plans_for_groups(db, user_id=user_id, position_group_ids=group_ids)
    plans_by_group = {plan.position_group_id: plan for plan in plans}

    symbols = sorted({row.symbol for row in rows})
    raw_data_by_symbol = latest_final_raw_data_by_symbol(db, symbols=symbols)
    summary_date = as_of_date or today_taipei()
    phase1_position_states_by_symbol = read_phase1_position_states_for_portfolio(
        db,
        positions=rows,
        data_date=summary_date,
    )
    weekly_major_holders_by_symbol = _weekly_major_holders_by_symbol(
        db,
        symbols=symbols,
        reference_date=summary_date,
    )
    return build_portfolio_risk_summary(
        rows,
        plans_by_group=plans_by_group,
        raw_data_by_symbol=raw_data_by_symbol,
        symbol_names_by_symbol={symbol: symbol_name_resolver(symbol) for symbol in symbols},
        phase1_position_states_by_symbol=phase1_position_states_by_symbol,
        weekly_major_holders_by_symbol=weekly_major_holders_by_symbol,
        as_of_date=summary_date,
    )


def _weekly_major_holders_by_symbol(
    db: Session,
    *,
    symbols: list[str],
    reference_date: date,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}

    current_rows = get_shared_background_context_rows(
        db,
        symbols=symbols,
        context_types=[_WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE],
        consumer=_PORTFOLIO_DIAGNOSIS_CONSUMER,
        reference_date=reference_date,
        point_in_time=True,
    )
    if not current_rows:
        return {}

    historical_rows = db.scalars(
        select(SharedBackgroundContext).where(
            SharedBackgroundContext.symbol.in_(symbols),
            SharedBackgroundContext.context_type == _WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE,
        )
    ).all()

    projection_by_symbol: dict[str, dict[str, Any]] = {}
    for current in current_rows:
        projection = _weekly_major_holders_projection(
            current,
            previous=_previous_weekly_major_holders_row(current, historical_rows),
        )
        if projection is not None:
            projection_by_symbol[current.symbol] = projection
    return projection_by_symbol


def _weekly_major_holders_projection(
    current: SharedBackgroundContext,
    *,
    previous: SharedBackgroundContext | None,
) -> dict[str, Any] | None:
    if current.freshness == "missing":
        return None
    payload = _mapping(current.payload)
    if not any(_float_value(payload.get(key)) is not None for key in _WEEKLY_HOLDER_RATIO_KEYS):
        return None

    previous_payload = _mapping(previous.payload) if previous is not None else {}
    projection: dict[str, Any] = {
        "status": current.freshness,
        "as_of_date": current.as_of_date.isoformat() if current.as_of_date is not None else None,
        "previous_as_of_date": previous.as_of_date.isoformat()
        if previous is not None and previous.as_of_date is not None
        else None,
    }
    for key in _WEEKLY_HOLDER_RATIO_KEYS:
        current_value = _float_value(payload.get(key))
        previous_value = _float_value(previous_payload.get(key))
        projection[key] = current_value
        projection[f"{key}_delta_pp"] = (
            round(current_value - previous_value, 4)
            if current_value is not None and previous_value is not None
            else None
        )
    return projection


def _previous_weekly_major_holders_row(
    current: SharedBackgroundContext,
    rows: list[SharedBackgroundContext],
) -> SharedBackgroundContext | None:
    if current.as_of_date is None:
        return None
    previous_rows = [
        row
        for row in rows
        if row.id != current.id
        and row.symbol == current.symbol
        and row.context_type == _WEEKLY_MAJOR_HOLDERS_CONTEXT_TYPE
        and row.freshness != "missing"
        and _context_applies_to_portfolio_diagnosis(row)
        and row.as_of_date is not None
        and row.as_of_date < current.as_of_date
        and _has_weekly_holder_ratio(row.payload)
    ]
    if not previous_rows:
        return None
    return max(previous_rows, key=_shared_context_sort_key)


def _context_applies_to_portfolio_diagnosis(row: SharedBackgroundContext) -> bool:
    consumers = row.applicable_consumers or []
    return "*" in consumers or _PORTFOLIO_DIAGNOSIS_CONSUMER in consumers


def _has_weekly_holder_ratio(payload: Any) -> bool:
    mapping = _mapping(payload)
    return any(_float_value(mapping.get(key)) is not None for key in _WEEKLY_HOLDER_RATIO_KEYS)


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
