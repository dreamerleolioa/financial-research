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


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


__all__ = ["read_phase1_observation_for_analyze"]
