from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import Phase1AvwapSnapshot
from ai_stock_sentinel.phase1_avwap.provider import DEFAULT_ADJUSTMENT_MODE, DEFAULT_PHASE1_DATASET


def get_phase1_avwap_snapshots(
    session: Session,
    *,
    symbols: Iterable[str],
    data_date: date,
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
) -> dict[str, Phase1AvwapSnapshot]:
    ordered_symbols = _ordered_unique_symbols(symbols)
    if not ordered_symbols:
        return {}
    rows = session.scalars(
        select(Phase1AvwapSnapshot).where(
            Phase1AvwapSnapshot.symbol.in_(ordered_symbols),
            Phase1AvwapSnapshot.data_date == data_date,
            Phase1AvwapSnapshot.dataset == dataset,
            Phase1AvwapSnapshot.adjustment_mode == adjustment_mode,
        )
    ).all()
    return {row.symbol: row for row in rows}


def upsert_phase1_avwap_snapshot(
    session: Session,
    *,
    symbol: str,
    data_date: date,
    payload: Mapping[str, Any],
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
    source_provider: str = "twse",
    source_granularity: str = "daily",
    is_final: bool = True,
    freshness: str = "fresh",
    missing_reason: str | None = None,
) -> Phase1AvwapSnapshot:
    normalized_symbol = _normalize_symbol(symbol)
    row = session.execute(
        select(Phase1AvwapSnapshot).where(
            Phase1AvwapSnapshot.symbol == normalized_symbol,
            Phase1AvwapSnapshot.data_date == data_date,
            Phase1AvwapSnapshot.dataset == dataset,
            Phase1AvwapSnapshot.adjustment_mode == adjustment_mode,
        )
    ).scalar_one_or_none()
    if row is None:
        row = Phase1AvwapSnapshot(
            symbol=normalized_symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
        )
    row.source_provider = source_provider
    row.source_granularity = source_granularity
    row.is_final = is_final
    row.freshness = freshness
    row.missing_reason = missing_reason
    row.payload = dict(payload)
    session.add(row)
    session.flush()
    return row


def _ordered_unique_symbols(symbols: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


__all__ = [
    "get_phase1_avwap_snapshots",
    "upsert_phase1_avwap_snapshot",
]
