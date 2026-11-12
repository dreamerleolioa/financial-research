from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Protocol

import yfinance as yf
from fastapi import Depends
from sqlalchemy.orm import Session
from ai_stock_sentinel.db.session import get_db


BENCHMARK_YFINANCE_SYMBOLS = {
    "TAIEX": "^TWII",
    "SPX": "^GSPC",
}


class ForwardPriceUnavailableError(RuntimeError):
    """A requested source returned no rows; this does not prove termination."""

    def __init__(self, symbols: Sequence[str], *, start_date: date, end_date: date):
        super().__init__("forward_price_provider_returned_no_data")
        self.symbols = tuple(sorted(set(symbols)))
        self.start_date = start_date
        self.end_date = end_date


class ForwardPriceProvider(Protocol):
    def fetch(
        self,
        symbols: Sequence[str],
        *,
        start_date: date,
        end_date: date,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]: ...


class _ForwardHistoryData:
    """Check responses on this ticker only, without changing global SDK flags."""

    def __init__(self, data, *, unavailable):
        self.data = data
        self.unavailable = unavailable
        self.failure = None
        self.source_empty = False
        self.timezone_only = False
        self.metadata_empty = False
        self.metadata_failures = []

    def __getattr__(self, name):
        return getattr(self.data, name)

    def _request(self, method, *args, **kwargs):
        self.failure = None
        self.source_empty = False
        try:
            response = getattr(self.data, method)(*args, **kwargs)
            # Yahoo's explicit not-found response may represent unavailable
            # history. Authentication, rate-limit and server errors must fail.
            if response.status_code >= 400 and response.status_code != 404:
                response.raise_for_status()
            url = kwargs.get("url", args[0] if args else "")
            if "/v8/finance/chart/" in url:
                body = response.json()
                chart = body.get("chart") if isinstance(body, dict) else None
                if isinstance(chart, dict):
                    error, result = chart.get("error"), chart.get("result")
                    if isinstance(error, dict) and error.get("code") == "Not Found":
                        self.source_empty = True
                    elif error is None and "result" in chart:
                        if result is None or result == []:
                            self.source_empty = True
                        elif isinstance(result, list) and isinstance(result[0], dict):
                            indicators = result[0].get("indicators") or {}
                            quotes = indicators.get("quote") if isinstance(indicators, dict) else None
                            self.source_empty = quotes in ([], [{}])
                if self.source_empty:
                    if self.timezone_only:
                        # Recent quotes are not evidence for the dated history.
                        # Let the native metadata lookup use its info fallback.
                        self.metadata_empty = True
                    else:
                        raise self.unavailable
            return response
        except Exception as exc:
            self.failure = exc
            if self.timezone_only:
                self.metadata_failures.append(exc)
            raise

    def get(self, *args, **kwargs):
        return self._request("get", *args, **kwargs)

    def cache_get(self, *args, **kwargs):
        return self._request("cache_get", *args, **kwargs)

    def get_raw_json(self, *args, **kwargs):
        try:
            return self.data.get_raw_json(*args, **kwargs)
        except Exception as exc:
            if self.timezone_only and getattr(getattr(exc, "response", None), "status_code", None) == 404:
                self.metadata_empty = True
                return {}
            self.failure = exc
            if self.timezone_only:
                self.metadata_failures.append(exc)
            raise


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

        def fetch_symbol(symbol):
            provider_symbol = provider_symbols[symbol]
            ticker = yf.Ticker(provider_symbol)
            # Ticker owns this attribute; the shared YfData singleton and its
            # config/session remain untouched. Preserve failures that timezone
            # discovery would otherwise hide behind a missing-data exception.
            unavailable = ForwardPriceUnavailableError([symbol], start_date=start_date, end_date=end_date)
            data = _ForwardHistoryData(ticker._data, unavailable=unavailable)
            ticker._data = data
            ticker._quote._data = data
            native_timezone = ticker._get_ticker_tz

            def timezone_info():
                # Only timezone fields are needed, not Quote.info's extra
                # fundamental requests or its broad parser-error suppression.
                ticker._quote._fetch_info()
                return ticker._quote._info or {}

            ticker.get_info = timezone_info

            def checked_timezone(timeout):
                data.timezone_only = True
                data.metadata_empty = False
                data.metadata_failures.clear()
                try:
                    timezone = native_timezone(timeout)
                finally:
                    data.timezone_only = False
                if timezone is None:
                    if data.metadata_failures:
                        raise data.metadata_failures[0]
                    if data.metadata_empty:
                        raise unavailable
                return timezone

            ticker._get_ticker_tz = checked_timezone
            try:
                history = ticker.history(start=start_date, end=end_date + timedelta(days=1),
                    period=None, interval="1d", auto_adjust=self.auto_adjust,
                    actions=self.actions, raise_errors=True)
            except ForwardPriceUnavailableError as exc:
                if exc is not unavailable:
                    raise
                return symbol, []
            if history is None or getattr(history, "empty", False):
                if data.failure is not None:
                    raise data.failure
                return symbol, []
            return symbol, _price_rows(_symbol_frame(history, provider_symbol),
                research=not self.auto_adjust, benchmark=symbol in BENCHMARK_YFINANCE_SYMBOLS)

        # yf.download catches every Ticker error and substitutes empty frames.
        # Per-ticker strict history keeps those failures visible, with bounded
        # concurrency rather than process-wide exception configuration changes.
        with ThreadPoolExecutor(max_workers=min(8, len(ordered_symbols))) as pool:
            result = {symbol: rows for symbol, rows in pool.map(fetch_symbol, ordered_symbols) if rows}
        if not result:
            raise ForwardPriceUnavailableError(ordered_symbols, start_date=start_date, end_date=end_date)
        return result


class YFinanceResearchPriceProvider(YFinanceForwardPriceProvider):
    """Separate price-only data; never merge with adjusted technical snapshots."""
    auto_adjust = False
    actions = True


def get_research_price_provider(db: Session = Depends(get_db)) -> ForwardPriceProvider:
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    return RecoveringResearchPriceProvider(session=db if isinstance(db, Session) else None)


def get_forward_price_provider(db: Session = Depends(get_db)) -> ForwardPriceProvider:
    from ai_stock_sentinel.daily_radar.work_queue import durable_enabled
    if durable_enabled() and isinstance(db, Session):
        from ai_stock_sentinel.daily_radar.durable_providers import CachedForwardPrices
        from ai_stock_sentinel.daily_radar.source_snapshots import store_for_session
        return CachedForwardPrices(db, store=store_for_session(db))
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
    volume_column = _matching_column(frame, "Volume")
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
                "volume": _frame_number(frame, volume_column, position),
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
    "ForwardPriceUnavailableError",
    "ForwardPriceProvider",
    "YFinanceForwardPriceProvider",
    "get_forward_price_provider",
]
