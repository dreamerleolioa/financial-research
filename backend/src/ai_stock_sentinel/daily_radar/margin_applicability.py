"""Evidence for margin inapplicability; an absent report row alone is insufficient."""
from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

TWSE_NEW_LISTING_URL = "https://www.twse.com.tw/rwd/zh/company/newlisting"
TWSE_PUBLIC_OFFERING_URL = "https://www.twse.com.tw/announcement/publicForm"
TWSE_CREDIT_STATUS_URL = "https://www.twse.com.tw/exchangeReport/TWT93U"
# 僅接受官方明確標示的第一上市；轉上市及其他註記不能用此規則推定資格。
_INITIAL_LISTING_REMARKS = {"第一上市", "創新板第一上市"}


def margin_is_not_applicable(
    payload: Mapping[str, Any],
    *,
    run_date: date | None = None,
    symbol: str | None = None,
) -> bool:
    evidence = payload.get("eligibility")
    if payload.get("applicability") != "not_applicable" or not isinstance(evidence, Mapping):
        return False
    source = evidence.get("source_url")
    if source == TWSE_CREDIT_STATUS_URL:
        try:
            reported = date.fromisoformat(str(evidence.get("report_date")))
            evaluated = date.fromisoformat(str(evidence.get("evaluated_for")))
        except ValueError:
            return False
        return (
            evidence.get("reason") == "official_credit_trading_ineligible"
            and evidence.get("credit_status") == "Y"
            and str(evidence.get("symbol", "")).endswith(".TW")
            and (symbol is None or evidence.get("symbol") == symbol)
            and reported == evaluated
            and (run_date is None or evaluated == run_date)
        )
    if source == TWSE_NEW_LISTING_URL:
        allowed_types, suffix = _INITIAL_LISTING_REMARKS, ".TW"
    elif source == TWSE_PUBLIC_OFFERING_URL:
        allowed_types = {"初上市", "初上櫃"}
        suffix = ".TW" if evidence.get("listing_type") == "初上市" else ".TWO"
    else:
        return False
    if (evidence.get("reason") != "initial_listing_under_six_months"
            or not isinstance(evidence.get("listing_type"), str)
            or evidence["listing_type"] not in allowed_types
            or not str(evidence.get("symbol", "")).endswith(suffix)):
        return False
    if symbol is not None and evidence.get("symbol") != symbol:
        return False
    try:
        listed = date.fromisoformat(str(evidence.get("listing_date")))
        evaluated = date.fromisoformat(str(evidence.get("evaluated_for")))
        month_index = listed.year * 12 + listed.month - 1 + 6
        year, month = divmod(month_index, 12)
        month += 1
        anniversary = date(year, month, min(listed.day, monthrange(year, month)[1]))
    except ValueError:
        return False
    return (run_date is None or evaluated == run_date) and listed <= evaluated < anniversary


def credit_trading_inapplicability(
    report: Mapping[str, Any], *, symbols: list[str], run_date: date,
) -> dict[str, dict[str, Any]]:
    """Accept only dated, explicit TWSE Y status; missing rows prove nothing."""
    if report.get("stat") != "OK" or report.get("date") != run_date.strftime("%Y%m%d"):
        return {}
    fields, rows, notes = report.get("fields"), report.get("data"), report.get("notes")
    if not isinstance(fields, list) or not isinstance(rows, list) or not isinstance(notes, list):
        return {}
    if not any(isinstance(note, str) and "Y-未取得信用交易資格" in note for note in notes):
        return {}
    fields = [str(field).strip() for field in fields]
    if any(fields.count(field) != 1 for field in ("代號", "備註")):
        return {}
    id_index, status_index = fields.index("代號"), fields.index("備註")
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) <= id_index:
            continue
        symbol = f"{str(row[id_index]).strip()}.TW"
        if symbol not in symbols:
            continue
        if symbol in seen:
            result.pop(symbol, None)
            continue
        seen.add(symbol)
        if len(row) <= status_index or str(row[status_index]).strip() != "Y":
            continue
        result[symbol] = {
            "applicability": "not_applicable",
            "eligibility": {
                "symbol": symbol,
                "source_url": TWSE_CREDIT_STATUS_URL,
                "reason": "official_credit_trading_ineligible",
                "credit_status": "Y",
                "report_date": run_date.isoformat(),
                "evaluated_for": run_date.isoformat(),
            },
        }
    return result


def initial_listing_inapplicability(
    report: Mapping[str, Any], *, symbols: list[str], run_date: date,
    market_code: str = "TW",
    source_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    if report.get("stat") != "OK":
        return {}
    fields, rows = report.get("fields"), report.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return {}
    if market_code not in {"TW", "TWO"}:
        return {}
    source_url = source_url or (TWSE_NEW_LISTING_URL if market_code == "TW"
                                else TWSE_PUBLIC_OFFERING_URL)
    if source_url == TWSE_NEW_LISTING_URL and market_code == "TW":
        required = ("公司代號", "股票上市買賣日期", "備註")
    elif source_url == TWSE_PUBLIC_OFFERING_URL:
        required = ("證券代號", "撥券日期(上市、上櫃日期)", "發行市場", "取消公開抽籤")
    else:
        return {}
    fields = [str(field).strip() for field in fields]
    if any(fields.count(field) != 1 for field in required):
        return {}
    indexes = [fields.index(field) for field in required]
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) <= max(indexes):
            continue
        stock_id, raw_date, remark, *cancelled = (str(row[index]).strip() for index in indexes)
        symbol = f"{stock_id}.{market_code}"
        if symbol not in symbols:
            continue
        if symbol in seen:
            result.pop(symbol, None)
            continue
        seen.add(symbol)
        if cancelled and cancelled[0]:
            continue
        try:
            year, month, day = (int(part) for part in raw_date.replace("/", ".").split("."))
            listed = date(year + 1911, month, day)
        except ValueError:
            continue
        if source_url == TWSE_PUBLIC_OFFERING_URL and str(report.get("date")) != str(listed.year):
            continue
        payload = {
            "applicability": "not_applicable",
            "eligibility": {
                "symbol": symbol,
                "source_url": source_url,
                "reason": "initial_listing_under_six_months",
                "listing_type": remark,
                "listing_date": listed.isoformat(),
                "evaluated_for": run_date.isoformat(),
            },
        }
        if margin_is_not_applicable(payload, run_date=run_date, symbol=symbol):
            result[symbol] = payload
    return result
