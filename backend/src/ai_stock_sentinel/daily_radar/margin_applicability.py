"""Evidence for margin inapplicability; an absent report row alone is insufficient."""
from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

TWSE_NEW_LISTING_URL = "https://www.twse.com.tw/rwd/zh/company/newlisting"
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
    if (evidence.get("source_url") != TWSE_NEW_LISTING_URL
            or evidence.get("reason") != "initial_listing_under_six_months"
            or str(evidence.get("listing_type")) not in _INITIAL_LISTING_REMARKS):
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


def initial_listing_inapplicability(
    report: Mapping[str, Any], *, symbols: list[str], run_date: date,
) -> dict[str, dict[str, Any]]:
    if report.get("stat") != "OK":
        return {}
    fields, rows = report.get("fields"), report.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return {}
    required = ("公司代號", "股票上市買賣日期", "備註")
    if any(fields.count(field) != 1 for field in required):
        return {}
    indexes = [fields.index(field) for field in required]
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) <= max(indexes):
            continue
        stock_id, raw_date, remark = (str(row[index]).strip() for index in indexes)
        symbol = f"{stock_id}.TW"
        if symbol not in symbols:
            continue
        if symbol in seen:
            result.pop(symbol, None)
            continue
        seen.add(symbol)
        try:
            year, month, day = (int(part) for part in raw_date.replace("/", ".").split("."))
            listed = date(year + 1911, month, day)
        except ValueError:
            continue
        payload = {
            "applicability": "not_applicable",
            "eligibility": {
                "symbol": symbol,
                "source_url": TWSE_NEW_LISTING_URL,
                "reason": "initial_listing_under_six_months",
                "listing_type": remark,
                "listing_date": listed.isoformat(),
                "evaluated_for": run_date.isoformat(),
            },
        }
        if margin_is_not_applicable(payload, run_date=run_date, symbol=symbol):
            result[symbol] = payload
    return result
