"""Prove stock distributions and cash-reduction halts for next-open skips."""

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from math import isclose
import re

from ai_stock_sentinel.calibration import twse_rights_recovery as rights
from ai_stock_sentinel.calibration.forward_validation import number


PROVENANCE_KEY = "official_corporate_action"
STOCK_DISTRIBUTION = "stock_distribution_in_window"
CASH_REDUCTION_HALT = "cash_reduction_halt_in_window"
TERMINAL_REASONS = frozenset({STOCK_DISTRIBUTION, CASH_REDUCTION_HALT})
EVIDENCE_VERSION = "next-open-official-corporate-action-v1"
TPEX_DISTRIBUTIONS = "/www/zh-tw/bulletin/exDailyQ"
TPEX_REDUCTIONS = "/www/zh-tw/bulletin/revivt"
TWSE_REDUCTION_DETAIL = "/rwd/zh/reducation/TWTAVUDetail"


@dataclass(frozen=True)
class Action:
    reason: str
    effective_date: date
    share_ratio: float
    cash_per_share: float
    previous_close: float
    halt_start: date | None = None


def _numeric(value):
    parsed = number(str(value).replace(",", ""))
    if parsed is None or parsed < 0:
        raise ValueError("official_corporate_action_invalid_terms")
    return parsed


def _twse_action(symbol, sessions, capture):
    code = symbol.split(".")[0]
    params = {"response": "json", "startDate": sessions[0].strftime("%Y%m%d"),
              "endDate": sessions[-1].strftime("%Y%m%d")}
    listing, source = capture(rights.EX_RIGHTS, params)
    events = [(day, row) for day, row in rights._dated_rows(listing, params, "資料日期",
              ["除權息前收盤價", "權/息", "詳細資料"]) if str(row["股票代號"]).strip() == code]
    sources = [source]
    reductions = []
    for path in (rights.REDUCTION, rights.FACE_VALUE):
        report, report_source = capture(path, params)
        fields = ["停止買賣前收盤價格", "減資原因", "詳細資料"] if path == rights.REDUCTION else []
        rows = rights._dated_rows(report, params, "恢復買賣日期", fields, allow_empty_status=path == rights.REDUCTION)
        matched = [(day, row) for day, row in rows if str(row["股票代號"]).strip() == code]
        if path == rights.FACE_VALUE and matched:
            return None, []
        reductions.extend(matched)
        sources.append(report_source)
    if len(events) + len(reductions) != 1:
        return None, []
    if reductions:
        resume, notice = reductions[0]
        action, detail_source = _twse_reduction(code, resume, notice, capture)
        return action, [*sources, detail_source] if action is not None else []

    day, notice = events[0]
    if notice["權/息"] not in {"權", "權息"}:
        return None, []
    if str(notice["詳細資料"]).strip() != f"{code},{day:%Y%m%d}":
        raise ValueError("official_corporate_action_identity_mismatch")
    detail, detail_source = capture(rights.RIGHTS_DETAIL,
        {"response": "json", "STK_NO": code, "T1": day.strftime("%Y%m%d")})
    dividend = "(每股配發現金股利)除息"
    free = "A. 按普通股股東持股比例每千股無償配股"
    other = {"B. 員工紅利轉增資": "股", "C. (有償) 現金增資": "股",
             "每股認購金額": "元／股", "按股東持股比例每千股認購": "股"}
    terms = rights._report_rows(detail, ["股票代號", dividend, free, *other])
    if len(terms) != 1 or str(terms[0]["股票代號"]).strip() != code:
        raise ValueError("official_corporate_action_identity_mismatch")
    row = terms[0]
    shares = rights._amount(row[free], "股")
    if not shares or any(rights._amount(row[field], unit) for field, unit in other.items()):
        return None, []
    return Action(STOCK_DISTRIBUTION, day, 1 + shares / 1000,
                  rights._amount(row[dividend], "元／股"),
                  _numeric(notice["除權息前收盤價"])), [*sources, detail_source]


def _twse_reduction(code, resume, notice, capture):
    if notice["減資原因"] != "退還股款":
        return None, None
    identifier, filed = (field.strip() for field in str(notice["詳細資料"]).split(","))
    if identifier != code or len(filed) != 8 or not filed.isascii() or not filed.isdecimal():
        raise ValueError("official_corporate_action_identity_mismatch")
    detail, source = capture(TWSE_REDUCTION_DETAIL, {"response": "json", "STK_NO": code, "FILE_DATE": filed})
    stop, shares, refund = "停止買賣日期：", "每壹仟股換發新股票：", "每股退還股款："
    other = {"原股每股配發現金股利：": "元/股", "減資並(有償)現金增資：": "股",
             "每股認購金額：": "元/股", "按股東持股比例每千股認購：": "股"}
    rows = rights._report_rows(detail, ["股票代號：", stop, shares, refund, *other])
    if len(rows) != 1 or str(rows[0]["股票代號："]).strip() != code:
        raise ValueError("official_corporate_action_identity_mismatch")
    row = rows[0]
    stop_date = _roc_date(row[stop])
    if filed > stop_date.strftime("%Y%m%d") or any(rights._amount(row[f], unit) for f, unit in other.items()):
        return None, None
    action = _cash_reduction(resume, stop_date, rights._amount(row[shares], "股") / 1000,
                            rights._amount(row[refund], "元/股"), notice["停止買賣前收盤價格"])
    return action, source


class _DetailCells(HTMLParser):
    """Read only text from the exchange's inline detail table."""

    def __init__(self, html):
        super().__init__()
        self.cells = []
        self.cell = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in {"th", "td"}:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            self.cells.append("".join(self.cell).strip())
            self.cell = None

    def terms(self):
        keys = self.cells[::2]
        if len(self.cells) % 2 or len(keys) != len(set(keys)):
            raise ValueError("official_corporate_action_invalid_reduction_detail")
        return dict(zip(keys, self.cells[1::2]))


def _roc_date(value):
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{3}(?:/[0-9]{2}/[0-9]{2}|[0-9]{4})", text):
        raise ValueError("official_corporate_action_invalid_date")
    text = text.replace("/", "")
    return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:]))


def _tpex_events(capture, path, sessions, code, date_field, code_field, fields):
    start, end = sessions[0], sessions[-1]
    params = {"response": "json", "startDate": start.strftime("%Y/%m/%d"),
              "endDate": end.strftime("%Y/%m/%d")}
    payload, source = capture(path, params)
    if payload.get("date") != f"{start:%Y%m%d}~{end:%Y%m%d}":
        raise ValueError("official_corporate_action_unverified_report_range")
    rows = tpex_report_rows(payload, [date_field, code_field, *fields])
    events = [(_roc_date(row[date_field]), row) for row in rows if str(row[code_field]).strip() == code]
    if any(not start <= day <= end for day, _ in events):
        raise ValueError("official_corporate_action_unverified_report_range")
    return events, source


def tpex_report_rows(payload, fields):
    tables = payload.get("tables")
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        raise ValueError("official_tpex_invalid_report")
    table = tables[0]
    rows = rights._report_rows(table | {"stat": payload.get("stat")}, fields)
    count = table.get("totalCount")
    if type(count) is not int or count != len(rows):
        raise ValueError("official_tpex_incomplete_report")
    return rows


def _tpex_action(symbol, sessions, capture):
    code = symbol.split(".")[0]
    cash_issue = ("現金增資股數", "現金增資認購價", "按持股比例仟股認購")
    distributions, distribution_source = _tpex_events(capture, TPEX_DISTRIBUTIONS, sessions, code,
        "除權息日期", "代號", ["除權息前收盤價", "權/息", "現金股利", "每仟股無償配股", *cash_issue])
    reductions, reduction_source = _tpex_events(capture, TPEX_REDUCTIONS, sessions, code,
        "恢復買賣日期", "股票代號", ["最後交易日之收盤價格", "減資原因", "詳細資料"])
    sources = [distribution_source, reduction_source]
    if len(distributions) + len(reductions) != 1:
        return None, []
    if distributions:
        day, row = distributions[0]
        shares = _numeric(row["每仟股無償配股"])
        if row["權/息"] not in {"除權", "除權息"} or not shares or any(_numeric(row[f]) for f in cash_issue):
            return None, []
        return Action(STOCK_DISTRIBUTION, day, 1 + shares / 1000,
                      _numeric(row["現金股利"]), _numeric(row["除權息前收盤價"])), sources

    resume, row = reductions[0]
    if row["減資原因"] != "現金減資":
        return None, []
    detail = _DetailCells(str(row["詳細資料"])).terms()
    if (detail["股票代號/股票名稱:"].split("/")[0].strip() != code
            or _roc_date(detail["恢復買賣日期:"]) != resume):
        raise ValueError("official_corporate_action_identity_mismatch")
    if any(detail[key] != "NA" for key in ("現金增資總股數:", "現金增資認購價:", "現金增資配股率:")):
        return None, []
    ratio = rights._amount(detail["每壹仟股換發新股票:"], "股") / 1000
    refund = rights._amount(detail["每股退還股款:"], "元/股")
    stop = _roc_date(detail["停止買賣日期:"])
    return _cash_reduction(resume, stop, ratio, refund, row["最後交易日之收盤價格"]), sources


def _cash_reduction(resume, stop, ratio, refund, previous_close):
    if not 0 < ratio < 1 or refund <= 0 or stop >= resume:
        raise ValueError("official_corporate_action_invalid_terms")
    return Action(CASH_REDUCTION_HALT, resume, ratio, refund, _numeric(previous_close), stop)


def _match_ohlc(row, reference, factor=1, tolerance=0.005):
    return all(isclose(row[field], reference[field] * factor, rel_tol=1e-7, abs_tol=tolerance)
               for field in rights.OHLC)


def prove_window(symbol, signal, window_days, as_of_date, *, yahoo_rows, benchmark_rows, marked_rows, provider):
    """Return evidence only when every discrepancy and missing session is explained."""
    index_rows = provider.official_research_history("TAIEX", start_date=signal, end_date=as_of_date,
                                                    max_rows=window_days + 1)
    expected = tuple(date.fromisoformat(row["date"]) for row in index_rows)
    if len(expected) != window_days + 1 or expected[0] != signal:
        raise ValueError("official_corporate_action_incomplete_calendar")
    sessions = expected[1:]
    target = sessions[-1]
    official_index = rights._daily_rows(index_rows, expected, "twse_index_history")
    listed = symbol.endswith(".TW")
    capture = provider.capture_twse_report if listed else provider.capture_tpex_report
    action, sources = (_twse_action if listed else _tpex_action)(symbol, sessions, capture)
    if action is None:
        return None
    if action.effective_date not in sessions or (action.halt_start and action.halt_start not in sessions):
        raise ValueError("official_corporate_action_event_outside_sessions")
    days = {str(day) for day in expected}
    halt = {str(day) for day in sessions if action.halt_start and action.halt_start <= day < action.effective_date}
    active = [day for day in expected if str(day) not in halt]
    official_stock = rights._daily_rows(
        provider.official_research_history(symbol, start_date=signal, end_date=target),
        active, "twse_stock_day" if listed else "tpex_trading_stock")
    benchmark = [row for row in benchmark_rows if str(signal) <= row["date"] <= str(target)]
    if len(benchmark) != len(days) or {row["date"] for row in benchmark} != days or any(
            row.get("ohlc_conflict_fields") or
            any(number(row.get(f)) is None for f in rights.OHLC)
            or not _match_ohlc({f: number(row[f]) for f in rights.OHLC}, official_index[row["date"]])
            for row in benchmark):
        raise ValueError("official_corporate_action_unexplained_benchmark")
    if any(row.get("date") in halt and (number(row.get("volume")) != 0 or row.get("ohlc_conflict_fields"))
           for row in yahoo_rows):
        raise ValueError("official_corporate_action_unverified_halt")
    active_days = set(official_stock)
    yahoo = rights._daily_rows((row for row in yahoo_rows if row.get("date") in active_days), active, "yfinance")
    event_date = str(action.effective_date)
    previous = max(day for day in official_stock if day < event_date)
    if not isclose(official_stock[previous]["close"], action.previous_close, rel_tol=0, abs_tol=0.005):
        raise ValueError("official_corporate_action_reference_close_mismatch")
    adjusted = []
    factor = 1 / action.share_ratio
    event_days = {event_date}
    if action.halt_start:
        event_days.add(str(action.halt_start))
    split_seen = cash_seen = False
    for row in yahoo_rows:
        day = row.get("date")
        split, cash = number(row.get("stock_splits")), number(row.get("dividends"))
        if day not in days:
            if (isinstance(day, str) and str(signal) < day < str(target)
                    and (number(row.get("volume")) != 0 or split not in (0, 1) or cash != 0)):
                raise ValueError("official_corporate_action_unverified_market_session")
            continue
        has_split, has_cash = split not in (0, 1), cash != 0
        if (has_split and (day not in event_days or split_seen or split is None
                           or not isclose(split, action.share_ratio))
                or has_cash and (day not in event_days or cash_seen or cash is None
                                 or not isclose(cash, action.cash_per_share))):
            raise ValueError("official_corporate_action_provider_event_mismatch")
        split_seen |= has_split
        cash_seen |= has_cash
    for day, observed in sorted(yahoo.items()):
        raw = official_stock[day]
        if observed.get("volume") == 0:
            raise ValueError("official_corporate_action_missing_trade")
        if _match_ohlc(observed, raw):
            continue
        if day >= event_date or not _match_ohlc(observed, raw, factor, tolerance=0.00005):
            raise ValueError("official_corporate_action_unexplained_price")
        if day != str(signal):
            adjusted.append(day)
    flags = {row["date"] for row in marked_rows
             if str(signal) < row["date"] <= str(target) and row.get("ohlc_conflict_fields")}
    if not (adjusted or halt) or not flags.issubset(adjusted):
        raise ValueError("official_corporate_action_unexplained_conflict")
    price_sources = {
        row["official_price_report"]["source_url"]: row["official_price_report"]
        for row in (*official_stock.values(), *official_index.values())
    }
    return action.reason, {
        "evidence_version": EVIDENCE_VERSION,
        "event": {"symbol": symbol, "effective_date": event_date, "share_ratio": action.share_ratio,
                  "type": "cash_reduction" if action.halt_start else "stock_distribution",
                  "previous_close": action.previous_close,
                  "cash_per_share": action.cash_per_share,
                  "halt_start": str(action.halt_start) if action.halt_start else None},
        "sources": [*sources, *price_sources.values()],
        "session_dates": [str(day) for day in sessions],
        "halt_dates": sorted(halt),
        "yahoo_adjusted_dates": adjusted,
    }
