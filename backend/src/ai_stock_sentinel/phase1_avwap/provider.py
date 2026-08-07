from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
import os
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.finmind_client import FinMindClient
from ai_stock_sentinel.daily_radar.market_bar_repository import get_taiwan_daily_bars
from ai_stock_sentinel.phase1_avwap.calculator import DailyPriceBar


DEFAULT_PHASE1_DATASET = "phase1_daily_ohlcv_amount"
DEFAULT_ADJUSTMENT_MODE = "unadjusted"
FINMIND_TAIWAN_STOCK_PRICE_DATASET = "TaiwanStockPrice"
TWSE_STOCK_DAY_DATASET = "TWSE_STOCK_DAY"
TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
DEFAULT_TWSE_MONTH_FETCH_WORKERS = 4

RequestGetter = Callable[..., Any]


@dataclass(frozen=True)
class DailyPriceHistoryResult:
    bars: list[DailyPriceBar]
    source_provider: str
    source_dataset: str


class DailyPriceProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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

    def source_provider(self, symbol: str) -> str:
        if _is_tpex_symbol(symbol):
            return _provider_metadata(self._fallback_provider, "source_provider", symbol, default="finmind")
        return "twse"

    def source_dataset(self, symbol: str) -> str:
        if _is_tpex_symbol(symbol):
            return _provider_metadata(
                self._fallback_provider,
                "source_dataset",
                symbol,
                default=FINMIND_TAIWAN_STOCK_PRICE_DATASET,
            )
        return TWSE_STOCK_DAY_DATASET

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        if _is_tpex_symbol(symbol):
            return self._fallback_provider.fetch_history(symbol, start_date=start_date, end_date=end_date)
        request_get = self._request_get or _import_requests_get()
        months = _month_starts(start_date, end_date)
        with ThreadPoolExecutor(max_workers=min(self._month_fetch_workers, len(months))) as executor:
            monthly_rows = executor.map(
                lambda month: _fetch_twse_month(
                    request_get,
                    symbol=symbol,
                    month=month,
                    timeout=self._timeout,
                ),
                months,
            )
            rows = [bar for month_rows in monthly_rows for bar in month_rows]
        return [
            bar
            for bar in sorted(rows, key=lambda item: item.trade_date)
            if start_date <= bar.trade_date <= end_date
        ]


class ArchiveFirstDailyPriceProvider:
    _MODES = {"yfinance_only", "official_first", "official_only"}

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
            "DAILY_RADAR_TW_OHLCV_PROVIDER_MODE",
            "yfinance_only",
        )
        if self._provider_mode not in self._MODES:
            raise ValueError("invalid DAILY_RADAR_TW_OHLCV_PROVIDER_MODE")
        self._min_trading_bars = max(1, min_trading_bars)

    def source_provider(self, symbol: str) -> str:
        if self._provider_mode == "yfinance_only":
            return _provider_metadata(
                self._fallback_provider,
                "source_provider",
                symbol,
                default="legacy_daily_price_provider",
            )
        return "taiwan_daily_bars_local_first"

    def source_dataset(self, symbol: str) -> str:
        if self._provider_mode == "yfinance_only":
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
        if self._provider_mode == "yfinance_only":
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
        if len(rows) >= self._min_trading_bars and rows[-1].trade_date == end_date:
            return DailyPriceHistoryResult(
                bars=[
                    DailyPriceBar(
                        trade_date=row.trade_date,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume),
                        amount=float(row.amount) if row.amount is not None else None,
                        estimated_amount=row.amount is None,
                    )
                    for row in rows
                ],
                source_provider=rows[-1].source_provider,
                source_dataset=rows[-1].source_dataset,
            )
        if self._provider_mode == "official_first":
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

    def _fallback_result(
        self,
        symbol: str,
        *,
        start_date: date,
        end_date: date,
    ) -> DailyPriceHistoryResult:
        return DailyPriceHistoryResult(
            bars=self._fallback_provider.fetch_history(
                symbol,
                start_date=start_date,
                end_date=end_date,
            ),
            source_provider=_provider_metadata(
                self._fallback_provider,
                "source_provider",
                symbol,
                default="legacy_daily_price_provider",
            ),
            source_dataset=_provider_metadata(
                self._fallback_provider,
                "source_dataset",
                symbol,
                default=FINMIND_TAIWAN_STOCK_PRICE_DATASET,
            ),
        )


def _fetch_twse_month(
    request_get: RequestGetter,
    *,
    symbol: str,
    month: date,
    timeout: int,
) -> list[DailyPriceBar]:
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
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else response
    except Exception as exc:
        raise DailyPriceProviderError("twse_stock_day_request_failed") from exc
    try:
        return normalize_twse_stock_day_payload(payload)
    except ValueError as exc:
        raise DailyPriceProviderError("twse_stock_day_parser_error") from exc


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


def _import_requests_get() -> RequestGetter:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests package is required for TWSE AVWAP requests") from exc
    return requests.get


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
