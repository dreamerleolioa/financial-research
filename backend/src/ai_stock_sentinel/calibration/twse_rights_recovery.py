"""Prove a cash-rights price conflict before using an entire TWSE price window."""

from datetime import date
from math import isclose
import re

from ai_stock_sentinel.calibration.forward_validation import number


TWSE = "https://www.twse.com.tw"
OHLC = ("open", "high", "low", "close")
CONFLICT_REASON = "conflicting_price_records"
PROVENANCE_KEY = "official_rights_recovery"
RESEARCH_VERSION = "daily-radar-next-open-price-v2"
EX_RIGHTS = "/rwd/zh/exRight/TWT49U"
RIGHTS_DETAIL = "/rwd/zh/exRight/TWT49UDetail"
REDUCTION = "/exchangeReport/TWTAUU"
FACE_VALUE = "/rwd/zh/change/TWTB8U"


def _report_rows(payload, required_fields):
    fields, data = payload.get("fields"), payload.get("data")
    if (str(payload.get("stat")).lower() != "ok" or not isinstance(fields, list)
            or not all(isinstance(f, str) for f in fields) or len(fields) != len(set(fields))
            or not set(required_fields).issubset(fields) or not isinstance(data, list)
            or any(not isinstance(row, list) or len(row) != len(fields) for row in data)):
        raise ValueError("twse_rights_invalid_report")
    return [dict(zip(fields, row)) for row in data]


def _dated_rows(payload, params, date_field, fields, *, allow_empty_status=False):
    # TWTAUU returns this exact successful empty response without date echoes.
    if allow_empty_status and payload == {"stat": "很抱歉，沒有符合條件的資料!"}:
        return []
    echoed = payload.get("params")
    if isinstance(echoed, dict):
        actual = echoed.get("startDate"), echoed.get("endDate")
    else:
        actual = payload.get("strDate"), payload.get("endDate")
    requested = params["startDate"], params["endDate"]
    if actual != requested:
        raise ValueError("twse_rights_unverified_report_range")
    results = []
    for row in _report_rows(payload, ["股票代號", date_field, *fields]):
        roc = str(row[date_field]).strip()
        parts = re.fullmatch(r"(\d{3})年(\d{2})月(\d{2})日", roc) or re.fullmatch(r"(\d{3})/(\d{2})/(\d{2})", roc)
        if not parts:
            raise ValueError("twse_rights_invalid_report_date")
        year, month, day = map(int, parts.groups())
        trade_date = date(year + 1911, month, day)
        if not requested[0] <= trade_date.strftime("%Y%m%d") <= requested[1]:
            raise ValueError("twse_rights_unverified_report_range")
        results.append((trade_date, row))
    return results


def _amount(cell, unit):
    match = re.fullmatch(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s+" + re.escape(unit), str(cell).strip())
    value = number(match[1].replace(",", "")) if match else None
    if value is None:
        raise ValueError("twse_rights_invalid_terms")
    return value


def _rights_issue(symbol, sessions, capture):
    code = symbol.split(".")[0]
    params = {"response": "json", "startDate": sessions[0].strftime("%Y%m%d"),
              "endDate": sessions[-1].strftime("%Y%m%d")}
    listing, listing_source = capture(EX_RIGHTS, params)
    events = [(day, row) for day, row in _dated_rows(listing, params, "資料日期",
              ["除權息前收盤價", "權/息", "詳細資料"]) if str(row["股票代號"]).strip() == code]
    if len(events) != 1 or events[0][0] not in sessions or events[0][1]["權/息"] != "權":
        raise ValueError("twse_rights_single_cash_issue_required")
    ex_date, notice = events[0]
    if str(notice["詳細資料"]).strip() != f"{code},{ex_date:%Y%m%d}":
        raise ValueError("twse_rights_detail_identity_mismatch")
    previous_close = number(str(notice["除權息前收盤價"]).replace(",", ""))
    if previous_close is None or previous_close <= 0:
        raise ValueError("twse_rights_invalid_terms")

    detail, detail_source = capture(RIGHTS_DETAIL, {"response": "json", "STK_NO": code, "T1": ex_date.strftime("%Y%m%d")})
    dividend, free, bonus = "(每股配發現金股利)除息", "A. 按普通股股東持股比例每千股無償配股", "B. 員工紅利轉增資"
    issued, subscription, ratio = "C. (有償) 現金增資", "每股認購金額", "按股東持股比例每千股認購"
    details = _report_rows(detail, ["股票代號", dividend, free, bonus, issued, subscription, ratio])
    if len(details) != 1 or str(details[0]["股票代號"]).strip() != code:
        raise ValueError("twse_rights_detail_identity_mismatch")
    terms = details[0]
    cash_dividend = _amount(terms[dividend], "元／股")
    free_shares = _amount(terms[free], "股")
    subscription_price = _amount(terms[subscription], "元／股")
    subscription_per_thousand = _amount(terms[ratio], "股")
    if (cash_dividend or free_shares or _amount(terms[bonus], "股")
            or min(_amount(terms[issued], "股"), subscription_price, subscription_per_thousand) <= 0):
        raise ValueError("twse_rights_single_cash_issue_required")

    action_sources = [listing_source]
    for report in (REDUCTION, FACE_VALUE):
        payload, source = capture(report, params)
        rows = _dated_rows(payload, params, "恢復買賣日期", [], allow_empty_status=report == REDUCTION)
        if any(str(row["股票代號"]).strip() == code for _, row in rows):
            raise ValueError("twse_rights_other_corporate_action")
        action_sources.append(source)
    return {
        "type": "cash_rights_issue", "symbol": symbol, "ex_date": ex_date.isoformat(),
        "previous_close": previous_close, "subscription_price": subscription_price,
        "subscription_shares_per_thousand": subscription_per_thousand,
        "cash_dividend_per_share": cash_dividend, "free_shares_per_thousand": free_shares, **detail_source,
    }, action_sources


def _daily_rows(rows, expected_dates, source):
    by_date = {}
    expected = {day.isoformat() for day in expected_dates}
    for row in rows:
        day = row.get("date")
        values = [number(row.get(field)) for field in OHLC]
        if (day not in expected or day in by_date or row.get("price_source") != source
                or row.get("ohlc_conflict_fields") or any(value is None for value in values)):
            raise ValueError("twse_rights_invalid_price_row")
        open_, high, low, close = values
        if not 0 < low <= min(open_, close) <= max(open_, close) <= high:
            raise ValueError("twse_rights_invalid_price_row")
        by_date[day] = row | dict(zip(OHLC, values))
    if by_date.keys() != expected:
        raise ValueError("twse_rights_incomplete_price_window")
    return by_date


def _unadjusted_rows(rows, action_source):
    return [row | {"price_basis": "unadjusted_price", "corporate_actions_checked": True,
                   "corporate_action_source": action_source, "dividends": 0, "stock_splits": 0}
            for row in rows]


def recover_window(symbol, signal, sessions, *, yahoo_rows, benchmark_rows, flagged_dates, capture, load_history):
    event, sources = _rights_issue(symbol, sessions, capture)
    target = sessions[-1]
    all_dates = (signal, *sessions)
    official_stock = _daily_rows(load_history(symbol, start_date=signal, end_date=target), all_dates, "twse_stock_day")
    official_index = _daily_rows(load_history("TAIEX", start_date=signal, end_date=target), all_dates, "twse_index_history")
    for row in benchmark_rows:
        official = official_index.get(row.get("date"))
        if official and (row.get("ohlc_conflict_fields") or any(
                number(row.get(field)) is not None
                and not isclose(number(row[field]), official[field], rel_tol=1e-7, abs_tol=0.005)
                for field in OHLC)):
            raise ValueError("twse_rights_unexplained_benchmark_conflict")
    # Yahoo dates absent from the official calendar do not establish a session.
    dates = {day.isoformat() for day in sessions}
    yahoo = _daily_rows((row for row in yahoo_rows if row.get("date") in dates), sessions, "yfinance")
    ex_day = event["ex_date"]
    prior_close = official_stock[max(day for day in official_stock if day < ex_day)]["close"]
    if not isclose(prior_close, event["previous_close"], rel_tol=0, abs_tol=0.005):
        raise ValueError("twse_rights_reference_close_mismatch")
    rate = event["subscription_shares_per_thousand"] / 1000
    factor = (prior_close + event["subscription_price"] * rate) / (1 + rate) / prior_close
    adjusted = []
    for day, observed in sorted(yahoo.items()):
        raw = official_stock[day]
        if (observed.get("stock_splits") not in (None, 0, 1) or observed.get("dividends") not in (None, 0)):
            raise ValueError("twse_rights_unexpected_corporate_action")
        if all(isclose(raw[field], observed[field], rel_tol=1e-7, abs_tol=0.005) for field in OHLC):
            continue
        if day >= ex_day or not all(
                isclose(raw[field] * factor, observed[field], rel_tol=1e-7, abs_tol=0.00005) for field in OHLC):
            raise ValueError("twse_rights_unexplained_price_conflict")
        adjusted.append(day)
    if not adjusted or not flagged_dates.issubset(adjusted):
        raise ValueError("twse_rights_unexplained_price_conflict")

    months = sorted({day.strftime("%Y%m01") for day in all_dates})
    evidence = {
        "evidence_version": "twse-cash-rights-v1",
        "event": event, "window_action_sources": sources,
        "price_sources": {
            "stock": [f"{TWSE}/exchangeReport/STOCK_DAY?response=json&date={month}&stockNo={symbol.split('.')[0]}"
                      for month in months],
            "benchmark": [f"{TWSE}/indicesReport/MI_5MINS_HIST?response=json&date={month}" for month in months],
        },
        "session_dates": sorted(dates), "yahoo_factor": factor, "yahoo_adjusted_dates": adjusted,
        "rights_value_included": False, "subscription_cash_flow_included": False,
    }
    return (_unadjusted_rows((official_stock[day] for day in sorted(dates)), "twse_official_rights_window"),
            _unadjusted_rows(official_index.values(), "index_not_applicable"), evidence)
