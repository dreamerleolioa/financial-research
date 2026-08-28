from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ai_stock_sentinel.data_sources.official_http import official_request_get

logger = logging.getLogger(__name__)

TWSE_STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_MAINBOARD_DAILY_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TWSE_COMPANY_PROFILE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANY_PROFILE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
DEFAULT_SYMBOL_METADATA_CACHE_TTL_SECONDS = 60 * 60 * 12
DEFAULT_SYMBOL_METADATA_FAILURE_CACHE_TTL_SECONDS = 60 * 5

# TWSE/TPEx official security industry codes. The shared labels intentionally
# normalize equivalent listed/OTC categories so portfolio concentration groups
# them together. Code 91 identifies Taiwan Depositary Receipts rather than an
# industry and is intentionally left unclassified.
OFFICIAL_INDUSTRY_NAMES = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險",
    "18": "貿易百貨",
    "19": "綜合",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}

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
        self._name_cache: dict[str, tuple[float, str | None]] = {}
        self._industry_cache: dict[str, tuple[float, str | None]] = {}
        self._market_rows_cache: dict[str, tuple[float, list[Mapping[str, Any]]]] = {}

    def resolve(self, symbol: str) -> SymbolMetadata:
        normalized_symbol = normalize_symbol(symbol)
        return SymbolMetadata(
            symbol=normalized_symbol,
            name=self.resolve_name(normalized_symbol),
            market=detect_market(normalized_symbol),
        )

    def resolve_name(self, symbol: str) -> str | None:
        normalized_symbol = normalize_symbol(symbol)
        cached = self._name_cache.get(normalized_symbol)
        now = self._clock()
        if cached and cached[0] > now:
            return cached[1]

        stock_id = strip_symbol_suffix(normalized_symbol)
        market = detect_market(normalized_symbol)
        quote_rows, ttl_seconds = self._rows_for_market(market, dataset="quotes")
        name = next(
            (_row_stock_name(row) for row in quote_rows if _row_stock_id(row) == stock_id),
            None,
        )
        self._name_cache[normalized_symbol] = (now + ttl_seconds, name)
        return name

    def resolve_industry(self, symbol: str) -> str | None:
        normalized_symbol = normalize_symbol(symbol)
        cached = self._industry_cache.get(normalized_symbol)
        now = self._clock()
        if cached and cached[0] > now:
            return cached[1]

        stock_id = strip_symbol_suffix(normalized_symbol)
        market = detect_market(normalized_symbol)
        company_rows, ttl_seconds = self._rows_for_market(market, dataset="companies")
        industry = next(
            (
                OFFICIAL_INDUSTRY_NAMES.get(_row_industry_code(row))
                for row in company_rows
                if _row_stock_id(row) == stock_id
            ),
            None,
        )
        self._industry_cache[normalized_symbol] = (now + ttl_seconds, industry)
        return industry

    def _rows_for_market(
        self,
        market: str,
        *,
        dataset: str,
    ) -> tuple[list[Mapping[str, Any]], int]:
        now = self._clock()
        cache_key = f"{dataset}:{market}"
        cached = self._market_rows_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1], int(cached[0] - now)

        if dataset == "quotes":
            url = TWSE_STOCK_DAY_ALL_URL if market == "TW" else TPEX_MAINBOARD_DAILY_QUOTES_URL
        else:
            url = TWSE_COMPANY_PROFILE_URL if market == "TW" else TPEX_COMPANY_PROFILE_URL
        rows = self._fetch_rows(url)
        if rows is None:
            self._market_rows_cache[cache_key] = (
                now + DEFAULT_SYMBOL_METADATA_FAILURE_CACHE_TTL_SECONDS,
                [],
            )
            return [], DEFAULT_SYMBOL_METADATA_FAILURE_CACHE_TTL_SECONDS
        self._market_rows_cache[cache_key] = (now + self._ttl_seconds, rows)
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


def resolve_symbol_industry(symbol: str) -> str | None:
    return _DEFAULT_RESOLVER.resolve_industry(symbol)


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
    for key in (
        "Code",
        "code",
        "股票代號",
        "公司代號",
        "SecuritiesCompanyCode",
        "代號",
        "stock_id",
        "StockID",
        "stock_no",
    ):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _row_stock_name(row: Mapping[str, Any]) -> str | None:
    for key in (
        "Name",
        "name",
        "股票名稱",
        "公司簡稱",
        "CompanyAbbreviation",
        "CompanyName",
        "SecuritiesCompanyName",
        "名稱",
        "stock_name",
    ):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _row_industry_code(row: Mapping[str, Any]) -> str:
    for key in ("產業別", "SecuritiesIndustryCode", "industry_code"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().zfill(2)
    return ""


def _import_requests_get() -> RequestGetter:
    return official_request_get


_DEFAULT_RESOLVER = SymbolMetadataResolver()
