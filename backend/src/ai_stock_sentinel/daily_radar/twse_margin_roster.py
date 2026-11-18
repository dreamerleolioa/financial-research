"""Dated TWSE evidence for a listed stock outside the margin trading roster."""
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta, timezone
import logging
import re
from typing import Any

from ai_stock_sentinel.clock import TAIPEI_TZ
from ai_stock_sentinel.daily_radar.constants import SOURCE_SNAPSHOT_TTL
from ai_stock_sentinel.daily_radar.market_session import TWSE_MI_INDEX_URL

TWSE_MARGIN_ROSTER_URL = "https://www.twse.com.tw/exchangeReport/TWT92U"
TWSE_MARGIN_URL = "https://www.twse.com.tw/exchangeReport/MI_MARGN"
TWSE_CREDIT_STATUS_URL = "https://www.twse.com.tw/exchangeReport/TWT93U"

_SCOPE_NOTE = ("本表所列有價證券為所有得為融資融券交易之上市有價證券，不含未取得融資融券資格者，"
               "或有「有價證券得為融資融券標準」第四條及第五條規定情事而暫停融資融券交易者。")
_CHECKS = {TWSE_MI_INDEX_URL: "listed", TWSE_MARGIN_URL: "no_balance_row",
           TWSE_CREDIT_STATUS_URL: "no_credit_status_row"}
_CODE = re.compile(r"[0-9][0-9A-Z]{3,7}")
_REASON = "outside_dated_twse_margin_roster"
logger = logging.getLogger(__name__)


class _InvalidReport(ValueError):
    """Absence cannot be inferred from a partial or ambiguous report."""


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def roster_confirmation_boundary(day: date) -> datetime:
    # Once the entire trading date and the raw HTTP snapshot TTL have elapsed,
    # even a cached report was retrieved after that date's intraday revisions.
    return datetime.combine(day + timedelta(days=1), time.min, TAIPEI_TZ) + SOURCE_SNAPSHOT_TTL


def twse_exemption_is_current(proof: Mapping, day: date) -> bool:
    checks = proof.get("checks")
    if (proof.get("evaluated_for") != day.isoformat() or not isinstance(checks, Mapping)
            or checks.get(TWSE_MARGIN_URL) != "no_balance_row"):
        return False
    try:
        checked = datetime.fromisoformat(proof["checked_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if checked.tzinfo is None or checked.astimezone(TAIPEI_TZ).date() < day:
        return False
    now = current_time()
    boundary = roster_confirmation_boundary(day)
    return checked <= now and (checked >= boundary or now < min(boundary, checked + SOURCE_SNAPSHOT_TTL))


def valid_roster_evidence(proof: Mapping, *, run_date: date | None, symbol: str | None) -> bool:
    try:
        reported = date.fromisoformat(proof["report_date"])
        checked = datetime.fromisoformat(proof["checked_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return (proof.get("reason") == _REASON
            and checked.tzinfo is not None and checked.astimezone(TAIPEI_TZ).date() >= reported
            and re.fullmatch(r"[1-9][0-9]{3}\.TW", str(proof.get("symbol"))) is not None
            and (symbol is None or proof["symbol"] == symbol)
            and proof.get("evaluated_for") == reported.isoformat()
            and (run_date is None or reported == run_date)
            and type(proof.get("roster_count")) is int and proof["roster_count"] > 0
            and proof.get("checks") == _CHECKS)


def _dated(report, run_date, dataset):
    if report.get("stat") != "OK" or report.get("date") != run_date.strftime("%Y%m%d"):
        raise _InvalidReport(f"{dataset}: unavailable or wrong date")
    return report


def _codes(table, required, dataset, *, has_total=False, credit=False):
    fields, rows = table.get("fields"), table.get("data")
    if (not isinstance(fields, list) or any(fields.count(field) != 1 for field in required)
            or not isinstance(rows, list) or not rows):
        raise _InvalidReport(f"{dataset}: invalid fields or rows")
    if has_total and (type(table.get("total")) is not int or table["total"] != len(rows)):
        raise _InvalidReport(f"{dataset}: row count mismatch")
    index = fields.index(required[0])
    name_index = fields.index(required[1])
    codes = set()
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(fields) or not isinstance(row[index], str):
            raise _InvalidReport(f"{dataset}: malformed row")
        code = row[index].strip()
        if credit and not code and row[name_index] == "合計" and i == len(rows) - 1:
            continue
        if not _CODE.fullmatch(code) or code in codes or not isinstance(row[name_index], str) or not row[name_index].strip():
            raise _InvalidReport(f"{dataset}: invalid or repeated security")
        codes.add(code)
    if not codes:
        raise _InvalidReport(f"{dataset}: empty report")
    return codes


def _table(report, run_date, title, dataset):
    tables = report.get("tables")
    if isinstance(tables, list):
        matches = [t for t in tables if isinstance(t, Mapping) and _title_matches(t.get("title"), run_date, title)]
        if len(matches) == 1:
            return matches[0]
    raise _InvalidReport(f"{dataset}: missing or ambiguous all-market table")


def _title_matches(title, day, label):
    # TWSE includes the ROC date in the title independently of the JSON date.
    return isinstance(title, str) and re.fullmatch(
        rf"{day.year - 1911}年0?{day.month}月0?{day.day}日\s+{re.escape(label)}", title.strip()
    ) is not None


def _units(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value):
        raise _InvalidReport("MI_MARGN: invalid transaction units")
    return int(value.replace(",", ""))


def _balance_codes(report, day):
    detail = _table(_dated(report, day, "MI_MARGN"), day, "融資融券彙總 (全部)", "MI_MARGN")
    codes = _codes(detail, ("代號", "名稱"), "MI_MARGN")
    summary = _table(report, day, "信用交易統計", "MI_MARGN")
    if (summary.get("fields") != ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"]
            or not isinstance(summary.get("data"), list)):
        raise _InvalidReport("MI_MARGN: invalid summary")
    for start, kind, repayment in ((2, "融資(交易單位)", "現金償還"), (8, "融券(交易單位)", "現券償還")):
        rows = [r for r in summary["data"] if isinstance(r, list) and r and r[0] == kind]
        if (len(rows) != 1 or len(rows[0]) != 6 or
                detail["fields"][start:start + 5] != ["買進", "賣出", repayment, "前日餘額", "今日餘額"]):
            raise _InvalidReport("MI_MARGN: invalid transaction columns")
        for offset, expected in enumerate(rows[0][1:]):
            if sum(_units(row[start + offset]) for row in detail["data"]) != _units(expected):
                raise _InvalidReport("MI_MARGN: detail does not reconcile to market totals")
    return codes


def verified_balance_codes(report: Mapping, day: date) -> set[str] | None:
    try:
        return _balance_codes(report, day)
    except _InvalidReport as exc:
        logger.warning("margin_balances_unverified run_date=%s reason=%s", day, exc)
        return None


def roster_inapplicability(*, symbols: list[str], run_date: date, credit_report: Mapping,
                           read: Callable, get_balance_codes: Callable[[], set[str] | None],
                           checked_at: datetime) -> dict[str, dict[str, Any]]:
    requested = {s[:-3] for s in symbols if re.fullmatch(r"[1-9][0-9]{3}\.TW", s)}
    if not requested:
        return {}
    params = {"date": run_date.strftime("%Y%m%d"), "response": "json"}
    try:
        credit = _dated(credit_report, run_date, "TWT93U")
        credit_codes = _codes(credit, ("代號", "名稱", "備註"), "TWT93U", has_total=True, credit=True)
        candidates = requested - credit_codes
        if not candidates:
            return {}
        roster = _dated(read(TWSE_MARGIN_ROSTER_URL, params, "TWSE_TWT92U"), run_date, "TWT92U")
        if (not isinstance(roster.get("notes"), list) or _SCOPE_NOTE not in roster["notes"]
                or not _title_matches(roster.get("title"), run_date, "平盤下得融(借)券賣出之證券名單")):
            raise _InvalidReport("TWT92U: scope not established")
        eligible = _codes(roster, ("證券代號", "證券名稱", "暫停融券賣出", "暫停借券賣出"), "TWT92U", has_total=True)
        candidates -= eligible
        if not candidates:
            return {}

        balance_codes = get_balance_codes()
        if balance_codes is None:
            return {}
        if candidates & balance_codes:
            logger.info("margin_roster_balance_row_present run_date=%s codes=%s",
                        run_date, sorted(candidates & balance_codes))
        candidates -= balance_codes
        if not candidates:
            return {}

        listed = _dated(read(TWSE_MI_INDEX_URL, params | {"type": "ALLBUT0999"}, "TWSE_MI_INDEX"), run_date, "MI_INDEX")
        prices = _table(listed, run_date, "每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))", "MI_INDEX")
        listed_codes = _codes(prices, ("證券代號", "證券名稱", "收盤價", "成交股數"), "MI_INDEX")
        if candidates - listed_codes:
            logger.info("margin_roster_listing_unconfirmed run_date=%s codes=%s",
                        run_date, sorted(candidates - listed_codes))
        candidates &= listed_codes
    except _InvalidReport as exc:
        logger.warning("margin_roster_unverified run_date=%s reason=%s", run_date, exc)
        return {}

    return {f"{code}.TW": {"applicability": "not_applicable", "eligibility": {
        "symbol": f"{code}.TW", "source_url": TWSE_MARGIN_ROSTER_URL, "reason": _REASON,
        "report_date": run_date.isoformat(), "evaluated_for": run_date.isoformat(),
        "roster_count": len(eligible), "checks": dict(_CHECKS), "checked_at": checked_at.isoformat(),
    }} for code in candidates}
