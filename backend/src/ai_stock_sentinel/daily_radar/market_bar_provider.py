from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from threading import Lock
import time
from typing import Any


TWSE_MARKET_BAR_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
TPEX_MARKET_BAR_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/"
    "stk_wn1430_result.php"
)

RequestGetter = Callable[..., Any]
Sleeper = Callable[[float], None]


class OfficialMarketBarProviderError(RuntimeError):
    def __init__(self, code: str, *, market: str) -> None:
        super().__init__(code)
        self.code = code
        self.market = market


@dataclass(frozen=True)
class MarketDailyBar:
    symbol: str
    market: str
    name: str | None
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: int | None
    source_provider: str
    source_dataset: str
    is_final: bool = True


class OfficialTaiwanMarketBarProvider:
    def __init__(
        self,
        *,
        request_get: RequestGetter | None = None,
        timeout: int = 45,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        max_retry_delay_seconds: float = 30.0,
        sleep: Sleeper = time.sleep,
    ) -> None:
        self._request_get = request_get
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._max_retry_delay_seconds = max(0.0, max_retry_delay_seconds)
        self._sleep = sleep
        self._tpex_request_lock = Lock()

    def fetch_market(self, *, market: str, trade_date: date) -> list[MarketDailyBar]:
        request_get = self._request_get or _import_requests_get()
        if market == "TW":
            payload = _request_json(
                request_get,
                TWSE_MARKET_BAR_URL,
                params={
                    "response": "json",
                    "date": trade_date.strftime("%Y%m%d"),
                    "type": "ALL",
                },
                timeout=self._timeout,
                market=market,
                max_attempts=self._max_attempts,
                retry_backoff_seconds=self._retry_backoff_seconds,
                max_retry_delay_seconds=self._max_retry_delay_seconds,
                sleep=self._sleep,
            )
            return normalize_twse_market_bars(payload, expected_date=trade_date)
        if market == "TWO":
            roc_year = trade_date.year - 1911
            with self._tpex_request_lock:
                payload = _request_json(
                    request_get,
                    TPEX_MARKET_BAR_URL,
                    params={
                        "l": "zh-tw",
                        "o": "json",
                        "d": f"{roc_year:03d}/{trade_date.month:02d}/{trade_date.day:02d}",
                        "se": "AL",
                        "s": "0,asc,0",
                    },
                    timeout=self._timeout,
                    market=market,
                    max_attempts=self._max_attempts,
                    retry_backoff_seconds=self._retry_backoff_seconds,
                    max_retry_delay_seconds=self._max_retry_delay_seconds,
                    sleep=self._sleep,
                )
            return normalize_tpex_market_bars(payload, expected_date=trade_date)
        raise OfficialMarketBarProviderError("unsupported_market", market=market)


def normalize_twse_market_bars(
    payload: Mapping[str, Any],
    *,
    expected_date: date,
) -> list[MarketDailyBar]:
    stat = str(payload.get("stat") or "").strip().lower()
    if stat != "ok":
        if _is_no_data_status(stat):
            return []
        raise OfficialMarketBarProviderError("twse_market_bar_response_error", market="TW")
    try:
        payload_date = _compact_date(str(payload.get("date") or ""))
    except ValueError as exc:
        raise OfficialMarketBarProviderError("twse_market_bar_date_invalid", market="TW") from exc
    if payload_date != expected_date:
        raise OfficialMarketBarProviderError("twse_market_bar_date_mismatch", market="TW")
    table = _find_table(
        payload,
        required_fields=("證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數"),
        market="TW",
    )
    return _normalize_market_bar_rows(
        table,
        market="TW",
        trade_date=payload_date,
        aliases={
            "code": ("證券代號",),
            "name": ("證券名稱",),
            "open": ("開盤價",),
            "high": ("最高價",),
            "low": ("最低價",),
            "close": ("收盤價",),
            "volume": ("成交股數",),
            "amount": ("成交金額",),
        },
        source_provider="twse",
        source_dataset="TWSE_MI_INDEX",
    )


def normalize_tpex_market_bars(
    payload: Mapping[str, Any],
    *,
    expected_date: date,
) -> list[MarketDailyBar]:
    stat = str(payload.get("stat") or "").strip().lower()
    if stat and stat != "ok" and _is_no_data_status(stat):
        return []
    table = _find_table(
        payload,
        required_fields=("代號", "收盤", "開盤", "最高", "最低", "成交股數"),
        market="TWO",
        normalized_field_match=True,
    )
    try:
        payload_date = _roc_slash_date(str(table.get("date") or ""))
    except ValueError as exc:
        raise OfficialMarketBarProviderError("tpex_market_bar_date_invalid", market="TWO") from exc
    if payload_date != expected_date:
        raise OfficialMarketBarProviderError("tpex_market_bar_date_mismatch", market="TWO")
    return _normalize_market_bar_rows(
        table,
        market="TWO",
        trade_date=payload_date,
        aliases={
            "code": ("代號",),
            "name": ("名稱",),
            "open": ("開盤",),
            "high": ("最高",),
            "low": ("最低",),
            "close": ("收盤",),
            "volume": ("成交股數",),
            "amount": ("成交金額(元)",),
        },
        source_provider="tpex",
        source_dataset="TPEX_otc_quotes_no1430",
    )


def _normalize_market_bar_rows(
    table: Mapping[str, Any],
    *,
    market: str,
    trade_date: date,
    aliases: Mapping[str, tuple[str, ...]],
    source_provider: str,
    source_dataset: str,
) -> list[MarketDailyBar]:
    fields = table.get("fields")
    data = table.get("data")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise OfficialMarketBarProviderError("market_bar_fields_invalid", market=market)
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise OfficialMarketBarProviderError("market_bar_rows_invalid", market=market)
    normalized_fields = {_normalize_field(str(field)): index for index, field in enumerate(fields)}
    indexes: dict[str, int] = {}
    for target, candidates in aliases.items():
        for candidate in candidates:
            index = normalized_fields.get(_normalize_field(candidate))
            if index is not None:
                indexes[target] = index
                break
    required = ("code", "name", "open", "high", "low", "close", "volume")
    if any(field not in indexes for field in required):
        raise OfficialMarketBarProviderError("market_bar_schema_changed", market=market)

    bars: list[MarketDailyBar] = []
    for row in data:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        if max(indexes.values()) >= len(row):
            continue
        stock_id = str(row[indexes["code"]] or "").strip()
        if re.fullmatch(r"[1-9]\d{3}", stock_id) is None:
            continue
        try:
            open_price = _decimal(row[indexes["open"]])
            high = _decimal(row[indexes["high"]])
            low = _decimal(row[indexes["low"]])
            close = _decimal(row[indexes["close"]])
            volume = _integer(row[indexes["volume"]])
            amount_index = indexes.get("amount")
            amount = _integer(row[amount_index]) if amount_index is not None else None
        except (InvalidOperation, ValueError):
            continue
        if min(open_price, high, low, close) <= 0 or high < low or volume < 0:
            continue
        suffix = ".TW" if market == "TW" else ".TWO"
        bars.append(
            MarketDailyBar(
                symbol=f"{stock_id}{suffix}",
                market=market,
                name=str(row[indexes["name"]] or "").strip() or None,
                trade_date=trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                source_provider=source_provider,
                source_dataset=source_dataset,
            )
        )
    return bars


def _find_table(
    payload: Mapping[str, Any],
    *,
    required_fields: tuple[str, ...],
    market: str,
    normalized_field_match: bool = False,
) -> Mapping[str, Any]:
    tables = payload.get("tables")
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        raise OfficialMarketBarProviderError("market_bar_tables_invalid", market=market)
    normalized_required = {_normalize_field(field) for field in required_fields}
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        fields = table.get("fields")
        if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
            continue
        if normalized_field_match:
            normalized = {_normalize_field(str(field)) for field in fields}
            if normalized_required.issubset(normalized):
                return table
        elif set(required_fields).issubset({str(field) for field in fields}):
            return table
    raise OfficialMarketBarProviderError("market_bar_table_not_found", market=market)


def _request_json(
    request_get: RequestGetter,
    url: str,
    *,
    params: Mapping[str, Any],
    timeout: int,
    market: str,
    max_attempts: int,
    retry_backoff_seconds: float,
    max_retry_delay_seconds: float,
    sleep: Sleeper,
) -> Mapping[str, Any]:
    for attempt in range(max_attempts):
        response: Any = None
        try:
            response = request_get(
                url,
                params=dict(params),
                timeout=timeout,
                headers={"Accept": "application/json"},
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else response
            break
        except Exception as exc:
            is_last_attempt = attempt + 1 >= max_attempts
            if is_last_attempt or not _is_retryable_request_failure(response):
                raise OfficialMarketBarProviderError(
                    "market_bar_request_failed",
                    market=market,
                ) from exc
            sleep(
                _retry_delay_seconds(
                    response,
                    attempt,
                    retry_backoff_seconds,
                    max_retry_delay_seconds,
                )
            )
    if not isinstance(payload, Mapping):
        raise OfficialMarketBarProviderError("market_bar_response_invalid", market=market)
    return payload


def _is_retryable_request_failure(response: Any) -> bool:
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int):
        return True
    return status_code < 400 or status_code in {408, 425, 429} or status_code >= 500


def _retry_delay_seconds(
    response: Any,
    attempt: int,
    backoff_seconds: float,
    max_delay_seconds: float,
) -> float:
    delay = backoff_seconds * (2**attempt)
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        retry_after = str(headers.get("Retry-After") or "").strip()
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass
    return min(delay, max_delay_seconds)


def _normalize_field(value: str) -> str:
    return re.sub(r"<[^>]+>|\s+", "", value)


def _decimal(value: Any) -> Decimal:
    text = str(value).replace(",", "").strip()
    if text in {"", "--", "-"}:
        raise InvalidOperation
    return Decimal(text)


def _integer(value: Any) -> int:
    return int(_decimal(value))


def _compact_date(value: str) -> date:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) != 8:
        raise ValueError("invalid date")
    return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))


def _roc_slash_date(value: str) -> date:
    parts = value.strip().split("/")
    if len(parts) != 3:
        raise ValueError("invalid ROC date")
    return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))


def _is_no_data_status(stat: str) -> bool:
    return any(marker in stat for marker in ("沒有符合條件", "查無資料", "no data"))


def _import_requests_get() -> RequestGetter:
    try:
        import requests
    except ImportError as exc:
        raise OfficialMarketBarProviderError("missing_dependency", market="TW") from exc
    return requests.get


__all__ = [
    "MarketDailyBar",
    "OfficialMarketBarProviderError",
    "OfficialTaiwanMarketBarProvider",
    "TPEX_MARKET_BAR_URL",
    "TWSE_MARKET_BAR_URL",
    "normalize_tpex_market_bars",
    "normalize_twse_market_bars",
]
