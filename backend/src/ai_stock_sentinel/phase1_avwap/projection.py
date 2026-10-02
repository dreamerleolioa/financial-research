from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.phase1_avwap.provider import DEFAULT_ADJUSTMENT_MODE, DEFAULT_PHASE1_DATASET
from ai_stock_sentinel.phase1_avwap.repository import (
    get_latest_phase1_avwap_snapshots_on_or_before,
    get_phase1_avwap_snapshots,
)
from ai_stock_sentinel.phase1_avwap.universe import resolve_phase1_managed_universe


logger = logging.getLogger(__name__)
DEFAULT_PHASE1_SNAPSHOT_MAX_AGE_DAYS = 7


def read_phase1_observation_for_analyze(
    session: Session,
    *,
    user_id: int,
    symbol: str,
    data_date: date,
    current_price: float | None = None,
    market: str = "TW",
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
    max_snapshot_age_days: int | None = DEFAULT_PHASE1_SNAPSHOT_MAX_AGE_DAYS,
) -> dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    try:
        universe = resolve_phase1_managed_universe(session, user_id=user_id, market=market)
        universe_symbols = {item.symbol for item in universe}
        if normalized_symbol not in universe_symbols:
            return _missing_observation(
                symbol=normalized_symbol,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                missing_reason="not_in_phase1_universe",
            )

        snapshot = get_latest_phase1_avwap_snapshots_on_or_before(
            session,
            symbols=[normalized_symbol],
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
        ).get(normalized_symbol)
    except Exception as exc:
        logger.warning(
            "phase1_observation_read_failed",
            extra={
                "symbol": normalized_symbol,
                "user_id": user_id,
                "data_date": data_date.isoformat(),
                "error_type": exc.__class__.__name__,
            },
        )
        return _missing_observation(
            symbol=normalized_symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_read_failed",
        )

    if snapshot is None:
        return _missing_observation(
            symbol=normalized_symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_missing",
        )

    payload = dict(snapshot.payload or {})
    snapshot_date = snapshot.data_date or data_date
    if _snapshot_is_stale(snapshot_date=snapshot_date, requested_date=data_date, max_age_days=max_snapshot_age_days):
        observation = _missing_observation(
            symbol=normalized_symbol,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_stale",
        )
        observation["requested_data_date"] = data_date.isoformat()
        observation["source"] = _snapshot_source(payload, snapshot)
        observation["source_granularity"] = snapshot.source_granularity
        return observation

    _strip_internal_snapshot_fields(payload)
    payload.setdefault("symbol", normalized_symbol)
    payload.setdefault("data_date", snapshot_date.isoformat())
    payload.setdefault("dataset", dataset)
    payload.setdefault("adjustment_mode", adjustment_mode)
    _enrich_analyze_anchor_distances(payload, current_price=current_price)
    payload["freshness"] = snapshot.freshness
    payload["missing_reason"] = snapshot.missing_reason
    payload["source"] = _snapshot_source(payload, snapshot)
    payload["source_granularity"] = snapshot.source_granularity
    payload["requested_data_date"] = data_date.isoformat()
    return payload


def read_phase1_avwap_contexts_for_daily_radar(
    session: Session,
    *,
    symbols: list[str],
    data_date: date,
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
) -> dict[str, dict[str, Any]]:
    normalized_symbols = [_normalize_symbol(symbol) for symbol in symbols]
    normalized_symbols = [symbol for symbol in dict.fromkeys(normalized_symbols) if symbol]
    try:
        snapshots = get_phase1_avwap_snapshots(
            session,
            symbols=normalized_symbols,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
        )
    except Exception as exc:
        logger.warning(
            "phase1_daily_radar_context_read_failed",
            extra={
                "symbols": normalized_symbols,
                "data_date": data_date.isoformat(),
                "error_type": exc.__class__.__name__,
            },
        )
        return {
            symbol: _missing_daily_radar_context(
                symbol=symbol,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                missing_reason="phase1_snapshot_read_failed",
            )
            for symbol in normalized_symbols
        }

    return {
        symbol: _daily_radar_context_from_snapshot(
            symbol=symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            snapshot=snapshots.get(symbol),
        )
        for symbol in normalized_symbols
    }


def _missing_observation(
    *,
    symbol: str,
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    missing_reason: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "data_date": data_date.isoformat(),
        "dataset": dataset,
        "adjustment_mode": adjustment_mode,
        "freshness": "missing",
        "missing_reason": missing_reason,
        "source": {
            "provider": "phase1_avwap_snapshot",
            "dataset": dataset,
            "adjustment_mode": adjustment_mode,
        },
        "source_granularity": "daily",
        "anchors": {},
        "data_quality": {
            "estimated": False,
            "source_granularity": "daily",
            "rows_used": 0,
            "missing_reason": missing_reason,
            "blocking": False,
        },
    }


def _daily_radar_context_from_snapshot(
    *,
    symbol: str,
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    snapshot: Any | None,
) -> dict[str, Any]:
    if snapshot is None:
        return _missing_daily_radar_context(
            symbol=symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_missing",
        )

    payload = dict(snapshot.payload or {})
    _strip_internal_snapshot_fields(payload)
    payload.setdefault("symbol", symbol)
    payload.setdefault("data_date", data_date.isoformat())
    payload.setdefault("dataset", dataset)
    payload.setdefault("adjustment_mode", adjustment_mode)
    payload["freshness"] = snapshot.freshness
    payload["missing_reason"] = snapshot.missing_reason
    payload["source"] = _snapshot_source(payload, snapshot)
    payload["source_granularity"] = snapshot.source_granularity
    payload["applicable_consumers"] = ["daily_radar"]
    data_quality = dict(payload.get("data_quality") or {})
    data_quality.setdefault("estimated", False)
    data_quality.setdefault("source_granularity", snapshot.source_granularity)
    data_quality["blocking"] = False
    payload["data_quality"] = data_quality
    return payload


def _missing_daily_radar_context(
    *,
    symbol: str,
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    missing_reason: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "data_date": data_date.isoformat(),
        "dataset": dataset,
        "adjustment_mode": adjustment_mode,
        "freshness": "missing",
        "missing_reason": missing_reason,
        "source": {
            "provider": "phase1_avwap_snapshot",
            "dataset": dataset,
            "adjustment_mode": adjustment_mode,
        },
        "source_granularity": "daily",
        "anchors": {},
        "applicable_consumers": ["daily_radar"],
        "data_quality": {
            "estimated": False,
            "source_granularity": "daily",
            "rows_used": 0,
            "missing_reason": missing_reason,
            "blocking": False,
        },
    }


def _snapshot_is_stale(*, snapshot_date: date, requested_date: date, max_age_days: int | None) -> bool:
    return max_age_days is not None and (requested_date - snapshot_date).days > max_age_days


def _strip_internal_snapshot_fields(payload: dict[str, Any]) -> None:
    payload.pop("bars", None)
    payload.pop("holding", None)
    anchors = {
        key: dict(anchor) if isinstance(anchor, dict) else anchor
        for key, anchor in dict(payload.get("anchors") or {}).items()
    }
    anchors.pop("entry", None)
    payload["anchors"] = anchors


def _enrich_analyze_anchor_distances(payload: dict[str, Any], *, current_price: float | None) -> None:
    anchors = payload.get("anchors")
    if not isinstance(anchors, dict):
        return
    snapshot_close = _number_or_none(dict(payload.get("ohlcv") or {}).get("close"))
    for anchor in anchors.values():
        if not isinstance(anchor, dict):
            continue
        avwap = _number_or_none(anchor.get("avwap"))
        if avwap is None:
            continue
        if anchor.get("snapshot_close") is None and snapshot_close is not None:
            anchor["snapshot_close"] = snapshot_close
        anchor_snapshot_close = _number_or_none(anchor.get("snapshot_close"))
        if anchor.get("distance_to_avwap_pct") is None:
            snapshot_distance = _pct_distance(anchor_snapshot_close, avwap)
            if snapshot_distance is not None:
                anchor["distance_to_avwap_pct"] = snapshot_distance
        if anchor.get("distance_to_avwap_pct") is not None:
            anchor.setdefault("distance_basis", "snapshot_close")
        current_distance = _pct_distance(current_price, avwap)
        if current_distance is not None:
            anchor["current_price"] = current_price
            anchor["current_distance_to_avwap_pct"] = current_distance
            anchor["current_distance_basis"] = "analyze_current_price"


def _snapshot_source(payload: dict[str, Any], snapshot: Any) -> dict[str, Any]:
    source = dict(payload.get("source") or {})
    return {
        **source,
        "provider": snapshot.source_provider,
        "dataset": source.get("dataset") or snapshot.dataset,
        "adjustment_mode": snapshot.adjustment_mode,
    }


def _pct_distance(price: float | None, reference: float) -> float | None:
    if price is None or reference == 0:
        return None
    return round((price - reference) / reference * 100, 4)


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


__all__ = [
    "read_phase1_avwap_contexts_for_daily_radar",
    "read_phase1_observation_for_analyze",
]
