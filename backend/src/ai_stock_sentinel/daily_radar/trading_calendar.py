"""Official session evidence, independent of the availability of price rows.

Persisted JSON is used by reads/scoring; only the preparation/maintenance provider
performs HTTP. Calendar coverage includes closed dates, not just price dates.
"""
from dataclasses import dataclass
from datetime import date, timedelta
import re

from fastapi import Depends
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyRadarPreparedRun
from ai_stock_sentinel.db.session import get_db

VERSION = "twse-session-calendar-v1"
CALENDAR_STEP = "validation-calendar"
SESSION_STEP = "market-session"


def months(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


@dataclass(frozen=True)
class TradingCalendar:
    start: date
    through: date
    sessions: tuple[date, ...]
    reports: tuple[dict, ...]

    def payload(self):
        return {"version": VERSION, "market": "TW", "covered_from": str(self.start),
                "covered_through": str(self.through), "sessions": [str(d) for d in self.sessions],
                "reports": list(self.reports)}

    @classmethod
    def parse(cls, payload):
        if payload.get("version") != VERSION or payload.get("market") != "TW":
            raise ValueError("calendar_evidence_version_or_market_invalid")
        start, end = date.fromisoformat(payload["covered_from"]), date.fromisoformat(payload["covered_through"])
        days = tuple(date.fromisoformat(day) for day in payload["sessions"])
        reports = tuple(payload["reports"])
        if start > end or list(days) != sorted(set(days)) or any(not start <= d <= end for d in days):
            raise ValueError("calendar_evidence_dates_invalid")
        wanted = {m.strftime("%Y-%m") for m in months(start, end)}
        if {r.get("month") for r in reports} != wanted or len(reports) != len(wanted):
            raise ValueError("calendar_evidence_month_gap")
        for report in reports:
            month = date.fromisoformat(report["month"] + "-01")
            if (report.get("source_url") != "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?response=json&date=" + month.strftime("%Y%m%d")
                    or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("snapshot_sha256", "")))
                    or not report.get("captured_at")):
                raise ValueError("calendar_evidence_source_invalid")
        return cls(start, end, days, reports)


class OfficialTradingCalendarProvider:
    def __init__(self, db=None):
        self.db = db

    def fetch(self, *, start_date, end_date):
        from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
        provider = RecoveringResearchPriceProvider(session=self.db)
        # Start on the first day to retain each month's report provenance even
        # when the requested subrange contains only a holiday/weekend.
        rows = provider.official_research_history("TAIEX", start_date=start_date.replace(day=1), end_date=end_date)
        reports = {}
        sessions = set()
        for row in rows:
            day = date.fromisoformat(row["date"])
            proof = row["official_price_report"]
            reports[day.strftime("%Y-%m")] = {"month": day.strftime("%Y-%m"), **proof}
            if start_date <= day <= end_date:
                sessions.add(day)
        return TradingCalendar.parse(TradingCalendar(start_date, end_date, tuple(sorted(sessions)),
                                                       tuple(reports.values())).payload())


class CalendarUnavailableError(RuntimeError):
    pass


def require_calendar(provider, *, start_date, end_date):
    try:
        calendar = TradingCalendar.parse(provider.fetch(start_date=start_date,end_date=end_date).payload())
        if calendar.start > start_date or calendar.through != end_date:
            raise ValueError("calendar_evidence_coverage_incomplete")
        return calendar
    except Exception as exc:
        raise CalendarUnavailableError("official_trading_calendar_unavailable") from exc


def get_trading_calendar_provider(db: Session = Depends(get_db)):
    return OfficialTradingCalendarProvider(db if isinstance(db, Session) else None)


def save_calendar(session, calendar):
    calendar = TradingCalendar.parse(calendar.payload())
    if not inspect(session.connection()).has_table(DailyRadarPreparedRun.__tablename__):
        return
    prepared = session.execute(select(DailyRadarPreparedRun).where(
        DailyRadarPreparedRun.market == "TW", DailyRadarPreparedRun.run_date == calendar.through)
        .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if prepared is not None:
        previous = (prepared.step_statuses or {}).get(CALENDAR_STEP)
        try:
            previous = TradingCalendar.parse(previous) if isinstance(previous, dict) else None
        except (ValueError, KeyError, TypeError):
            previous = None
        if previous is not None:
            lo, hi = max(previous.start, calendar.start), min(previous.through, calendar.through)
            if lo <= hi and {d for d in previous.sessions if lo <= d <= hi} != {
                    d for d in calendar.sessions if lo <= d <= hi}:
                raise ValueError("calendar_evidence_conflict")
            if previous.start <= calendar.start and previous.through == calendar.through:
                return
        prepared.step_statuses = dict(prepared.step_statuses or {}) | {CALENDAR_STEP: calendar.payload()}


def load_calendar(session, *, start_date, as_of_date):
    if not inspect(session.connection()).has_table(DailyRadarPreparedRun.__tablename__):
        return None
    payloads = session.execute(select(DailyRadarPreparedRun.step_statuses[CALENDAR_STEP].as_json()).where(
        DailyRadarPreparedRun.market == "TW", DailyRadarPreparedRun.run_date <= as_of_date)).scalars()
    evidence = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        try:
            calendar = TradingCalendar.parse(payload)
        except (ValueError, KeyError, TypeError):
            continue
        if calendar.through <= as_of_date:
            evidence.append(calendar)
    # A single complete proof is preferable to an unprovable union of sparse
    # price histories. Overlapping contradictory proofs remain unavailable.
    covering = [c for c in evidence if c.start <= start_date <= c.through]
    if not covering:
        return None
    best = max(covering, key=lambda c: c.through)
    for other in evidence:
        lo, hi = max(best.start, other.start), min(best.through, other.through)
        if lo <= hi and {d for d in best.sessions if lo <= d <= hi} != {d for d in other.sessions if lo <= d <= hi}:
            return None
    return best


def check_outcome_calendar(outcomes, sessions):
    """Classify saved malformed windows without changing any persisted prices."""
    from ai_stock_sentinel.calibration.forward_validation import parse_date
    days = sorted(set(sessions))
    checked, invalid = [], []
    for row in outcomes:
        if row["status"] == "validated":
            signal, target = parse_date(row["signal_date"]), parse_date(row.get("target_date"))
            expected = [d for d in days if signal is not None and d > signal][:int(row["window_days"])]
            if signal not in days or len(expected) != row["window_days"] or target != expected[-1]:
                reason = "historical_window_calendar_mismatch"
                invalid.append({"candidate_id":row["candidate_id"],"window_days":row["window_days"],
                                "validation_version":row["validation_version"],"reason":reason})
                row = dict(row) | {"status":"skipped","skip_reason":reason}
        checked.append(row)
    return checked, invalid
