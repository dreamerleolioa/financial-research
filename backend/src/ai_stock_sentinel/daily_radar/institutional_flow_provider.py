from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
import re
import time
from typing import Any, TypeVar

from ai_stock_sentinel.data_sources.official_http import official_request_get
from ai_stock_sentinel.daily_radar.institutional_flow import (
    InstitutionalFlowRow,
    InstitutionalReport,
)


TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"
TWSE_T86_FALLBACK_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_3I_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)

RequestGetter = Callable[..., Any]
Sleeper = Callable[[float], None]
NormalizedReport = TypeVar("NormalizedReport")


class OfficialInstitutionalReportError(RuntimeError):
    def __init__(self, code: str, *, market: str) -> None:
        super().__init__(code)
        self.code = code
        self.market = market


class OfficialTaiwanInstitutionalReportProvider:
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
        self._request_get = request_get or _default_request_get
        self._timeout = max(1, timeout)
        self._max_attempts = max(1, max_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._max_retry_delay_seconds = max(0.0, max_retry_delay_seconds)
        self._sleep = sleep

    def fetch_market(self, *, market: str, trade_date: date) -> InstitutionalReport:
        if market == "TW":
            return _request_and_normalize(
                self._request_get,
                urls=(TWSE_T86_URL, TWSE_T86_FALLBACK_URL),
                params={
                    "response": "json",
                    "date": trade_date.strftime("%Y%m%d"),
                    "selectType": "ALLBUT0999",
                },
                timeout=self._timeout,
                market=market,
                max_attempts=self._max_attempts,
                retry_backoff_seconds=self._retry_backoff_seconds,
                max_retry_delay_seconds=self._max_retry_delay_seconds,
                sleep=self._sleep,
                normalize=lambda payload: normalize_twse_institutional_report(
                    payload,
                    expected_date=trade_date,
                ),
            )
        if market == "TWO":
            return _request_and_normalize(
                self._request_get,
                urls=(TPEX_3I_URL,),
                params={
                    "l": "zh-tw",
                    "o": "json",
                    "d": (
                        f"{trade_date.year - 1911:03d}/"
                        f"{trade_date.month:02d}/{trade_date.day:02d}"
                    ),
                },
                timeout=self._timeout,
                market=market,
                max_attempts=self._max_attempts,
                retry_backoff_seconds=self._retry_backoff_seconds,
                max_retry_delay_seconds=self._max_retry_delay_seconds,
                sleep=self._sleep,
                normalize=lambda payload: normalize_tpex_institutional_report(
                    payload,
                    expected_date=trade_date,
                ),
            )
        raise OfficialInstitutionalReportError("unsupported_market", market=market)


def normalize_twse_institutional_report(
    payload: Mapping[str, Any],
    *,
    expected_date: date,
) -> InstitutionalReport:
    if str(payload.get("stat") or "").strip().lower() != "ok":
        raise OfficialInstitutionalReportError(
            "twse_institutional_response_error",
            market="TW",
        )
    try:
        payload_date = _compact_date(str(payload.get("date") or ""))
    except ValueError as exc:
        raise OfficialInstitutionalReportError(
            "twse_institutional_date_invalid",
            market="TW",
        ) from exc
    if payload_date != expected_date:
        raise OfficialInstitutionalReportError(
            "twse_institutional_date_mismatch",
            market="TW",
        )
    fields = _required_sequence(
        payload.get("fields"),
        code="twse_institutional_fields_invalid",
        market="TW",
    )
    expected_fields = {
        0: "證券代號",
        1: "證券名稱",
        4: "外陸資買賣超股數(不含外資自營商)",
        10: "投信買賣超股數",
        11: "自營商買賣超股數",
        18: "三大法人買賣超股數",
    }
    _validate_indexed_fields(
        fields,
        expected_fields=expected_fields,
        code="twse_institutional_schema_changed",
        market="TW",
    )
    _validate_report_row_count(
        payload.get("data"),
        declared_count=payload.get("total"),
        code="twse_institutional_row_count_mismatch",
        market="TW",
    )
    rows = _normalize_rows(
        payload.get("data"),
        market="TW",
        trade_date=payload_date,
        indexes={
            "code": 0,
            "name": 1,
            "foreign": 4,
            "trust": 10,
            "dealer": 11,
            "total": 18,
        },
    )
    return InstitutionalReport(
        market="TW",
        trade_date=payload_date,
        source_provider="twse",
        source_dataset="TWSE_T86",
        rows=rows,
    )


def normalize_tpex_institutional_report(
    payload: Mapping[str, Any],
    *,
    expected_date: date,
) -> InstitutionalReport:
    if str(payload.get("stat") or "").strip().lower() != "ok":
        raise OfficialInstitutionalReportError(
            "tpex_institutional_response_error",
            market="TWO",
        )
    table = _tpex_report_table(payload)
    try:
        payload_date = _roc_slash_date(str(table.get("date") or ""))
    except ValueError as exc:
        raise OfficialInstitutionalReportError(
            "tpex_institutional_date_invalid",
            market="TWO",
        ) from exc
    if payload_date != expected_date:
        raise OfficialInstitutionalReportError(
            "tpex_institutional_date_mismatch",
            market="TWO",
        )
    fields = _required_sequence(
        table.get("fields"),
        code="tpex_institutional_fields_invalid",
        market="TWO",
    )
    _validate_indexed_fields(
        fields,
        expected_fields={
            0: "代號",
            1: "名稱",
            4: "買賣超股數",
            13: "買賣超股數",
            22: "買賣超股數",
            23: "三大法人買賣超股數合計",
        },
        code="tpex_institutional_schema_changed",
        market="TWO",
    )
    _validate_report_row_count(
        table.get("data"),
        declared_count=table.get("totalCount"),
        code="tpex_institutional_row_count_mismatch",
        market="TWO",
    )
    rows = _normalize_rows(
        table.get("data"),
        market="TWO",
        trade_date=payload_date,
        indexes={
            "code": 0,
            "name": 1,
            "foreign": 4,
            "trust": 13,
            "dealer": 22,
            "total": 23,
        },
    )
    return InstitutionalReport(
        market="TWO",
        trade_date=payload_date,
        source_provider="tpex",
        source_dataset="TPEX_3ITRADE_HEDGE",
        rows=rows,
    )


def _normalize_rows(
    raw_rows: Any,
    *,
    market: str,
    trade_date: date,
    indexes: Mapping[str, int],
) -> tuple[InstitutionalFlowRow, ...]:
    rows = _required_sequence(
        raw_rows,
        code="institutional_report_rows_invalid",
        market=market,
    )
    max_index = max(indexes.values())
    suffix = ".TW" if market == "TW" else ".TWO"
    normalized: list[InstitutionalFlowRow] = []
    seen_symbols: set[str] = set()
    for raw_row in rows:
        if not _is_sequence(raw_row) or len(raw_row) <= max_index:
            raise OfficialInstitutionalReportError(
                "institutional_report_row_invalid",
                market=market,
            )
        stock_id = str(raw_row[indexes["code"]] or "").strip()
        if re.fullmatch(r"[1-9]\d{3}", stock_id) is None:
            continue
        symbol = f"{stock_id}{suffix}"
        if symbol in seen_symbols:
            raise OfficialInstitutionalReportError(
                "institutional_report_duplicate_symbol",
                market=market,
            )
        try:
            row = InstitutionalFlowRow(
                symbol=symbol,
                market=market,
                name=str(raw_row[indexes["name"]] or "").strip() or None,
                trade_date=trade_date,
                foreign_net_shares=_integer(raw_row[indexes["foreign"]]),
                investment_trust_net_shares=_integer(raw_row[indexes["trust"]]),
                dealer_net_shares=_integer(raw_row[indexes["dealer"]]),
                total_net_shares=_integer(raw_row[indexes["total"]]),
            )
        except (TypeError, ValueError) as exc:
            raise OfficialInstitutionalReportError(
                "institutional_report_row_invalid",
                market=market,
            ) from exc
        seen_symbols.add(symbol)
        normalized.append(row)
    if not normalized:
        raise OfficialInstitutionalReportError(
            "institutional_report_empty",
            market=market,
        )
    return tuple(normalized)


def _tpex_report_table(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    tables = payload.get("tables")
    if not _is_sequence(tables):
        raise OfficialInstitutionalReportError(
            "tpex_institutional_tables_invalid",
            market="TWO",
        )
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        title = str(table.get("title") or "")
        if "三大法人買賣明細" in title:
            return table
    raise OfficialInstitutionalReportError(
        "tpex_institutional_table_not_found",
        market="TWO",
    )


def _required_sequence(value: Any, *, code: str, market: str) -> Sequence[Any]:
    if not _is_sequence(value):
        raise OfficialInstitutionalReportError(code, market=market)
    return value


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _validate_indexed_fields(
    fields: Sequence[Any],
    *,
    expected_fields: Mapping[int, str],
    code: str,
    market: str,
) -> None:
    if len(fields) <= max(expected_fields) or any(
        _normalize_field(str(fields[index])) != _normalize_field(expected)
        for index, expected in expected_fields.items()
    ):
        raise OfficialInstitutionalReportError(code, market=market)


def _validate_report_row_count(
    raw_rows: Any,
    *,
    declared_count: Any,
    code: str,
    market: str,
) -> None:
    if not _is_sequence(raw_rows):
        raise OfficialInstitutionalReportError(
            "institutional_report_rows_invalid",
            market=market,
        )
    try:
        expected_count = _integer(declared_count)
    except (TypeError, ValueError) as exc:
        raise OfficialInstitutionalReportError(code, market=market) from exc
    if expected_count != len(raw_rows):
        raise OfficialInstitutionalReportError(code, market=market)


def _request_and_normalize(
    request_get: RequestGetter,
    *,
    urls: tuple[str, ...],
    params: Mapping[str, str],
    timeout: int,
    market: str,
    max_attempts: int,
    retry_backoff_seconds: float,
    max_retry_delay_seconds: float,
    sleep: Sleeper,
    normalize: Callable[[Mapping[str, Any]], NormalizedReport],
) -> NormalizedReport:
    for attempt in range(max_attempts):
        response: Any = None
        url = urls[min(attempt, len(urls) - 1)]
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
            if not isinstance(payload, Mapping):
                raise TypeError("official response is not an object")
            return normalize(payload)
        except Exception as exc:
            is_last_attempt = attempt + 1 >= max_attempts
            if is_last_attempt or not _is_retryable_request_failure(response):
                if isinstance(exc, OfficialInstitutionalReportError):
                    raise
                raise OfficialInstitutionalReportError(
                    "institutional_report_request_failed",
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
    raise OfficialInstitutionalReportError(
        "institutional_report_request_failed",
        market=market,
    )


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


def _default_request_get(url: str, **kwargs: Any) -> Any:
    return official_request_get(url, max_attempts=1, **kwargs)


def _normalize_field(value: str) -> str:
    return re.sub(r"<[^>]+>|\s+", "", value)


def _integer(value: Any) -> int:
    normalized = str(value).replace(",", "").strip()
    if not normalized or re.fullmatch(r"-?\d+", normalized) is None:
        raise ValueError("invalid integer")
    return int(normalized)


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


__all__ = [
    "OfficialInstitutionalReportError",
    "OfficialTaiwanInstitutionalReportProvider",
    "TPEX_3I_URL",
    "TWSE_T86_FALLBACK_URL",
    "TWSE_T86_URL",
    "normalize_tpex_institutional_report",
    "normalize_twse_institutional_report",
]
