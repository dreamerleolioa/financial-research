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


def read_phase1_current_day_observations_for_managed_universe(
    session: Session,
    *,
    user_id: int,
    data_date: date,
    market: str = "TW",
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
) -> dict[str, dict[str, Any]]:
    universe = resolve_phase1_managed_universe(session, user_id=user_id, market=market)
    non_holding_items = [
        item
        for item in universe
        if "active_holding" not in item.sources
        and any(source in item.sources for source in ("watchlist", "daily_radar_candidate"))
    ]
    symbols = [item.symbol for item in non_holding_items]
    try:
        snapshots = get_phase1_avwap_snapshots(
            session,
            symbols=symbols,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
        )
    except Exception as exc:
        logger.warning(
            "phase1_current_day_observation_read_failed",
            extra={
                "user_id": user_id,
                "data_date": data_date.isoformat(),
                "error_type": exc.__class__.__name__,
            },
        )
        return {
            item.symbol: _missing_current_day_observation(
                symbol=item.symbol,
                sources=item.sources,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                missing_reason="phase1_snapshot_read_failed",
            )
            for item in non_holding_items
        }

    return {
        item.symbol: _current_day_observation_from_snapshot(
            symbol=item.symbol,
            sources=item.sources,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            snapshot=snapshots.get(item.symbol),
        )
        for item in non_holding_items
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


def _current_day_observation_from_snapshot(
    *,
    symbol: str,
    sources: list[str],
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    snapshot: Any | None,
) -> dict[str, Any]:
    if snapshot is None:
        return _missing_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_missing",
        )

    payload = dict(snapshot.payload or {})
    freshness = snapshot.freshness
    missing_reason = snapshot.missing_reason or payload.get("missing_reason")
    anchors = dict(payload.get("anchors") or {})
    ohlcv = dict(payload.get("ohlcv") or {})
    close = _number_or_none(ohlcv.get("close"))
    swing_anchor = _select_anchor(anchors, "swing_low_60d")
    breakout_anchor = _select_anchor(anchors, "breakout_20d")

    if freshness == "missing":
        observation = _missing_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason=missing_reason or "phase1_snapshot_missing",
        )
    elif close is None:
        observation = _missing_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason=missing_reason or "phase1_close_missing",
        )
    else:
        observation = _classify_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            freshness=freshness,
            missing_reason=missing_reason,
            close=close,
            swing_anchor=swing_anchor,
            breakout_anchor=breakout_anchor,
            snapshot=payload,
        )

    observation["source"] = {
        **dict(observation.get("source") or {}),
        "provider": snapshot.source_provider,
        "dataset": snapshot.dataset,
        "adjustment_mode": snapshot.adjustment_mode,
    }
    observation["source_granularity"] = snapshot.source_granularity
    return observation


def _classify_current_day_observation(
    *,
    symbol: str,
    sources: list[str],
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    freshness: str,
    missing_reason: str | None,
    close: float,
    swing_anchor: dict[str, Any] | None,
    breakout_anchor: dict[str, Any] | None,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    swing_distance = _anchor_distance(swing_anchor)
    breakout_distance = _anchor_distance(breakout_anchor)
    display_anchor: dict[str, Any] | None = None

    if swing_distance is not None and swing_distance >= 10:
        state = "overheated"
        label = None
        matched_rules = ["phase1_swing_low_extended_10pct"]
        display_anchor = swing_anchor
    elif breakout_distance is not None and 0 <= breakout_distance <= 5:
        state = "strong_breakout"
        label = "建倉"
        matched_rules = ["phase1_breakout_anchor_supported_within_5pct"]
        display_anchor = breakout_anchor
    elif swing_distance is not None and 0 <= swing_distance <= 5:
        state = "pullback_watch"
        label = "建倉"
        matched_rules = ["phase1_swing_low_anchor_supported_within_5pct"]
        display_anchor = swing_anchor
    else:
        state = "range_watch"
        label = None
        matched_rules = ["phase1_no_current_day_list_match"]
        display_anchor = breakout_anchor or swing_anchor

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
        "sources": list(sources),
        "close": close,
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


def _missing_current_day_observation(
    *,
    symbol: str,
    sources: list[str],
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
        "sources": list(sources),
        "close": None,
        "display_anchor": None,
        "matched_rules": ["phase1_current_day_observation_unavailable"],
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
    payload.setdefault("symbol", symbol)
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


def _select_anchor(anchors: dict[str, Any], anchor_type: str) -> dict[str, Any] | None:
    anchor = anchors.get(anchor_type)
    if not isinstance(anchor, dict) or anchor.get("available") is not True:
        return None
    return {
        "type": anchor_type,
        "anchor_date": anchor.get("anchor_date"),
        "anchor_reason": anchor.get("anchor_reason"),
        "avwap": anchor.get("avwap"),
        "distance_to_avwap_pct": anchor.get("distance_to_avwap_pct"),
        "source_granularity": anchor.get("source_granularity", "daily"),
        "estimated": bool(anchor.get("estimated", False)),
    }


def _anchor_distance(anchor: dict[str, Any] | None) -> float | None:
    if anchor is None:
        return None
    return _number_or_none(anchor.get("distance_to_avwap_pct"))


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
    "read_phase1_current_day_observations_for_managed_universe",
    "read_phase1_observation_for_analyze",
    "read_phase1_position_states_for_portfolio",
]
