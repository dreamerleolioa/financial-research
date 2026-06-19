from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.phase1_avwap.provider import DEFAULT_ADJUSTMENT_MODE, DEFAULT_PHASE1_DATASET
from ai_stock_sentinel.phase1_avwap.repository import get_phase1_avwap_snapshots
from ai_stock_sentinel.phase1_avwap.universe import resolve_phase1_managed_universe


logger = logging.getLogger(__name__)


def read_phase1_observation_for_analyze(
    session: Session,
    *,
    user_id: int,
    symbol: str,
    data_date: date,
    market: str = "TW",
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
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

        snapshot = get_phase1_avwap_snapshots(
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
    payload.setdefault("symbol", normalized_symbol)
    payload.setdefault("data_date", data_date.isoformat())
    payload.setdefault("dataset", dataset)
    payload.setdefault("adjustment_mode", adjustment_mode)
    payload["freshness"] = snapshot.freshness
    payload["missing_reason"] = snapshot.missing_reason
    payload["source"] = {
        **dict(payload.get("source") or {}),
        "provider": snapshot.source_provider,
        "dataset": snapshot.dataset,
        "adjustment_mode": snapshot.adjustment_mode,
    }
    payload["source_granularity"] = snapshot.source_granularity
    return payload


def read_phase1_position_states_for_portfolio(
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
            "phase1_position_state_read_failed",
            extra={
                "symbols": normalized_symbols,
                "data_date": data_date.isoformat(),
                "error_type": exc.__class__.__name__,
            },
        )
        return {
            symbol: _missing_position_state(
                symbol=symbol,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                missing_reason="phase1_snapshot_read_failed",
            )
            for symbol in normalized_symbols
        }

    return {
        symbol: _position_state_from_snapshot(
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


def _position_state_from_snapshot(
    *,
    symbol: str,
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    snapshot: Any | None,
) -> dict[str, Any]:
    if snapshot is None:
        return _missing_position_state(
            symbol=symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_missing",
        )

    payload = dict(snapshot.payload or {})
    freshness = snapshot.freshness
    missing_reason = snapshot.missing_reason or payload.get("missing_reason")
    anchors = dict(payload.get("anchors") or {})
    display_anchor = _select_position_display_anchor(anchors)

    if freshness == "missing" or display_anchor is None:
        reason = missing_reason or "phase1_position_anchor_missing"
        state = _missing_position_state(
            symbol=symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason=reason,
        )
    else:
        state = _classify_position_state(
            symbol=symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            freshness=freshness,
            missing_reason=missing_reason,
            display_anchor=display_anchor,
            snapshot=payload,
        )

    state["source"] = {
        **dict(state.get("source") or {}),
        "provider": snapshot.source_provider,
        "dataset": snapshot.dataset,
        "adjustment_mode": snapshot.adjustment_mode,
    }
    state["source_granularity"] = snapshot.source_granularity
    return state


def _classify_position_state(
    *,
    symbol: str,
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    freshness: str,
    missing_reason: str | None,
    display_anchor: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    distance = display_anchor.get("distance_to_avwap_pct")
    if not isinstance(distance, int | float):
        state = "data_unavailable"
        label = "資料不足"
        matched_rules = ["phase1_distance_missing"]
        missing_reason = missing_reason or "phase1_distance_to_avwap_missing"
    elif distance <= -2:
        state = "exit_risk"
        label = "停損警戒"
        matched_rules = ["phase1_display_anchor_lost_by_2pct"]
    elif distance < 0:
        state = "warning"
        label = "停損警戒"
        matched_rules = ["phase1_display_anchor_lost"]
    else:
        state = "hold"
        label = "續抱"
        matched_rules = ["phase1_display_anchor_supported"]

    data_quality = dict(snapshot.get("data_quality") or {})
    data_quality.setdefault("estimated", False)
    data_quality.setdefault("source_granularity", "daily")
    data_quality["missing_reason"] = missing_reason
    data_quality["blocking"] = False

    return {
        "symbol": symbol,
        "data_date": data_date.isoformat(),
        "dataset": dataset,
        "adjustment_mode": adjustment_mode,
        "state": state,
        "label": label,
        "freshness": freshness,
        "missing_reason": missing_reason,
        "display_anchor": display_anchor,
        "matched_rules": matched_rules,
        "source": {
            "provider": "phase1_avwap_snapshot",
            "dataset": dataset,
            "adjustment_mode": adjustment_mode,
        },
        "source_granularity": "daily",
        "data_quality": data_quality,
    }


def _missing_position_state(
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
        "state": "data_unavailable",
        "label": "資料不足",
        "freshness": "missing",
        "missing_reason": missing_reason,
        "display_anchor": None,
        "matched_rules": ["phase1_position_state_unavailable"],
        "source": {
            "provider": "phase1_avwap_snapshot",
            "dataset": dataset,
            "adjustment_mode": adjustment_mode,
        },
        "source_granularity": "daily",
        "data_quality": {
            "estimated": False,
            "source_granularity": "daily",
            "rows_used": 0,
            "missing_reason": missing_reason,
            "blocking": False,
        },
    }


def _select_position_display_anchor(anchors: dict[str, Any]) -> dict[str, Any] | None:
    for anchor_type in ("entry", "breakout_20d", "swing_low_60d"):
        anchor = anchors.get(anchor_type)
        if isinstance(anchor, dict) and anchor.get("available") is True:
            return {
                "type": anchor_type,
                "anchor_date": anchor.get("anchor_date"),
                "anchor_reason": anchor.get("anchor_reason"),
                "avwap": anchor.get("avwap"),
                "distance_to_avwap_pct": anchor.get("distance_to_avwap_pct"),
                "source_granularity": anchor.get("source_granularity", "daily"),
                "estimated": bool(anchor.get("estimated", False)),
            }
    return None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


__all__ = [
    "read_phase1_observation_for_analyze",
    "read_phase1_position_states_for_portfolio",
]
