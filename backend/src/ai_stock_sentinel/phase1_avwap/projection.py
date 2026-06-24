from __future__ import annotations

import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _PositionStateRequest:
    key: str
    symbol: str
    holding_entry_date: date | None
    holding_avg_cost: float | None


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


def read_phase1_position_states_for_portfolio(
    session: Session,
    *,
    symbols: list[str] | None = None,
    positions: list[Any] | None = None,
    data_date: date,
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
    max_snapshot_age_days: int | None = DEFAULT_PHASE1_SNAPSHOT_MAX_AGE_DAYS,
) -> dict[str, dict[str, Any]]:
    requests = _position_state_requests(symbols=symbols, positions=positions)
    normalized_symbols = [symbol for symbol in dict.fromkeys(request.symbol for request in requests) if symbol]
    try:
        snapshots = get_latest_phase1_avwap_snapshots_on_or_before(
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
            request.key: _missing_position_state(
                symbol=request.symbol,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                missing_reason="phase1_snapshot_read_failed",
            )
            for request in requests
        }

    return {
        request.key: _position_state_from_snapshot(
            symbol=request.symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            snapshot=snapshots.get(request.symbol),
            holding_entry_date=request.holding_entry_date,
            holding_avg_cost=request.holding_avg_cost,
            max_snapshot_age_days=max_snapshot_age_days,
        )
        for request in requests
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
    max_snapshot_age_days: int | None = DEFAULT_PHASE1_SNAPSHOT_MAX_AGE_DAYS,
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
        snapshots = get_latest_phase1_avwap_snapshots_on_or_before(
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
            max_snapshot_age_days=max_snapshot_age_days,
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
    max_snapshot_age_days: int | None,
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
    snapshot_date = snapshot.data_date or data_date
    freshness = snapshot.freshness
    missing_reason = snapshot.missing_reason or payload.get("missing_reason")
    anchors = _shared_snapshot_anchors(payload)
    ohlcv = dict(payload.get("ohlcv") or {})
    close = _number_or_none(ohlcv.get("close"))
    swing_anchor = _select_anchor(anchors, "swing_low_60d")
    breakout_anchor = _select_anchor(anchors, "breakout_20d")

    if _snapshot_is_stale(snapshot_date=snapshot_date, requested_date=data_date, max_age_days=max_snapshot_age_days):
        observation = _missing_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_stale",
        )
    elif freshness == "missing":
        observation = _missing_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason=missing_reason or "phase1_snapshot_missing",
        )
    elif close is None:
        observation = _missing_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason=missing_reason or "phase1_close_missing",
        )
    else:
        observation = _classify_current_day_observation(
            symbol=symbol,
            sources=sources,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            freshness=freshness,
            missing_reason=missing_reason,
            close=close,
            swing_anchor=swing_anchor,
            breakout_anchor=breakout_anchor,
            snapshot=payload,
        )

    observation["requested_data_date"] = data_date.isoformat()
    observation["source"] = _snapshot_source(payload, snapshot)
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


def _position_state_from_snapshot(
    *,
    symbol: str,
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    snapshot: Any | None,
    holding_entry_date: date | None = None,
    holding_avg_cost: float | None = None,
    max_snapshot_age_days: int | None = DEFAULT_PHASE1_SNAPSHOT_MAX_AGE_DAYS,
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
    snapshot_date = snapshot.data_date or data_date
    freshness = snapshot.freshness
    missing_reason = snapshot.missing_reason or payload.get("missing_reason")
    anchors = _shared_snapshot_anchors(payload)
    entry_anchor = _entry_anchor_from_snapshot_payload(
        payload,
        holding_entry_date=holding_entry_date,
    )
    if entry_anchor is not None:
        anchors["entry"] = entry_anchor
    display_anchor = _select_position_display_anchor(anchors)

    if _snapshot_is_stale(snapshot_date=snapshot_date, requested_date=data_date, max_age_days=max_snapshot_age_days):
        state = _missing_position_state(
            symbol=symbol,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason="phase1_snapshot_stale",
        )
    elif freshness == "missing" or display_anchor is None:
        reason = missing_reason or _position_anchor_missing_reason(payload, holding_entry_date=holding_entry_date)
        state = _missing_position_state(
            symbol=symbol,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            missing_reason=reason,
        )
    else:
        state = _classify_position_state(
            symbol=symbol,
            data_date=snapshot_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            freshness=freshness,
            missing_reason=missing_reason,
            display_anchor=display_anchor,
            snapshot=payload,
            holding_avg_cost=holding_avg_cost,
        )

    state["requested_data_date"] = data_date.isoformat()
    state["source"] = _snapshot_source(payload, snapshot)
    state["source_granularity"] = snapshot.source_granularity
    return state


def _snapshot_is_stale(*, snapshot_date: date, requested_date: date, max_age_days: int | None) -> bool:
    return max_age_days is not None and (requested_date - snapshot_date).days > max_age_days


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
    holding_avg_cost: float | None,
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
        "holding_avg_cost": holding_avg_cost,
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


def _shared_snapshot_anchors(payload: dict[str, Any]) -> dict[str, Any]:
    anchors = dict(payload.get("anchors") or {})
    anchors.pop("entry", None)
    return anchors


def _position_state_requests(
    *,
    symbols: list[str] | None,
    positions: list[Any] | None,
) -> list[_PositionStateRequest]:
    if positions is not None:
        requests: list[_PositionStateRequest] = []
        for position in positions:
            symbol = _normalize_symbol(str(getattr(position, "symbol", "")))
            if not symbol:
                continue
            key = str(getattr(position, "position_group_id", "") or symbol)
            entry_date = getattr(position, "entry_date", None)
            if hasattr(entry_date, "date"):
                entry_date = entry_date.date()
            requests.append(
                _PositionStateRequest(
                    key=key,
                    symbol=symbol,
                    holding_entry_date=entry_date if isinstance(entry_date, date) else None,
                    holding_avg_cost=_number_or_none(getattr(position, "entry_price", None)),
                )
            )
        return requests

    normalized_symbols = [_normalize_symbol(symbol) for symbol in (symbols or [])]
    return [
        _PositionStateRequest(
            key=symbol,
            symbol=symbol,
            holding_entry_date=None,
            holding_avg_cost=None,
        )
        for symbol in dict.fromkeys(normalized_symbols)
        if symbol
    ]


def _position_anchor_missing_reason(payload: dict[str, Any], *, holding_entry_date: date | None) -> str:
    if holding_entry_date is None:
        return "holding_entry_date_missing"
    bars = _snapshot_bars(payload)
    if not bars:
        return "phase1_snapshot_bars_missing"
    if bars[0]["date"] > holding_entry_date:
        return "holding_entry_date_before_snapshot_history"
    return "phase1_position_anchor_missing"


def _entry_anchor_from_snapshot_payload(
    payload: dict[str, Any],
    *,
    holding_entry_date: date | None,
) -> dict[str, Any] | None:
    if holding_entry_date is None:
        return None
    bars = _snapshot_bars(payload)
    if not bars or bars[0]["date"] > holding_entry_date:
        return None
    anchor_index = next((index for index, bar in enumerate(bars) if bar["date"] >= holding_entry_date), None)
    if anchor_index is None:
        return {
            "available": False,
            "anchor_date": holding_entry_date.isoformat(),
            "missing_reason": "no_price_row_on_or_after_holding_entry_date",
        }
    anchor = bars[anchor_index]
    anchor_bars = bars[anchor_index:]
    volume = sum(bar["volume"] for bar in anchor_bars)
    if volume <= 0:
        return None
    avwap = round(sum(bar["amount"] for bar in anchor_bars) / volume, 4)
    current_close = _number_or_none(dict(payload.get("ohlcv") or {}).get("close"))
    if current_close is None and bars:
        current_close = bars[-1]["close"]
    return {
        "available": True,
        "anchor_date": anchor["date"].isoformat(),
        "anchor_reason": "holding_entry_date",
        "avwap": avwap,
        "snapshot_close": current_close,
        "distance_to_avwap_pct": _pct_distance(current_close, avwap),
        "distance_basis": "snapshot_close",
        "source_granularity": "daily",
        "estimated": any(bar["estimated_amount"] for bar in anchor_bars),
    }


def _snapshot_bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in payload.get("bars") or []:
        if not isinstance(row, dict):
            continue
        trade_date = _date_or_none(row.get("date") or row.get("trade_date"))
        volume = _number_or_none(row.get("volume"))
        amount = _number_or_none(row.get("amount"))
        close = _number_or_none(row.get("close"))
        if trade_date is None or volume is None or amount is None or close is None:
            continue
        parsed.append({
            "date": trade_date,
            "volume": volume,
            "amount": amount,
            "close": close,
            "estimated_amount": bool(row.get("estimated_amount", False)),
        })
    return sorted(parsed, key=lambda bar: bar["date"])


def _date_or_none(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _pct_distance(price: float | None, reference: float) -> float | None:
    if price is None or reference == 0:
        return None
    return round((price - reference) / reference * 100, 4)


def _select_anchor(anchors: dict[str, Any], anchor_type: str) -> dict[str, Any] | None:
    anchor = anchors.get(anchor_type)
    if not isinstance(anchor, dict) or anchor.get("available") is not True:
        return None
    return {
        "type": anchor_type,
        "anchor_date": anchor.get("anchor_date"),
        "anchor_reason": anchor.get("anchor_reason"),
        "avwap": anchor.get("avwap"),
        "snapshot_close": anchor.get("snapshot_close"),
        "distance_to_avwap_pct": anchor.get("distance_to_avwap_pct"),
        "distance_basis": anchor.get("distance_basis"),
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
