from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

from ai_stock_sentinel.clock import today_taipei
from ai_stock_sentinel.data_sources.official_http import (
    official_request_get,
    official_request_post,
)

TWSE_ACTIVE_ETF_LIST_URL = "https://www.twse.com.tw/rwd/zh/ETF/activeList?response=json"
MONEYDJ_HOLDINGS_URL_TEMPLATE = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={fund_code}.TW&page=1"
)
MONEYDJ_PARSER_VERSION = "moneydj-active-etf-holdings-v2"
NOMURA_PARSER_VERSION = "nomura-active-etf-holdings-v1"
NOMURA_TRADE_INFO_URL = (
    "https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundTradeInfo"
)
NOMURA_OFFICIAL_URL_TEMPLATE = (
    "https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo={fund_code}"
)
NOMURA_FUND_CODES = frozenset({"00980A", "00985A", "00999A"})
_ACTIVE_EQUITY_FUND_CODE_RE = re.compile(r"^\d{5}A$")
_DATA_DATE_RE = re.compile(r"資料日期\s*[:：]\s*(\d{4}/\d{2}/\d{2})")
_SAFE_SECURITY_CODE_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,20}$")
_NON_EQUITY_SUFFIXES = frozenset({"CUR", "FX", "TF"})
_MAX_HOLDING_TABLE_ROWS = 2_000
_MAX_HTML_CHARACTERS = 5_000_000
_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
_WEIGHT_QUANTUM = Decimal("0.0001")
_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/json",
    "User-Agent": "AI-Stock-Sentinel/1.0 (+personal research tracker)",
}


class ActiveEtfProviderError(RuntimeError):
    """Stable provider failure category. Raw exceptions are logged by the caller."""


@dataclass(frozen=True, slots=True)
class ActiveEtfFundDescriptor:
    fund_code: str
    name: str
    category: str
    source_url: str


@dataclass(frozen=True, slots=True)
class ActiveEtfHoldingRow:
    symbol: str
    name: str
    shares: int
    weight_pct: Decimal
    position_order: int


@dataclass(frozen=True, slots=True)
class ActiveEtfFundSnapshot:
    fund: ActiveEtfFundDescriptor
    data_date: date
    fetched_at: datetime
    holdings: tuple[ActiveEtfHoldingRow, ...]
    skipped_instrument_count: int
    payload_hash: str
    normalized_hash: str
    parser_version: str = MONEYDJ_PARSER_VERSION
    raw_payload: bytes = b""
    source_provider: str = "moneydj"
    source_url: str | None = None


class _HoldingsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, str | None]]]] = []
        self._table_depth = 0
        self._table: list[list[tuple[str, str | None]]] | None = None
        self._row: list[tuple[str, str | None]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self._table_depth == 0:
                self._table = []
            self._table_depth += 1
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_text = []
            self._cell_href = None
        elif tag == "a" and self._cell_text is not None:
            self._cell_href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._table_depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1:
            return
        if tag in {"td", "th"} and self._cell_text is not None and self._row is not None:
            text = " ".join("".join(self._cell_text).split())
            self._row.append((text, self._cell_href))
            self._cell_text = None
            self._cell_href = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None


def parse_twse_active_equity_funds(payload: object) -> list[ActiveEtfFundDescriptor]:
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ActiveEtfProviderError("active_etf_registry_invalid")
    fields = payload.get("fields")
    rows = payload.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        raise ActiveEtfProviderError("active_etf_registry_invalid")
    try:
        code_index = fields.index("證券代號")
        name_index = fields.index("證券簡稱")
        category_index = fields.index("ETF分類")
    except ValueError as exc:
        raise ActiveEtfProviderError("active_etf_registry_schema_changed") from exc

    funds: list[ActiveEtfFundDescriptor] = []
    seen_codes: set[str] = set()
    for raw_row in rows:
        if not isinstance(raw_row, list):
            raise ActiveEtfProviderError("active_etf_registry_row_invalid")
        try:
            fund_code = str(raw_row[code_index]).strip().upper()
            name = str(raw_row[name_index]).strip()
            category = str(raw_row[category_index]).strip()
        except IndexError as exc:
            raise ActiveEtfProviderError("active_etf_registry_row_invalid") from exc
        if not _ACTIVE_EQUITY_FUND_CODE_RE.fullmatch(fund_code):
            continue
        if not name or len(name) > 100 or fund_code in seen_codes:
            raise ActiveEtfProviderError("active_etf_registry_row_invalid")
        seen_codes.add(fund_code)
        funds.append(
            ActiveEtfFundDescriptor(
                fund_code=fund_code,
                name=name,
                category=category,
                source_url=MONEYDJ_HOLDINGS_URL_TEMPLATE.format(fund_code=fund_code),
            )
        )
    if not funds:
        raise ActiveEtfProviderError("active_etf_registry_empty")
    return sorted(funds, key=lambda fund: fund.fund_code)


def parse_moneydj_holdings_html(
    html: str,
    *,
    fund: ActiveEtfFundDescriptor,
    raw_payload: bytes | None = None,
    fetched_at: datetime | None = None,
) -> ActiveEtfFundSnapshot:
    payload_bytes = raw_payload if raw_payload is not None else html.encode("utf-8")
    if len(html) > _MAX_HTML_CHARACTERS or len(payload_bytes) > _MAX_HTML_CHARACTERS:
        raise ActiveEtfProviderError("active_etf_holdings_response_too_large")
    expected_heading = f"({fund.fund_code}.TW)-全部持股"
    if expected_heading not in html:
        raise ActiveEtfProviderError("active_etf_fund_identity_mismatch")
    date_match = _DATA_DATE_RE.search(html)
    if date_match is None:
        raise ActiveEtfProviderError("active_etf_data_date_missing")
    try:
        data_date = date.fromisoformat(date_match.group(1).replace("/", "-"))
    except ValueError as exc:
        raise ActiveEtfProviderError("active_etf_data_date_invalid") from exc
    observed_at = fetched_at or datetime.now(timezone.utc)
    if data_date > today_taipei(observed_at):
        raise ActiveEtfProviderError("active_etf_data_date_in_future")

    parser = _HoldingsTableParser()
    parser.feed(html)
    table_with_columns = next(
        (
            (_table, columns)
            for _table in parser.tables
            if (columns := _holdings_column_indexes(_table)) is not None
        ),
        None,
    )
    if table_with_columns is None:
        raise ActiveEtfProviderError("active_etf_holdings_table_missing")
    table, (name_index, weight_index, shares_index) = table_with_columns
    if len(table) - 1 > _MAX_HOLDING_TABLE_ROWS:
        raise ActiveEtfProviderError("active_etf_holdings_row_limit_exceeded")

    holdings: list[ActiveEtfHoldingRow] = []
    skipped_count = 0
    seen_symbols: set[str] = set()
    for row in table[1:]:
        if len(row) <= max(name_index, weight_index, shares_index):
            raise ActiveEtfProviderError("active_etf_holding_row_invalid")
        label, href = row[name_index]
        symbol = _security_code_from_href(href)
        if symbol is None or not _is_equity_security_code(symbol):
            skipped_count += 1
            continue
        if symbol in seen_symbols:
            raise ActiveEtfProviderError("active_etf_duplicate_holding")
        seen_symbols.add(symbol)
        name = _holding_name(label, symbol)
        shares = _parse_nonnegative_integer(row[shares_index][0])
        weight_pct = _parse_weight_pct(row[weight_index][0])
        holdings.append(
            ActiveEtfHoldingRow(
                symbol=symbol,
                name=name,
                shares=shares,
                weight_pct=weight_pct,
                position_order=len(holdings),
            )
        )
    if not holdings:
        raise ActiveEtfProviderError("active_etf_holdings_empty")

    raw_hash = hashlib.sha256(payload_bytes).hexdigest()
    normalized_payload = {
        "data_date": data_date.isoformat(),
        "fund_code": fund.fund_code,
        "holdings": [
            {
                "symbol": row.symbol,
                "name": row.name,
                "shares": row.shares,
                "weight_pct": str(row.weight_pct),
                "position_order": row.position_order,
            }
            for row in holdings
        ],
    }
    normalized_hash = hashlib.sha256(
        json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return ActiveEtfFundSnapshot(
        fund=fund,
        data_date=data_date,
        fetched_at=observed_at,
        holdings=tuple(holdings),
        skipped_instrument_count=skipped_count,
        payload_hash=raw_hash,
        normalized_hash=normalized_hash,
        raw_payload=payload_bytes,
        source_url=fund.source_url,
    )


def parse_nomura_holdings_payload(
    payload: object,
    *,
    fund: ActiveEtfFundDescriptor,
    raw_payload: bytes,
    fetched_at: datetime | None = None,
) -> ActiveEtfFundSnapshot:
    if len(raw_payload) > _MAX_HTML_CHARACTERS:
        raise ActiveEtfProviderError("active_etf_holdings_response_too_large")
    if not isinstance(payload, dict) or payload.get("StatusCode") != 0:
        raise ActiveEtfProviderError("active_etf_official_payload_invalid")
    entries = payload.get("Entries")
    if not isinstance(entries, dict) or entries.get("CFundId") != fund.fund_code:
        raise ActiveEtfProviderError("active_etf_fund_identity_mismatch")
    raw_date = entries.get("CNavDt") or entries.get("CNavDtStr")
    try:
        data_date = datetime.fromisoformat(str(raw_date)).date()
    except (TypeError, ValueError) as exc:
        raise ActiveEtfProviderError("active_etf_data_date_invalid") from exc
    observed_at = fetched_at or datetime.now(timezone.utc)
    if data_date > today_taipei(observed_at):
        raise ActiveEtfProviderError("active_etf_data_date_in_future")
    raw_rows = entries.get("Stocks")
    if not isinstance(raw_rows, list) or not raw_rows or len(raw_rows) > _MAX_HOLDING_TABLE_ROWS:
        raise ActiveEtfProviderError("active_etf_holdings_empty")

    holdings: list[ActiveEtfHoldingRow] = []
    seen_symbols: set[str] = set()
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            raise ActiveEtfProviderError("active_etf_holding_row_invalid")
        symbol = str(raw_row.get("CStockCode", "")).strip().upper()
        name = str(raw_row.get("CStockName", "")).strip()
        if not symbol or not _SAFE_SECURITY_CODE_RE.fullmatch(symbol) or len(name) > 100 or not name:
            raise ActiveEtfProviderError("active_etf_holding_row_invalid")
        if symbol in seen_symbols:
            raise ActiveEtfProviderError("active_etf_duplicate_holding")
        seen_symbols.add(symbol)
        shares = _parse_json_nonnegative_integer(raw_row.get("CQuantity"))
        weight_pct = _parse_json_weight_pct(raw_row.get("CWeightsPct"))
        holdings.append(
            ActiveEtfHoldingRow(
                symbol=symbol,
                name=name,
                shares=shares,
                weight_pct=weight_pct,
                position_order=len(holdings),
            )
        )
    return _build_official_snapshot(
        fund=fund,
        data_date=data_date,
        fetched_at=observed_at,
        holdings=holdings,
        raw_payload=raw_payload,
        parser_version=NOMURA_PARSER_VERSION,
        source_url=NOMURA_OFFICIAL_URL_TEMPLATE.format(fund_code=fund.fund_code),
    )


class IssuerOfficialActiveEtfProvider:
    source_provider = "issuer_official"

    def __init__(
        self,
        *,
        request_post: Callable[..., Any] | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self._request_post = request_post or official_request_post
        self._timeout_seconds = max(1, timeout_seconds)

    def supports(self, fund_code: str) -> bool:
        return fund_code in NOMURA_FUND_CODES

    def fetch_snapshot(
        self,
        fund: ActiveEtfFundDescriptor,
        *,
        expected_data_date: date | None = None,
    ) -> ActiveEtfFundSnapshot:
        if fund.fund_code in NOMURA_FUND_CODES:
            response = self._request_post(
                NOMURA_TRADE_INFO_URL,
                timeout=self._timeout_seconds,
                headers={**_REQUEST_HEADERS, "Content-Type": "application/json"},
                json={
                    "Type": 2,
                    "Keyword": fund.fund_code,
                    "FundNo": fund.fund_code,
                    "Date": today_taipei().isoformat(),
                },
            )
            _raise_for_status(response)
            response_content = getattr(response, "content", None)
            if isinstance(response_content, bytes) and len(response_content) > _MAX_HTML_CHARACTERS:
                raise ActiveEtfProviderError("active_etf_holdings_response_too_large")
            payload = response.json()
            raw_payload = (
                response_content
                if isinstance(response_content, bytes) and response_content
                else json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
            return parse_nomura_holdings_payload(
                payload,
                fund=fund,
                raw_payload=raw_payload,
            )
        raise ActiveEtfProviderError("active_etf_official_source_unsupported")


class MoneyDjActiveEtfProvider:
    source_provider = "moneydj"

    def __init__(
        self,
        *,
        request_get: Callable[..., Any] | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self._request_get = request_get or official_request_get
        self._timeout_seconds = max(1, timeout_seconds)

    def fetch_registry(self) -> list[ActiveEtfFundDescriptor]:
        try:
            response = self._request_get(
                TWSE_ACTIVE_ETF_LIST_URL,
                timeout=self._timeout_seconds,
                headers={**_REQUEST_HEADERS, "Accept": "application/json"},
            )
            _raise_for_status(response)
            payload = response.json()
        except ActiveEtfProviderError:
            raise
        except (TypeError, ValueError) as exc:
            raise ActiveEtfProviderError("active_etf_registry_invalid_json") from exc
        except Exception as exc:
            raise ActiveEtfProviderError("active_etf_registry_fetch_failed") from exc
        return parse_twse_active_equity_funds(payload)

    def fetch_snapshot(self, fund: ActiveEtfFundDescriptor) -> ActiveEtfFundSnapshot:
        response = self._request_get(
            fund.source_url,
            timeout=self._timeout_seconds,
            headers=_REQUEST_HEADERS,
        )
        _raise_for_status(response)
        response_content = getattr(response, "content", None)
        if isinstance(response_content, bytes) and len(response_content) > _MAX_HTML_CHARACTERS:
            raise ActiveEtfProviderError("active_etf_holdings_response_too_large")
        html = getattr(response, "text", None)
        if not isinstance(html, str) or not html.strip():
            raise ActiveEtfProviderError("active_etf_holdings_response_empty")
        return parse_moneydj_holdings_html(
            html,
            fund=fund,
            raw_payload=(
                response_content
                if isinstance(response_content, bytes) and response_content
                else None
            ),
        )


def _raise_for_status(response: Any) -> None:
    status_code = getattr(response, "status_code", None)
    try:
        normalized_status = int(status_code)
    except (TypeError, ValueError) as exc:
        raise ActiveEtfProviderError("active_etf_provider_status_invalid") from exc
    if normalized_status < 200 or normalized_status >= 300:
        raise ActiveEtfProviderError("active_etf_provider_http_error")


def _holdings_column_indexes(
    table: list[list[tuple[str, str | None]]],
) -> tuple[int, int, int] | None:
    if not table:
        return None
    headers = [cell[0] for cell in table[0]]
    name_index = next(
        (index for index, header in enumerate(headers) if header in {"個股名稱", "持股名稱"}),
        None,
    )
    weight_index = next(
        (index for index, header in enumerate(headers) if header.startswith("投資比例")),
        None,
    )
    shares_index = next(
        (index for index, header in enumerate(headers) if header == "持有股數"),
        None,
    )
    if name_index is None or weight_index is None or shares_index is None:
        return None
    return name_index, weight_index, shares_index


def _security_code_from_href(href: str | None) -> str | None:
    if not href:
        return None
    values = parse_qs(urlparse(href).query).get("etfid")
    if not values:
        return None
    symbol = values[0].strip().upper()
    return symbol or None


def _is_equity_security_code(symbol: str) -> bool:
    if not _SAFE_SECURITY_CODE_RE.fullmatch(symbol) or "*" in symbol:
        return False
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    return bool(suffix) and suffix not in _NON_EQUITY_SUFFIXES


def _holding_name(label: str, symbol: str) -> str:
    suffix = f"({symbol})"
    name = label[: -len(suffix)].strip() if label.endswith(suffix) else label.strip()
    if not name or len(name) > 100:
        raise ActiveEtfProviderError("active_etf_holding_name_invalid")
    return name


def _parse_nonnegative_integer(value: str) -> int:
    normalized = value.replace(",", "").strip()
    if not normalized.isdigit():
        raise ActiveEtfProviderError("active_etf_holding_shares_invalid")
    parsed = int(normalized)
    if parsed < 0 or parsed > _MAX_SAFE_JSON_INTEGER:
        raise ActiveEtfProviderError("active_etf_holding_shares_invalid")
    return parsed


def _parse_weight_pct(value: str) -> Decimal:
    try:
        parsed = Decimal(value.replace("%", "").replace(",", "").strip())
        if not parsed.is_finite() or parsed < 0 or parsed > 100:
            raise ActiveEtfProviderError("active_etf_holding_weight_invalid")
        return parsed.quantize(_WEIGHT_QUANTUM)
    except InvalidOperation as exc:
        raise ActiveEtfProviderError("active_etf_holding_weight_invalid") from exc


def _parse_json_nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ActiveEtfProviderError("active_etf_holding_shares_invalid")
    return _parse_nonnegative_integer(str(value))


def _parse_json_weight_pct(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ActiveEtfProviderError("active_etf_holding_weight_invalid")
    return _parse_weight_pct(str(value))


def _build_official_snapshot(
    *,
    fund: ActiveEtfFundDescriptor,
    data_date: date,
    fetched_at: datetime,
    holdings: list[ActiveEtfHoldingRow],
    raw_payload: bytes,
    parser_version: str,
    source_url: str,
) -> ActiveEtfFundSnapshot:
    normalized_payload = {
        "data_date": data_date.isoformat(),
        "fund_code": fund.fund_code,
        "holdings": [
            {
                "symbol": row.symbol,
                "name": row.name,
                "shares": row.shares,
                "weight_pct": str(row.weight_pct),
                "position_order": row.position_order,
            }
            for row in holdings
        ],
    }
    return ActiveEtfFundSnapshot(
        fund=fund,
        data_date=data_date,
        fetched_at=fetched_at,
        holdings=tuple(holdings),
        skipped_instrument_count=0,
        payload_hash=hashlib.sha256(raw_payload).hexdigest(),
        normalized_hash=hashlib.sha256(
            json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        parser_version=parser_version,
        raw_payload=raw_payload,
        source_provider="issuer_official",
        source_url=source_url,
    )


__all__ = [
    "MONEYDJ_PARSER_VERSION",
    "ActiveEtfFundDescriptor",
    "ActiveEtfFundSnapshot",
    "ActiveEtfHoldingRow",
    "ActiveEtfProviderError",
    "IssuerOfficialActiveEtfProvider",
    "MoneyDjActiveEtfProvider",
    "parse_moneydj_holdings_html",
    "parse_nomura_holdings_payload",
    "parse_twse_active_equity_funds",
]
