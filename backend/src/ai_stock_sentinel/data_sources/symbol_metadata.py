from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

TWSE_STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_MAINBOARD_DAILY_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
DEFAULT_SYMBOL_METADATA_CACHE_TTL_SECONDS = 60 * 60 * 12
DEFAULT_SYMBOL_METADATA_FAILURE_CACHE_TTL_SECONDS = 60 * 5

RequestGetter = Callable[..., Any]


@dataclass(frozen=True)
class SymbolMetadata:
    symbol: str
    name: str | None
    market: str | None = None


class SymbolMetadataResolver:
    def __init__(
        self,
        *,
        request_get: RequestGetter | None = None,
        ttl_seconds: int = DEFAULT_SYMBOL_METADATA_CACHE_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._request_get = request_get
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.time
        self._cache: dict[str, tuple[float, SymbolMetadata]] = {}
        self._market_rows_cache: dict[str, tuple[float, list[Mapping[str, Any]]]] = {}

    def resolve(self, symbol: str) -> SymbolMetadata:
        normalized_symbol = normalize_symbol(symbol)
        cached = self._cache.get(normalized_symbol)
        now = self._clock()
        if cached and cached[0] > now:
            return cached[1]

        metadata, ttl_seconds = self._resolve_uncached(normalized_symbol)
        self._cache[normalized_symbol] = (now + ttl_seconds, metadata)
        return metadata

    def resolve_name(self, symbol: str) -> str | None:
        return self.resolve(symbol).name

    def _resolve_uncached(self, symbol: str) -> tuple[SymbolMetadata, int]:
        stock_id = strip_symbol_suffix(symbol)
        market = detect_market(symbol)
        rows, ttl_seconds = self._rows_for_market(market)
        for row in rows:
            if _row_stock_id(row) == stock_id:
                return SymbolMetadata(symbol=symbol, name=_row_stock_name(row), market=market), ttl_seconds
        return SymbolMetadata(symbol=symbol, name=None, market=market), ttl_seconds

    def _rows_for_market(self, market: str) -> tuple[list[Mapping[str, Any]], int]:
        now = self._clock()
        cached = self._market_rows_cache.get(market)
        if cached and cached[0] > now:
            return cached[1], int(cached[0] - now)

        url = TWSE_STOCK_DAY_ALL_URL if market == "TW" else TPEX_MAINBOARD_DAILY_QUOTES_URL
        rows = self._fetch_rows(url)
        if rows is None:
            self._market_rows_cache[market] = (now + DEFAULT_SYMBOL_METADATA_FAILURE_CACHE_TTL_SECONDS, [])
            return [], DEFAULT_SYMBOL_METADATA_FAILURE_CACHE_TTL_SECONDS
        self._market_rows_cache[market] = (now + self._ttl_seconds, rows)
        return rows, self._ttl_seconds

    def _fetch_rows(self, url: str) -> list[Mapping[str, Any]] | None:
        try:
            request_get = self._request_get or _import_requests_get()
            response = request_get(url, timeout=15, headers={"Accept": "application/json"})
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else response
        except Exception as exc:
            logger.warning("[SymbolMetadataResolver] provider request failed url=%s error=%s", url, exc)
            return None

        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            return [row for row in payload if isinstance(row, Mapping)]
        if isinstance(payload, Mapping):
            data = payload.get("data") or payload.get("tables") or []
            if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
                return [row for row in data if isinstance(row, Mapping)]
        return []


def resolve_symbol_metadata(symbol: str) -> SymbolMetadata:
    return _DEFAULT_RESOLVER.resolve(symbol)


def resolve_symbol_name(symbol: str) -> str | None:
    return _DEFAULT_RESOLVER.resolve_name(symbol)


def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def strip_symbol_suffix(symbol: str) -> str:
    return normalize_symbol(symbol).split(".")[0]


def detect_market(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized.endswith(".TWO"):
        return "TWO"
    return "TW"


def _row_stock_id(row: Mapping[str, Any]) -> str:
    for key in ("Code", "code", "股票代號", "SecuritiesCompanyCode", "代號", "stock_id", "StockID", "stock_no"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _row_stock_name(row: Mapping[str, Any]) -> str | None:
    for key in ("Name", "name", "股票名稱", "CompanyName", "SecuritiesCompanyName", "名稱", "stock_name"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _import_requests_get() -> RequestGetter:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests package is required for symbol metadata requests") from exc
    return requests.get


_DEFAULT_RESOLVER = SymbolMetadataResolver()
