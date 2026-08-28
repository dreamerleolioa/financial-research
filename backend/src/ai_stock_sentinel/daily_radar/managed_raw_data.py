from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import StockAnalysisCache, UserPortfolio
from ai_stock_sentinel.taiwan_symbols import (
    is_supported_taiwan_symbol,
    normalize_taiwan_symbol,
)


MANAGED_RAW_DATA_SYMBOL_LIMIT = 250
MANAGED_RAW_DATA_ANALYSIS_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ManagedRawDataSelection:
    symbols: tuple[str, ...]
    active_symbols: tuple[str, ...]
    recent_analysis_symbols: tuple[str, ...]
    active_symbol_count: int
    recent_analysis_symbol_count: int
    overlap_symbol_count: int
    deferred_recent_symbol_count: int
    active_symbols_over_budget: bool


def select_managed_raw_data_symbols(
    session: Session,
    *,
    run_date: date,
    max_symbols: int = MANAGED_RAW_DATA_SYMBOL_LIMIT,
) -> ManagedRawDataSelection:
    """Select non-radar symbols that still need daily final raw-data coverage.

    Active positions have priority over recently analyzed symbols. All source
    rows are bounded by ``run_date`` so a historical maintenance run cannot use
    portfolio entries or analysis cache rows from the future.
    """

    if max_symbols < 1:
        raise ValueError("max_symbols must be positive")

    active_rows = session.scalars(
        select(UserPortfolio.symbol)
        .where(
            UserPortfolio.is_active.is_(True),
            UserPortfolio.entry_date <= run_date,
        )
        .order_by(UserPortfolio.symbol.asc())
    ).all()
    active_symbols = _ordered_supported_symbols(active_rows)

    recent_start_date = run_date - timedelta(
        days=MANAGED_RAW_DATA_ANALYSIS_LOOKBACK_DAYS
    )
    latest_analysis_date = func.max(StockAnalysisCache.record_date).label(
        "latest_analysis_date"
    )
    recent_rows = session.execute(
        select(StockAnalysisCache.symbol, latest_analysis_date)
        .where(
            StockAnalysisCache.record_date >= recent_start_date,
            StockAnalysisCache.record_date <= run_date,
        )
        .group_by(StockAnalysisCache.symbol)
        .order_by(latest_analysis_date.desc(), StockAnalysisCache.symbol.asc())
    ).all()
    recent_analysis_symbols = _ordered_supported_symbols(
        row.symbol for row in recent_rows
    )

    active_symbol_set = set(active_symbols)
    overlap_symbol_count = sum(
        symbol in active_symbol_set for symbol in recent_analysis_symbols
    )
    recent_only_symbols = [
        symbol
        for symbol in recent_analysis_symbols
        if symbol not in active_symbol_set
    ]

    active_symbols_over_budget = len(active_symbols) > max_symbols
    if active_symbols_over_budget:
        selected_symbols: list[str] = []
        deferred_recent_symbol_count = len(recent_only_symbols)
    else:
        recent_capacity = max_symbols - len(active_symbols)
        selected_recent_symbols = recent_only_symbols[:recent_capacity]
        selected_symbols = [*active_symbols, *selected_recent_symbols]
        deferred_recent_symbol_count = len(recent_only_symbols) - len(
            selected_recent_symbols
        )

    return ManagedRawDataSelection(
        symbols=tuple(selected_symbols),
        active_symbols=tuple(active_symbols),
        recent_analysis_symbols=tuple(recent_analysis_symbols),
        active_symbol_count=len(active_symbols),
        recent_analysis_symbol_count=len(recent_analysis_symbols),
        overlap_symbol_count=overlap_symbol_count,
        deferred_recent_symbol_count=deferred_recent_symbol_count,
        active_symbols_over_budget=active_symbols_over_budget,
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
