from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any


@dataclass(frozen=True)
class NormalizedFundamentalPeriod:
    symbol: str
    market: str
    fiscal_year: int
    fiscal_quarter: int
    period_end: date
    statement_scope: str
    industry_schema: str
    cumulative_eps: Decimal | None
    quarter_eps: Decimal | None
    source_report_date: date | None
    availability_quality: str
    source_provider: str
    source_dataset: str
    payload_hash: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class NormalizedDividendEvent:
    symbol: str
    market: str
    dividend_year: int
    period_start: date | None
    period_end: date | None
    period_label: str | None
    sequence: str | None
    decision_status: str | None
    board_date: date | None
    shareholder_date: date | None
    ex_dividend_date: date | None
    earnings_cash_per_share: Decimal | None
    legal_reserve_cash_per_share: Decimal | None
    capital_reserve_cash_per_share: Decimal | None
    total_cash_per_share: Decimal | None
    source_provider: str
    source_dataset: str
    event_key: str
    payload_hash: str
    raw_payload: dict[str, Any]


def normalize_official_statement_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    market: str,
    industry_schema: str,
    source_dataset: str,
) -> list[NormalizedFundamentalPeriod]:
    suffix = ".TW" if market == "TW" else ".TWO"
    normalized: list[NormalizedFundamentalPeriod] = []
    for row in rows:
        stock_id = _first_text(row, "公司代號", "公司代碼", "SecuritiesCompanyCode", "Code")
        if re.fullmatch(r"[1-9]\d{3}", stock_id or "") is None:
            continue
        year_value = _first_text(row, "年度", "Year")
        quarter_value = _first_text(row, "季別", "Season")
        eps_value = _eps_value(row)
        try:
            fiscal_year = _calendar_year(year_value)
            fiscal_quarter = int(quarter_value or "")
        except ValueError:
            continue
        if fiscal_quarter not in {1, 2, 3, 4}:
            continue
        raw_payload = dict(row)
        normalized.append(
            NormalizedFundamentalPeriod(
                symbol=f"{stock_id}{suffix}",
                market=market,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                period_end=_quarter_end(fiscal_year, fiscal_quarter),
                statement_scope="consolidated",
                industry_schema=industry_schema,
                cumulative_eps=eps_value,
                quarter_eps=None,
                source_report_date=_optional_date(_first_text(row, "出表日期", "Date")),
                availability_quality="observed",
                source_provider="official_openapi",
                source_dataset=source_dataset,
                payload_hash=_payload_hash(raw_payload),
                raw_payload=raw_payload,
            )
        )
    return normalized


def normalize_finmind_statement_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
) -> list[NormalizedFundamentalPeriod]:
    market = "TWO" if symbol.upper().endswith(".TWO") else "TW"
    normalized: list[NormalizedFundamentalPeriod] = []
    for row in rows:
        if str(row.get("type") or "") != "EPS":
            continue
        try:
            period_end = date.fromisoformat(str(row.get("date") or "")[:10])
        except ValueError:
            continue
        quarter = {3: 1, 6: 2, 9: 3, 12: 4}.get(period_end.month)
        if quarter is None:
            continue
        raw_payload = dict(row)
        normalized.append(
            NormalizedFundamentalPeriod(
                symbol=symbol.upper(),
                market=market,
                fiscal_year=period_end.year,
                fiscal_quarter=quarter,
                period_end=_quarter_end(period_end.year, quarter),
                statement_scope="consolidated",
                industry_schema="finmind_bootstrap",
                cumulative_eps=None,
                quarter_eps=_decimal(row.get("value")),
                source_report_date=None,
                availability_quality="historical_unknown",
                source_provider="finmind_bootstrap",
                source_dataset="TaiwanStockFinancialStatements",
                payload_hash=_payload_hash(raw_payload),
                raw_payload=raw_payload,
            )
        )
    return normalized


def normalize_mops_historical_eps_payload(
    payload: Mapping[str, Any],
    *,
    symbol: str,
) -> list[NormalizedFundamentalPeriod]:
    normalized_symbol = symbol.strip().upper()
    matched_symbol = re.fullmatch(r"([1-9]\d{3})\.(TW|TWO)", normalized_symbol)
    if matched_symbol is None:
        raise ValueError("invalid Taiwan stock symbol")
    stock_id, market = matched_symbol.groups()

    axes = payload.get("xaxisList")
    graph_data = payload.get("graphData")
    if not _is_sequence(axes) or not _is_sequence(graph_data) or len(graph_data) != 1:
        raise ValueError("MOPS historical EPS response has an invalid graph contract")

    identity_values = next(
        (
            values
            for key in ("showNameList", "checkedNameList", "displayCompanyId")
            if _is_sequence(values := payload.get(key)) and values
        ),
        None,
    )
    if identity_values is None or not any(
        re.match(rf"^{re.escape(stock_id)}(?:\s|$)", str(value).strip())
        for value in identity_values
    ):
        raise ValueError("MOPS historical EPS response symbol mismatch")

    series = graph_data[0]
    if not isinstance(series, Mapping) or not _is_sequence(series.get("data")):
        raise ValueError("MOPS historical EPS response has an invalid data series")

    normalized: list[NormalizedFundamentalPeriod] = []
    seen_indexes: set[int] = set()
    for point in series["data"]:
        if not _is_sequence(point) or len(point) < 2:
            raise ValueError("MOPS historical EPS response has an invalid data point")
        index_value = point[0]
        if isinstance(index_value, bool):
            raise ValueError("MOPS historical EPS response has an invalid axis index")
        try:
            index = int(index_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("MOPS historical EPS response has an invalid axis index") from exc
        if index != index_value or index < 0 or index >= len(axes) or index in seen_indexes:
            raise ValueError("MOPS historical EPS response has an invalid axis index")
        seen_indexes.add(index)

        axis = str(axes[index]).strip()
        axis_match = re.fullmatch(r"(\d{4})Q([1-4])", axis)
        if axis_match is None:
            raise ValueError("MOPS historical EPS response has an invalid quarter axis")
        eps_value = _decimal(point[1])
        if eps_value is None:
            continue
        if not eps_value.is_finite():
            raise ValueError("MOPS historical EPS response has a non-finite EPS value")

        fiscal_year = int(axis_match.group(1))
        fiscal_quarter = int(axis_match.group(2))
        report_type = str(point[2]).strip() if len(point) >= 3 else None
        raw_payload = {
            "axis": axis,
            "value": str(point[1]),
            "report_type": report_type,
            "company_label": str(series.get("label") or "").strip() or None,
            "requested_symbol": normalized_symbol,
        }
        normalized.append(
            NormalizedFundamentalPeriod(
                symbol=normalized_symbol,
                market=market,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                period_end=_quarter_end(fiscal_year, fiscal_quarter),
                statement_scope="consolidated",
                industry_schema="mops_historical_eps",
                cumulative_eps=None,
                quarter_eps=eps_value,
                source_report_date=None,
                availability_quality="historical_unknown",
                source_provider="mops_historical",
                source_dataset="MOPS_FINANCIAL_COMPARISON_EPS",
                payload_hash=_payload_hash(raw_payload),
                raw_payload=raw_payload,
            )
        )
    return normalized


def normalize_twse_dividend_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[NormalizedDividendEvent]:
    normalized: list[NormalizedDividendEvent] = []
    for row in rows:
        stock_id = _first_text(row, "公司代號", "Code")
        if re.fullmatch(r"[1-9]\d{3}", stock_id or "") is None:
            continue
        try:
            dividend_year = _calendar_year(_first_text(row, "股利年度"))
        except ValueError:
            continue
        period_start, period_end = _period_range(_first_text(row, "股利所屬期間"))
        earnings = _decimal(row.get("股東配發-盈餘分配之現金股利(元/股)"))
        legal = _decimal(row.get("股東配發-法定盈餘公積發放之現金(元/股)"))
        capital = _decimal(row.get("股東配發-資本公積發放之現金(元/股)"))
        total = _sum_decimals(earnings, legal, capital)
        raw_payload = dict(row)
        sequence = _first_text(row, "期別")
        event_key = ":".join(
            part
            for part in (
                stock_id,
                str(dividend_year),
                period_start.isoformat() if period_start else None,
                period_end.isoformat() if period_end else None,
                sequence,
            )
            if part
        )
        normalized.append(
            NormalizedDividendEvent(
                symbol=f"{stock_id}.TW",
                market="TW",
                dividend_year=dividend_year,
                period_start=period_start,
                period_end=period_end,
                period_label=_first_text(row, "股利所屬年(季)度"),
                sequence=sequence,
                decision_status=_first_text(row, "決議（擬議）進度"),
                board_date=_optional_date(_first_text(row, "董事會（擬議）股利分派日")),
                shareholder_date=_optional_date(_first_text(row, "股東會日期")),
                ex_dividend_date=None,
                earnings_cash_per_share=earnings,
                legal_reserve_cash_per_share=legal,
                capital_reserve_cash_per_share=capital,
                total_cash_per_share=total,
                source_provider="twse",
                source_dataset="TWSE_t187ap45_L",
                event_key=event_key,
                payload_hash=_payload_hash(raw_payload),
                raw_payload=raw_payload,
            )
        )
    return normalized


def normalize_tpex_ex_dividend_payload(
    payload: Mapping[str, Any],
) -> list[NormalizedDividendEvent]:
    tables = payload.get("tables")
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)) or not tables:
        return []
    table = tables[0]
    if not isinstance(table, Mapping):
        return []
    fields = table.get("fields")
    data = table.get("data")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        return []
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        return []
    indexes = {_normalize_field(str(field)): index for index, field in enumerate(fields)}
    required = ("除權息日期", "代號", "現金股利")
    if any(_normalize_field(field) not in indexes for field in required):
        return []
    normalized: list[NormalizedDividendEvent] = []
    for row in data:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        try:
            stock_id = str(row[indexes["代號"]]).strip()
            ex_date = _required_date(str(row[indexes["除權息日期"]]))
            cash = _decimal(row[indexes["現金股利"]])
        except (IndexError, ValueError):
            continue
        if re.fullmatch(r"[1-9]\d{3}", stock_id) is None or cash is None:
            continue
        raw_payload = {
            str(field): row[index]
            for index, field in enumerate(fields)
            if index < len(row)
        }
        normalized.append(
            NormalizedDividendEvent(
                symbol=f"{stock_id}.TWO",
                market="TWO",
                dividend_year=ex_date.year,
                period_start=None,
                period_end=None,
                period_label="realized_ex_dividend_event",
                sequence=None,
                decision_status="realized",
                board_date=None,
                shareholder_date=None,
                ex_dividend_date=ex_date,
                earnings_cash_per_share=None,
                legal_reserve_cash_per_share=None,
                capital_reserve_cash_per_share=None,
                total_cash_per_share=cash,
                source_provider="tpex",
                source_dataset="TPEX_exDailyQ",
                event_key=f"{stock_id}:{ex_date.isoformat()}",
                payload_hash=_payload_hash(raw_payload),
                raw_payload=raw_payload,
            )
        )
    return normalized


def normalize_finmind_dividend_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
) -> list[NormalizedDividendEvent]:
    market = "TWO" if symbol.upper().endswith(".TWO") else "TW"
    normalized: list[NormalizedDividendEvent] = []
    for row in rows:
        try:
            event_date = date.fromisoformat(str(row.get("date") or "")[:10])
        except ValueError:
            continue
        cash = _decimal(row.get("CashEarningsDistribution"))
        if cash is None:
            continue
        raw_payload = dict(row)
        dividend_year, period_start, period_end, period_label = _finmind_dividend_period(
            row,
            event_date=event_date,
        )
        ex_dividend_date = _optional_date(_first_text(row, "CashExDividendTradingDate"))
        normalized.append(
            NormalizedDividendEvent(
                symbol=symbol.upper(),
                market=market,
                dividend_year=dividend_year,
                period_start=period_start,
                period_end=period_end,
                period_label=period_label,
                sequence=None,
                decision_status="historical_unknown",
                board_date=None,
                shareholder_date=None,
                ex_dividend_date=ex_dividend_date or event_date,
                earnings_cash_per_share=cash,
                legal_reserve_cash_per_share=None,
                capital_reserve_cash_per_share=None,
                total_cash_per_share=cash,
                source_provider="finmind_bootstrap",
                source_dataset="TaiwanStockDividend",
                event_key=f"{symbol.upper()}:{event_date.isoformat()}",
                payload_hash=_payload_hash(raw_payload),
                raw_payload=raw_payload,
            )
        )
    return normalized


def _eps_value(row: Mapping[str, Any]) -> Decimal | None:
    for key, value in row.items():
        normalized = _normalize_field(str(key)).lower()
        if "基本每股盈餘" in normalized or "basicearningspershare" in normalized:
            return _decimal(value)
    return None


def _first_text(row: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _calendar_year(value: str | None) -> int:
    year = int(str(value or "").strip())
    return year + 1911 if year < 1911 else year


def _quarter_end(year: int, quarter: int) -> date:
    return {
        1: date(year, 3, 31),
        2: date(year, 6, 30),
        3: date(year, 9, 30),
        4: date(year, 12, 31),
    }[quarter]


def _quarter_start(year: int, quarter: int) -> date:
    return {
        1: date(year, 1, 1),
        2: date(year, 4, 1),
        3: date(year, 7, 1),
        4: date(year, 10, 1),
    }[quarter]


def _finmind_dividend_period(
    row: Mapping[str, Any],
    *,
    event_date: date,
) -> tuple[int, date | None, date | None, str]:
    period_label = _first_text(row, "year")
    if period_label:
        matched = re.fullmatch(r"\s*(\d{2,4})\s*年(?:度)?(?:\s*第\s*([1-4])\s*季)?\s*", period_label)
        if matched:
            fiscal_year = _calendar_year(matched.group(1))
            quarter_text = matched.group(2)
            if quarter_text is None:
                return (
                    fiscal_year,
                    date(fiscal_year, 1, 1),
                    date(fiscal_year, 12, 31),
                    period_label,
                )
            quarter = int(quarter_text)
            return (
                fiscal_year,
                _quarter_start(fiscal_year, quarter),
                _quarter_end(fiscal_year, quarter),
                period_label,
            )
    return event_date.year, None, None, period_label or "finmind_period_unknown"


def _optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return _required_date(value)
    except ValueError:
        return None


def _required_date(value: str) -> date:
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) == 7:
        return date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7]))
    if len(digits) == 8:
        year = int(digits[:4])
        if year > 1911:
            return date(year, int(digits[4:6]), int(digits[6:8]))
    parts = [part for part in re.split(r"\D+", text) if part]
    if len(parts) >= 3:
        year = int(parts[0])
        return date(year + 1911 if year < 1911 else year, int(parts[1]), int(parts[2]))
    raise ValueError("invalid date")


def _period_range(value: str | None) -> tuple[date | None, date | None]:
    if not value or "~" not in value:
        return None, None
    start, end = value.split("~", 1)
    return _optional_date(start), _optional_date(end)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "--"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _sum_decimals(*values: Decimal | None) -> Decimal | None:
    available = [value for value in values if value is not None]
    return sum(available, Decimal("0")) if available else None


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_field(value: str) -> str:
    return re.sub(r"<[^>]+>|\s+", "", value)


__all__ = [
    "NormalizedDividendEvent",
    "NormalizedFundamentalPeriod",
    "normalize_finmind_dividend_rows",
    "normalize_finmind_statement_rows",
    "normalize_mops_historical_eps_payload",
    "normalize_official_statement_rows",
    "normalize_tpex_ex_dividend_payload",
    "normalize_twse_dividend_rows",
]
