from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from functools import partial
import json
import logging
import math
import os
from threading import Event
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.finmind_client import FinMindClient, FinMindClientError
from ai_stock_sentinel.data_sources.official_http import official_request_get
from ai_stock_sentinel.data_sources.reviewed_market_lifecycle import listing_evidence
from ai_stock_sentinel.calibration.repository import load_benchmark_calendar_from_prepared_market_context
from ai_stock_sentinel.daily_radar.market_bar_repository import get_taiwan_daily_bars, get_taiwan_archive_session_dates
from ai_stock_sentinel.phase1_avwap.calculator import DailyPriceBar


DEFAULT_PHASE1_DATASET = "phase1_daily_ohlcv_amount"
DEFAULT_ADJUSTMENT_MODE = "unadjusted"
FINMIND_TAIWAN_STOCK_PRICE_DATASET = "TaiwanStockPrice"
TWSE_STOCK_DAY_DATASET = "TWSE_STOCK_DAY"
TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
DEFAULT_TWSE_MONTH_FETCH_WORKERS = 2
TWSE_REQUEST_MAX_ATTEMPTS = 2
logger = logging.getLogger(__name__)

RequestGetter = Callable[..., Any]


@dataclass(frozen=True)
class DailyPriceHistoryResult:
    bars: list[DailyPriceBar]
    source_provider: str
    source_dataset: str
    source_trace: dict[str, Any] = field(default_factory=dict)


class DailyPriceProviderError(RuntimeError):
    def __init__(self, code: str, *, http_status: int | None = None,
                 source_trace: dict[str, Any] | None = None,
                 source_provider: str | None = None, source_dataset: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.source_trace = source_trace or {}
        self.source_provider = source_provider
        self.source_dataset = source_dataset


class InsufficientListedHistory(DailyPriceProviderError):
    """Complete official history since a proven listing, shorter than the anchors."""


class FinMindDailyPriceProvider:
    source_provider = "finmind"
    source_dataset = FINMIND_TAIWAN_STOCK_PRICE_DATASET

    def __init__(self, *, client: FinMindClient | None = None) -> None:
        self._client = client or FinMindClient()

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        rows = self._client.fetch_data(
            dataset=FINMIND_TAIWAN_STOCK_PRICE_DATASET,
            data_id=_finmind_data_id(symbol),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        return normalize_finmind_daily_price_rows(rows)


class TwseDailyPriceProvider:
    def __init__(
        self,
        *,
        request_get: RequestGetter | None = None,
        fallback_provider: FinMindDailyPriceProvider | None = None,
        timeout: int = 15,
        month_fetch_workers: int = DEFAULT_TWSE_MONTH_FETCH_WORKERS,
    ) -> None:
        self._request_get = request_get
        self._fallback_provider = fallback_provider or FinMindDailyPriceProvider()
        self._timeout = timeout
        self._month_fetch_workers = max(1, month_fetch_workers)
        # Request-scoped only; a new as-of date must fetch the month again.
        self._monthly_rows: dict[tuple[str, date, date], tuple[DailyPriceBar, ...]] = {}
        self._blocked_dates: set[date] = set()
        self._fallback_symbols: set[str] = set()

    def begin_refresh(self) -> None:
        self._blocked_dates.clear()
        self._fallback_symbols.clear()
        self._monthly_rows.clear()

    def source_provider(self, symbol: str) -> str:
        if _is_tpex_symbol(symbol) or symbol in self._fallback_symbols:
            return _provider_metadata(self._fallback_provider, "source_provider", symbol, default="finmind")
        return "twse"

    def source_dataset(self, symbol: str) -> str:
        if _is_tpex_symbol(symbol) or symbol in self._fallback_symbols:
            return _provider_metadata(
                self._fallback_provider,
                "source_dataset",
                symbol,
                default=FINMIND_TAIWAN_STOCK_PRICE_DATASET,
            )
        return TWSE_STOCK_DAY_DATASET

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        return self.fetch_history_result(symbol, start_date=start_date, end_date=end_date).bars

    def fetch_history_result(self, symbol: str, *, start_date: date, end_date: date) -> DailyPriceHistoryResult:
        trace = {}
        if not _is_tpex_symbol(symbol):
            try:
                if end_date not in self._blocked_dates:
                    bars = self._fetch_twse_history(symbol, start_date=start_date, end_date=end_date)
                    return DailyPriceHistoryResult(_validated_history(bars, start_date, end_date),
                                                   "twse", TWSE_STOCK_DAY_DATASET)
            except DailyPriceProviderError as exc:
                if exc.http_status != 403:
                    raise
                self._blocked_dates.add(end_date)
            trace = {"fallback_reason": "twse_stock_day_http_403", "http_status": 403,
                     "primary_provider": "twse", "requested_start_date": start_date.isoformat(),
                     "requested_end_date": end_date.isoformat()}
        self._fallback_symbols.add(symbol)
        provider, dataset = self.source_provider(symbol), self.source_dataset(symbol)
        try:
            bars = self._fallback_provider.fetch_history(symbol, start_date=start_date, end_date=end_date)
            accepted = _validated_history(bars, start_date, end_date)
            if trace:
                trace.update({"rows_used": len(accepted),
                              "actual_start_date": accepted[0].trade_date.isoformat() if accepted else None,
                              "actual_end_date": accepted[-1].trade_date.isoformat() if accepted else None})
            return DailyPriceHistoryResult(accepted, provider, dataset, trace)
        except (FinMindClientError, DailyPriceProviderError, KeyError, ValueError) as exc:
            raise DailyPriceProviderError(getattr(exc, "code", "daily_price_history_unavailable"),
                source_trace=trace, source_provider=provider, source_dataset=dataset) from exc

    def _fetch_twse_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        request_get = self._request_get or partial(official_request_get, max_attempts=TWSE_REQUEST_MAX_ATTEMPTS)
        months = _month_starts(start_date, end_date)
        monthly_rows = {
            month: self._monthly_rows[(symbol, month, end_date)]
            for month in months if (symbol, month, end_date) in self._monthly_rows
        }
        pending = [month for month in months if month not in monthly_rows]
        failures: dict[date, DailyPriceProviderError] = {}
        if pending:
            forbidden = Event()
            def fetch_month(month: date) -> list[DailyPriceBar]:
                if forbidden.is_set():
                    raise DailyPriceProviderError("twse_stock_day_request_failed", http_status=403)
                try:
                    return _fetch_twse_month(request_get, symbol=symbol, month=month, timeout=self._timeout)
                except DailyPriceProviderError as exc:
                    if exc.http_status == 403:
                        forbidden.set()
                    raise
            with ThreadPoolExecutor(max_workers=min(self._month_fetch_workers, len(pending))) as executor:
                futures = {
                    executor.submit(fetch_month, month): month
                    for month in pending
                }
                for future in as_completed(futures):
                    month = futures[future]
                    try:
                        fetched = tuple(future.result())
                    except CancelledError:
                        continue
                    except DailyPriceProviderError as exc:
                        failures[month] = exc
                        if exc.http_status == 403:
                            self._blocked_dates.add(end_date)
                            for queued in futures:
                                queued.cancel()
                        continue
                    monthly_rows[month] = fetched
                    if fetched:
                        self._monthly_rows[(symbol, month, end_date)] = fetched
        # Check every available month before failure precedence, including cached
        # months. Keep the full monthly cache when validating a clipped window.
        for month, bars in monthly_rows.items():
            try:
                _validated_history(bars, start_date, end_date)
            except DailyPriceProviderError as exc:
                failures[month] = exc
        if failures:
            # Retain other successful months, but never return partial history.
            invalid_payloads = [error for error in failures.values()
                                if error.code in {"twse_stock_day_parser_error", "daily_price_duplicate_dates",
                                                  "daily_price_values_invalid"}
                                or (error.http_status is not None and 200 <= error.http_status < 300)]
            if invalid_payloads:
                raise invalid_payloads[0]
            if any(error.http_status == 403 for error in failures.values()):
                raise next(error for error in failures.values() if error.http_status == 403)
            raise failures[min(failures)]
        rows = [bar for month in months for bar in monthly_rows[month]]
        return [
            bar
            for bar in sorted(rows, key=lambda item: item.trade_date)
            if start_date <= bar.trade_date <= end_date
        ]


class ArchiveFirstDailyPriceProvider:
    _MODES = {"archive_first", "remote_only", "yfinance_only", "official_first", "official_only"}

    def __init__(
        self,
        session: Session,
        *,
        fallback_provider: Any | None = None,
        provider_mode: str | None = None,
        min_trading_bars: int = 60,
    ) -> None:
        self._session = session
        self._fallback_provider = fallback_provider or TwseDailyPriceProvider()
        self._provider_mode = provider_mode or os.getenv(
            "DAILY_RADAR_AVWAP_PROVIDER_MODE",
            "archive_first",
        )
        if self._provider_mode not in self._MODES:
            raise ValueError("invalid DAILY_RADAR_AVWAP_PROVIDER_MODE")
        self._min_trading_bars = max(1, min_trading_bars)
        self._archive_dates: dict[tuple[str, date, date], list[date]] = {}

    def begin_refresh(self) -> None:
        self._archive_dates.clear()
        reset = getattr(self._fallback_provider, "begin_refresh", None)
        if callable(reset):
            reset()

    def source_provider(self, symbol: str) -> str:
        if self._provider_mode in {"yfinance_only", "remote_only"}:
            return _provider_metadata(
                self._fallback_provider,
                "source_provider",
                symbol,
                default="legacy_daily_price_provider",
            )
        return "taiwan_daily_bars_local_first"

    def source_dataset(self, symbol: str) -> str:
        if self._provider_mode in {"yfinance_only", "remote_only"}:
            return _provider_metadata(
                self._fallback_provider,
                "source_dataset",
                symbol,
                default=FINMIND_TAIWAN_STOCK_PRICE_DATASET,
            )
        return "taiwan_market_daily_ohlcv_or_legacy_fallback"

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        return self.fetch_history_result(
            symbol,
            start_date=start_date,
            end_date=end_date,
        ).bars

    def fetch_history_result(
        self,
        symbol: str,
        *,
        start_date: date,
        end_date: date,
    ) -> DailyPriceHistoryResult:
        if self._provider_mode in {"yfinance_only", "remote_only"}:
            return self._fallback_result(
                symbol,
                start_date=start_date,
                end_date=end_date,
            )
        rows = get_taiwan_daily_bars(
            self._session,
            symbols=[symbol],
            start_date=start_date,
            end_date=end_date,
        )
        evidence = listing_evidence(symbol, as_of_date=end_date)
        if evidence is not None:
            rows = [row for row in rows if row.trade_date >= evidence.listing_date]
        market = "TWO" if _is_tpex_symbol(symbol) else "TW"
        key = (market, start_date, end_date)
        if key not in self._archive_dates:
            self._archive_dates[key] = get_taiwan_archive_session_dates(
                self._session, market=market, start_date=start_date, end_date=end_date,
            )
        recent_dates = self._archive_dates[key][-self._min_trading_bars:]
        if (len(rows) >= self._min_trading_bars and rows[-1].trade_date == end_date
                and set(recent_dates).issubset({row.trade_date for row in rows})):
            try:
                bars = _validated_history([
                    DailyPriceBar(
                        trade_date=row.trade_date,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume),
                        amount=(float(row.amount) if row.amount is not None else
                                (float(row.high) + float(row.low) + float(row.close)) / 3 * float(row.volume)),
                        estimated_amount=row.amount is None,
                    )
                    for row in rows
                ], start_date, end_date)
            except DailyPriceProviderError:
                bars = []
            if bars:
                return DailyPriceHistoryResult(
                    bars=bars,
                    source_provider=rows[-1].source_provider,
                    source_dataset=rows[-1].source_dataset,
                    source_trace={"route": "archive_first", "rows_used": len(bars),
                                  "actual_start_date": bars[0].trade_date.isoformat(),
                                  "actual_end_date": bars[-1].trade_date.isoformat()},
                )
        self._check_short_listed_history(symbol, rows=rows, start_date=start_date, end_date=end_date)
        if self._provider_mode in {"official_first", "archive_first"}:
            return self._fallback_result(
                symbol,
                start_date=start_date,
                end_date=end_date,
            )
        return DailyPriceHistoryResult(
            bars=[],
            source_provider="taiwan_daily_bars",
            source_dataset="taiwan_market_daily_ohlcv",
        )

    def _check_short_listed_history(self, symbol, *, rows, start_date, end_date):
        evidence = listing_evidence(symbol, as_of_date=end_date)
        if (evidence is None or evidence.listing_date < start_date
                or not rows or len(rows) >= self._min_trading_bars):
            return
        listed_rows = [row for row in rows if row.trade_date >= evidence.listing_date]
        calendar = load_benchmark_calendar_from_prepared_market_context(
            self._session, market="TW", benchmark_symbol="TAIEX",
            start_date=evidence.listing_date, as_of_date=end_date,
        )
        # The union of individual stock bars alone cannot establish a calendar.
        market = "TWO" if _is_tpex_symbol(symbol) else "TW"
        archive_dates = self._archive_dates[(market, start_date, end_date)]
        if (not calendar or calendar[0] != evidence.listing_date or calendar[-1] != end_date
                or set(calendar) != {row.trade_date for row in listed_rows}
                or set(calendar) != {day for day in archive_dates if day >= evidence.listing_date}
                or any(row.source_provider != ("tpex" if market == "TWO" else "twse") for row in listed_rows)):
            return
        try:
            bars = _validated_history([
                DailyPriceBar(trade_date=row.trade_date, open=float(row.open), high=float(row.high),
                              low=float(row.low), close=float(row.close), volume=float(row.volume),
                              amount=float(row.amount) if row.amount is not None else
                              (float(row.high) + float(row.low) + float(row.close)) / 3 * float(row.volume),
                              estimated_amount=row.amount is None)
                for row in listed_rows
            ], evidence.listing_date, end_date)
        except DailyPriceProviderError:
            return
        raise InsufficientListedHistory(
            "insufficient_listed_history", source_provider=listed_rows[-1].source_provider,
            source_dataset=listed_rows[-1].source_dataset,
            source_trace={"route": "archive_first", "market_lifecycle": evidence.payload(),
                          "rows_used": len(bars), "required_trading_bars": self._min_trading_bars,
                          "actual_start_date": bars[0].trade_date.isoformat(),
                          "actual_end_date": bars[-1].trade_date.isoformat()},
        )

    def _fallback_result(
        self,
        symbol: str,
        *,
        start_date: date,
        end_date: date,
    ) -> DailyPriceHistoryResult:
        evidence = listing_evidence(symbol, as_of_date=end_date)
        if evidence is not None:
            start_date = max(start_date, evidence.listing_date)
        fetch_result = getattr(self._fallback_provider, "fetch_history_result", None)
        if callable(fetch_result):
            result = fetch_result(symbol, start_date=start_date, end_date=end_date)
        else:
            result = DailyPriceHistoryResult(
                bars=self._fallback_provider.fetch_history(symbol, start_date=start_date, end_date=end_date),
                source_provider=_provider_metadata(self._fallback_provider, "source_provider", symbol,
                                                  default="legacy_daily_price_provider"),
                source_dataset=_provider_metadata(self._fallback_provider, "source_dataset", symbol,
                                                 default=FINMIND_TAIWAN_STOCK_PRICE_DATASET),
            )
        if evidence is None:
            return result
        trace = {**result.source_trace, "market_lifecycle": evidence.payload()}
        try:
            bars = _validated_history(result.bars, start_date, end_date)
            trace.update(rows_used=len(bars), required_trading_bars=self._min_trading_bars)
            if len(bars) < self._min_trading_bars:
                # Short remote coverage does not prove the complete official listed history.
                raise DailyPriceProviderError("daily_price_history_incomplete")
        except DailyPriceProviderError as exc:
            raise DailyPriceProviderError(exc.code, source_trace=trace,
                source_provider=result.source_provider, source_dataset=result.source_dataset) from exc
        return DailyPriceHistoryResult(bars, result.source_provider, result.source_dataset, trace)


def _fetch_twse_month(
    request_get: RequestGetter,
    *,
    symbol: str,
    month: date,
    timeout: int,
) -> list[DailyPriceBar]:
    response = None
    stage = "request"
    try:
        response = request_get(
            TWSE_STOCK_DAY_URL,
            params={
                "response": "json",
                "date": f"{month.year:04d}{month.month:02d}01",
                "stockNo": _twse_stock_no(symbol),
            },
            timeout=timeout,
        )
        stage = "http_status"
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        stage = "json"
        payload = response.json() if hasattr(response, "json") else response
    except Exception as exc:
        _log_twse_failure(symbol, month, stage, response, exc)
        raise DailyPriceProviderError("twse_stock_day_request_failed",
                                      http_status=getattr(response, "status_code", None)) from exc
    try:
        rows = normalize_twse_stock_day_payload(payload)
        if any((bar.trade_date.year, bar.trade_date.month) != (month.year, month.month) for bar in rows):
            raise ValueError("TWSE returned rows outside the requested month")
        return rows
    except ValueError as exc:
        _log_twse_failure(symbol, month, "payload_schema", response, exc)
        raise DailyPriceProviderError("twse_stock_day_parser_error") from exc


def _log_twse_failure(symbol: str, month: date, stage: str, response: Any, exc: Exception) -> None:
    headers = getattr(response, "headers", {}) or {}
    # Allowlisted response metadata only; no bodies, exception text or credentials.
    detail = {
        "symbol": symbol,
        "month": month.isoformat(),
        "stage": stage,
        "error_type": type(exc).__name__,
        "http_status": getattr(response, "status_code", None),
        "content_type": str(headers.get("Content-Type", ""))[:80] or None,
    }
    logger.warning("twse_stock_day_fetch_failed %s", json.dumps(detail, sort_keys=True))


def _validated_history(bars: Sequence[DailyPriceBar], start_date: date, end_date: date) -> list[DailyPriceBar]:
    accepted = sorted((bar for bar in bars if start_date <= bar.trade_date <= end_date),
                      key=lambda bar: bar.trade_date)
    if len({bar.trade_date for bar in accepted}) != len(accepted):
        raise DailyPriceProviderError("daily_price_duplicate_dates")
    for bar in accepted:
        prices = (bar.open, bar.high, bar.low, bar.close)
        if (not all(math.isfinite(value) and value > 0 for value in prices)
                or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close)
                or not math.isfinite(bar.volume) or bar.volume < 0
                or bar.amount is None or not math.isfinite(bar.amount) or bar.amount < 0):
            raise DailyPriceProviderError("daily_price_values_invalid")
    return accepted


def normalize_finmind_daily_price_rows(rows: list[Mapping[str, Any]]) -> list[DailyPriceBar]:
    normalized: list[DailyPriceBar] = []
    for row in rows:
        trade_date = date.fromisoformat(str(row["date"]))
        open_price = _number(row, "open")
        high = _number(row, "max", "high")
        low = _number(row, "min", "low")
        close = _number(row, "close")
        volume = _number(row, "Trading_Volume", "volume")
        amount = _optional_number(row, "Trading_money", "amount")
        estimated_amount = False
        if amount is None:
            amount = ((high + low + close) / 3) * volume
            estimated_amount = True
        normalized.append(
            DailyPriceBar(
                trade_date=trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                estimated_amount=estimated_amount,
            )
        )
    return sorted(normalized, key=lambda bar: bar.trade_date)


def normalize_twse_stock_day_payload(payload: Mapping[str, Any]) -> list[DailyPriceBar]:
    if str(payload.get("stat", "")).upper() != "OK":
        return []
    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise ValueError("twse_stock_day_fields_unavailable")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        return []
    indexes = {str(field): index for index, field in enumerate(fields)}
    normalized: list[DailyPriceBar] = []
    for row in data:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        if _twse_row_has_placeholder_prices(row, indexes):
            continue
        trade_date = _twse_trade_date(_sequence_cell(row, indexes, "日期"))
        open_price = _parse_number(_sequence_cell(row, indexes, "開盤價"))
        high = _parse_number(_sequence_cell(row, indexes, "最高價"))
        low = _parse_number(_sequence_cell(row, indexes, "最低價"))
        close = _parse_number(_sequence_cell(row, indexes, "收盤價"))
        volume = _parse_number(_sequence_cell(row, indexes, "成交股數"))
        amount = _parse_number(_sequence_cell(row, indexes, "成交金額"))
        normalized.append(
            DailyPriceBar(
                trade_date=trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                estimated_amount=False,
            )
        )
    return sorted(normalized, key=lambda bar: bar.trade_date)


def _finmind_data_id(symbol: str) -> str:
    return symbol.upper().removesuffix(".TW").removesuffix(".TWO")


def _twse_stock_no(symbol: str) -> str:
    return symbol.upper().removesuffix(".TW")


def _is_tpex_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(".TWO")


def _month_starts(start_date: date, end_date: date) -> list[date]:
    months: list[date] = []
    cursor = date(start_date.year, start_date.month, 1)
    final = date(end_date.year, end_date.month, 1)
    while cursor <= final:
        months.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _sequence_cell(row: Sequence[Any], indexes: Mapping[str, int], field: str) -> str:
    index = indexes.get(field)
    if index is None or index >= len(row):
        raise ValueError(f"missing twse field: {field}")
    return str(row[index]).strip()


def _twse_row_has_placeholder_prices(row: Sequence[Any], indexes: Mapping[str, int]) -> bool:
    try:
        return any(
            _sequence_cell(row, indexes, field) == "--"
            for field in ("開盤價", "最高價", "最低價", "收盤價")
        )
    except ValueError:
        return False


def _twse_trade_date(value: str) -> date:
    if "/" in value:
        year_text, month_text, day_text = value.split("/")
        return date(int(year_text) + 1911, int(month_text), int(day_text))
    if len(value) == 7 and value.isdigit():
        return date(int(value[:3]) + 1911, int(value[3:5]), int(value[5:7]))
    raise ValueError("invalid twse trade date")


def _parse_number(value: str) -> float:
    normalized = value.replace(",", "").strip()
    if not normalized:
        raise ValueError("missing twse numeric value")
    return float(normalized)


def _provider_metadata(provider: object, name: str, symbol: str, *, default: str) -> str:
    value = getattr(provider, name, None)
    if callable(value):
        return str(value(symbol))
    if value:
        return str(value)
    return default


def _number(row: Mapping[str, Any], *keys: str) -> float:
    value = _optional_number(row, *keys)
    if value is None:
        raise ValueError(f"missing numeric field: {'/'.join(keys)}")
    return value


def _optional_number(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        return float(value)
    return None


__all__ = [
    "DailyPriceHistoryResult",
    "ArchiveFirstDailyPriceProvider",
    "DailyPriceProviderError",
    "DEFAULT_ADJUSTMENT_MODE",
    "DEFAULT_PHASE1_DATASET",
    "FINMIND_TAIWAN_STOCK_PRICE_DATASET",
    "FinMindDailyPriceProvider",
    "TWSE_STOCK_DAY_DATASET",
    "TWSE_STOCK_DAY_URL",
    "TwseDailyPriceProvider",
    "normalize_finmind_daily_price_rows",
    "normalize_twse_stock_day_payload",
]
