from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Protocol

import yfinance as yf


BENCHMARK_YFINANCE_SYMBOLS = {
    "TAIEX": "^TWII",
    "SPX": "^GSPC",
}


class ForwardPriceProvider(Protocol):
    def fetch(
        self,
        symbols: Sequence[str],
        *,
        start_date: date,
        end_date: date,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]: ...


class YFinanceForwardPriceProvider:
    auto_adjust = True
    actions = False

    def fetch(
        self,
        symbols: Sequence[str],
        *,
        start_date: date,
        end_date: date,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        ordered_symbols = sorted({str(symbol) for symbol in symbols if str(symbol)})
        if not ordered_symbols:
            return {}
        provider_symbols = {
            symbol: BENCHMARK_YFINANCE_SYMBOLS.get(symbol, symbol)
            for symbol in ordered_symbols
        }
        history = yf.download(
            sorted(set(provider_symbols.values())),
            group_by="ticker",
            start=start_date,
            end=end_date + timedelta(days=1),
            interval="1d",
            threads=True,
            progress=False,
            auto_adjust=self.auto_adjust,
            actions=self.actions,
        )
        if history is None or getattr(history, "empty", False):
            raise RuntimeError("forward_price_provider_returned_no_data")
        return {
            symbol: rows
            for symbol, provider_symbol in provider_symbols.items()
            if (rows := _price_rows(
                _symbol_frame(history, provider_symbol),
                research=not self.auto_adjust,
                benchmark=symbol in BENCHMARK_YFINANCE_SYMBOLS,
            ))
        }


class YFinanceResearchPriceProvider(YFinanceForwardPriceProvider):
    """Separate price-only data; never merge with adjusted technical snapshots."""
    auto_adjust = False
    actions = True


def get_research_price_provider() -> ForwardPriceProvider:
    return YFinanceResearchPriceProvider()


def get_forward_price_provider() -> ForwardPriceProvider:
    return YFinanceForwardPriceProvider()


def _symbol_frame(history: Any, symbol: str) -> Any:
    columns = getattr(history, "columns", None)
    if columns is None:
        return history
    if getattr(columns, "nlevels", 1) < 2:
        return history
    level_zero = set(str(value) for value in columns.get_level_values(0))
    if symbol in level_zero:
        return history[symbol]
    level_one = set(str(value) for value in columns.get_level_values(1))
    if symbol in level_one:
        return history.xs(symbol, axis=1, level=1)
    return history.iloc[0:0] if hasattr(history, "iloc") else history


def _price_rows(frame: Any, *, research: bool = False, benchmark: bool = False) -> list[dict[str, Any]]:
    index = getattr(frame, "index", None)
    if index is None:
        return []
    columns = {
        field: _matching_column(frame, field)
        for field in ("Open", "High", "Low", "Close")
    }
    if columns["Close"] is None:
        return []
    dividend_column = _matching_column(frame, "Dividends")
    split_column = _matching_column(frame, "Stock Splits")
    rows: list[dict[str, Any]] = []
    for position, index_value in enumerate(index):
        close = _frame_number(frame, columns["Close"], position)
        row_date = _index_date(index_value)
        if close is None or close <= 0 or row_date is None:
            continue
        open_price = _frame_number(frame, columns["Open"], position)
        row = {
            "date": row_date.isoformat(),
            "open": (open_price if open_price is not None and open_price > 0 else None)
                    if research else (open_price if open_price is not None and open_price > 0 else close),
            "high": _frame_number(frame, columns["High"], position),
            "low": _frame_number(frame, columns["Low"], position),
            "close": close,
        }
        if research:
            dividend = _frame_number(frame, dividend_column, position)
            split = _frame_number(frame, split_column, position)
            row.update({
                "price_basis": "unadjusted_price", "price_source": "yfinance",
                "corporate_actions_checked": benchmark or (dividend is not None and split is not None),
                "dividends": 0 if benchmark else dividend,
                "stock_splits": 0 if benchmark else split,
            })
        rows.append(row)
    return rows


def _matching_column(frame: Any, field: str) -> Any | None:
    for column in getattr(frame, "columns", []):
        if str(column).lower() == field.lower():
            return column
    return None


def _frame_number(frame: Any, column: Any | None, position: int) -> float | None:
    if column is None:
        return None
    series = frame[column]
    value = series.iloc[position] if hasattr(series, "iloc") else series[position]
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _index_date(value: Any) -> date | None:
    if hasattr(value, "date"):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


__all__ = [
    "ForwardPriceProvider",
    "YFinanceForwardPriceProvider",
    "get_forward_price_provider",
]
