from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import StockAnalysisCache
from ai_stock_sentinel.taiwan_symbols import (
    is_supported_taiwan_symbol,
    normalize_taiwan_symbol,
)


MANAGED_RAW_DATA_SYMBOL_LIMIT = 250
MANAGED_RAW_DATA_ANALYSIS_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ManagedRawDataSelection:
    symbols: tuple[str, ...]
    recent_analysis_symbols: tuple[str, ...]
    recent_analysis_symbol_count: int
    deferred_recent_symbol_count: int


def select_managed_raw_data_symbols(
    session: Session,
    *,
    run_date: date,
    max_symbols: int = MANAGED_RAW_DATA_SYMBOL_LIMIT,
) -> ManagedRawDataSelection:
    """Select non-radar symbols that still need daily final raw-data coverage.

    Only recent general research participates. Retained position caches do not
    schedule work, and historical maintenance cannot read future analysis rows.
    """

    if max_symbols < 1:
        raise ValueError("max_symbols must be positive")

    recent_start_date = run_date - timedelta(
        days=MANAGED_RAW_DATA_ANALYSIS_LOOKBACK_DAYS
    )
    latest_analysis_date = func.max(StockAnalysisCache.record_date).label(
        "latest_analysis_date"
    )
    recent_rows = session.execute(
        select(StockAnalysisCache.symbol, latest_analysis_date)
        .where(
            StockAnalysisCache.analysis_type == "general",
            StockAnalysisCache.record_date >= recent_start_date,
            StockAnalysisCache.record_date <= run_date,
        )
        .group_by(StockAnalysisCache.symbol)
        .order_by(latest_analysis_date.desc(), StockAnalysisCache.symbol.asc())
    ).all()
    recent_analysis_symbols = _ordered_supported_symbols(
        row.symbol for row in recent_rows
    )

    selected_symbols = recent_analysis_symbols[:max_symbols]
    deferred_recent_symbol_count = len(recent_analysis_symbols) - len(selected_symbols)

    return ManagedRawDataSelection(
        symbols=tuple(selected_symbols),
        recent_analysis_symbols=tuple(recent_analysis_symbols),
        recent_analysis_symbol_count=len(recent_analysis_symbols),
        deferred_recent_symbol_count=deferred_recent_symbol_count,
    )


def _ordered_supported_symbols(symbols: Iterable[object]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in symbols:
        symbol = normalize_taiwan_symbol(str(value))
        if symbol in seen or not is_supported_taiwan_symbol(symbol):
            continue
        seen.add(symbol)
        ordered.append(symbol)
    return ordered


__all__ = [
    "MANAGED_RAW_DATA_ANALYSIS_LOOKBACK_DAYS",
    "MANAGED_RAW_DATA_SYMBOL_LIMIT",
    "ManagedRawDataSelection",
    "select_managed_raw_data_symbols",
]
