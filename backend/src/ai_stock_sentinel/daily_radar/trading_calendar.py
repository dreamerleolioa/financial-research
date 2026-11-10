"""Official session evidence, independent of the availability of price rows.

Persisted JSON is used by reads/scoring; only the preparation/maintenance provider
performs HTTP. Calendar coverage includes closed dates, not just price dates.
"""
from dataclasses import dataclass
from bisect import bisect_left
from datetime import date, timedelta
import re

from fastapi import Depends
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyRadarPreparedRun
from ai_stock_sentinel.db.session import get_db

VERSION = "twse-session-calendar-v2"
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
        following = (end.replace(day=28) + timedelta(days=4)).strftime("%Y-%m")
        supplied = {r.get("month") for r in reports}
        if not wanted <= supplied or supplied - wanted - {following} or len(reports) != len(supplied):
            raise ValueError("calendar_evidence_month_gap")
        reported_days = set()
        for report in reports:
            month = date.fromisoformat(report["month"] + "-01")
            if (report.get("source_url") != "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?response=json&date=" + month.strftime("%Y%m%d")
                    or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("snapshot_sha256", "")))
                    or not report.get("captured_at")):
                raise ValueError("calendar_evidence_source_invalid")
            full_dates = [date.fromisoformat(d) for d in report["session_dates"]]
            if not full_dates or full_dates != sorted(set(full_dates)) or any(d.strftime("%Y-%m") != report["month"] for d in full_dates):
                raise ValueError("calendar_evidence_report_dates_invalid")
            reported_days.update(full_dates)
        # The report's query month and capture time do not prove publication
        # through the cutoff. Retain unfiltered dates, including a later month's
        # positive publication witness for a closed month end. Until then the
        # tail is unknown, rather than silently treating absent rows as closure.
        if not reported_days or max(reported_days) < end:
            raise ValueError("calendar_evidence_publication_incomplete")
        if set(days) != {d for d in reported_days if start <= d <= end}:
            raise ValueError("calendar_evidence_sessions_mismatch")
        return cls(start, end, days, reports)


class OfficialTradingCalendarProvider:
    def __init__(self, db=None, *, source_store=None):
        self.db = db
        self.source_store = source_store

    def fetch(self, *, start_date, end_date):
        from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
        provider = RecoveringResearchPriceProvider(session=self.db, source_store=self.source_store)
        reports = []
        sessions = set()
        def collect(month):
            payload, proof = provider.capture_twse_report("/indicesReport/MI_5MINS_HIST", {
                "response": "json", "date": month.strftime("%Y%m%d")})
            rows = payload.get("data")
            if (payload.get("stat") != "OK" or str(payload.get("date", ""))[:6] != month.strftime("%Y%m")
                    or not isinstance(rows, list) or not rows or ("total" in payload and (
                        type(payload["total"]) is not int or payload["total"] != len(rows)))):
                raise ValueError("calendar_evidence_report_incomplete")
            index = payload["fields"].index("日期")
            dates = []
            for row in rows:
                y, m, d = map(int, str(row[index]).split("/"))
                dates.append(date(y + 1911, m, d))
            reports.append({**proof, "month": month.strftime("%Y-%m"),
                            "session_dates": [str(d) for d in dates]})
            sessions.update(d for d in dates if start_date <= d <= end_date)
        for month in months(start_date, end_date):
            collect(month)
        if not any(d >= str(end_date) for r in reports for d in r["session_dates"]):
            collect((end_date.replace(day=28) + timedelta(days=4)).replace(day=1))
        return TradingCalendar.parse(TradingCalendar(start_date, end_date, tuple(sorted(sessions)),
                                                       tuple(reports)).payload())


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
    """Return whether a prepared-run checkpoint actually retained this proof."""
    calendar = TradingCalendar.parse(calendar.payload())
    if not inspect(session.connection()).has_table(DailyRadarPreparedRun.__tablename__):
        return False
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(904240106)"))
    for previous in stored_calendars(session):
        if calendars_conflict(previous, calendar):
            raise ValueError("calendar_evidence_conflict")
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
                return True
        prepared.step_statuses = dict(prepared.step_statuses or {}) | {CALENDAR_STEP: calendar.payload()}
        return True
    return False


def stored_calendars(session, *, as_of_date=None):
    if not inspect(session.connection()).has_table(DailyRadarPreparedRun.__tablename__):
        return []
    query = select(DailyRadarPreparedRun.run_date, DailyRadarPreparedRun.step_statuses[CALENDAR_STEP].as_json()).where(DailyRadarPreparedRun.market == "TW")
    if as_of_date is not None:
        query = query.where(DailyRadarPreparedRun.run_date <= as_of_date)
    payloads = session.execute(query)
    evidence = []
    for source_date, payload in payloads:
        if not isinstance(payload, dict):
            continue
        try:
            calendar = TradingCalendar.parse(payload)
        except (ValueError, KeyError, TypeError):
            continue
        if calendar.through <= source_date and (as_of_date is None or calendar.through <= as_of_date):
            evidence.append(calendar)
    from ai_stock_sentinel.daily_radar.work_queue import durable_enabled
    if durable_enabled():
        from ai_stock_sentinel.db.models import RadarWorkItem
        if inspect(session.connection()).has_table(RadarWorkItem.__tablename__):
            query = select(RadarWorkItem.run_date, RadarWorkItem.result).where(RadarWorkItem.stage == 'calendar', RadarWorkItem.state == 'ready')
            if as_of_date is not None:
                query = query.where(RadarWorkItem.run_date <= as_of_date)
            saved = session.execute(query)
            for source_date, result in saved:
                try:
                    calendar = TradingCalendar.parse(result['calendar'])
                    if source_date is not None and calendar.through <= source_date and (as_of_date is None or calendar.through <= as_of_date):
                        evidence.append(calendar)
                except (ValueError, TypeError, KeyError):
                    continue
    return evidence


def calendars_conflict(left, right):
    lo, hi = max(left.start, right.start), min(left.through, right.through)
    return lo <= hi and {d for d in left.sessions if lo <= d <= hi} != {d for d in right.sessions if lo <= d <= hi}


def load_calendar(session, *, start_date, as_of_date):
    evidence = stored_calendars(session, as_of_date=as_of_date)
    # A single complete proof is preferable to an unprovable union of sparse
    # price histories. Overlapping contradictory proofs remain unavailable.
    covering = [c for c in evidence if c.start <= start_date <= c.through]
    if not covering:
        return None
    best = max(covering, key=lambda c: c.through)
    for other in evidence:
        if calendars_conflict(best, other):
            return None
    return best


class CalendarEvidenceIndex:
    """Select one existing proof per signal; never synthesize coverage.

    Input proofs must already be parsed and visible at the requested cutoff.
    Daily claims only index contradictions, including claims of closed dates.
    This avoids comparing every proof pair for every outcome in a long cohort.
    """
    def __init__(self, calendars):
        self._ordered = sorted(calendars, key=lambda c: c.through, reverse=True)
        claims, conflicts = {}, set()
        for calendar in self._ordered:
            sessions = set(calendar.sessions)
            day = calendar.start
            while day <= calendar.through:
                opened = day in sessions
                if claims.setdefault(day, opened) != opened:
                    conflicts.add(day)
                day += timedelta(days=1)
        conflicts = sorted(conflicts)
        self._invalid = set()
        for calendar in self._ordered:
            offset = bisect_left(conflicts, calendar.start)
            if offset < len(conflicts) and conflicts[offset] <= calendar.through:
                self._invalid.add(id(calendar))
        self._by_signal = {}

    def for_signal(self, signal):
        if signal not in self._by_signal:
            best = next((c for c in self._ordered if signal is not None and c.start <= signal <= c.through), None)
            # Retain load_calendar's conservative conflict policy; do not fall
            # back to an older proof when the newest covering one conflicts.
            self._by_signal[signal] = best if best is not None and id(best) not in self._invalid else None
        return self._by_signal[signal]

    def latest(self):
        return max((c for c in self._ordered if id(c) not in self._invalid),
            key=lambda c: (c.through, -c.start.toordinal()), default=None)


def check_outcome_calendars(outcomes, evidence):
    """Project outcomes by proof, normalizing each calendar once per batch."""
    from ai_stock_sentinel.calibration.forward_validation import parse_date
    checked, buckets, invalid_by_position = list(outcomes), {}, {}
    for position, row in enumerate(checked):
        if row['status'] != 'validated':
            continue
        calendar = evidence.for_signal(parse_date(row['signal_date']))
        key = id(calendar) if calendar is not None else None
        buckets.setdefault(key, (calendar, []))[1].append((position, row))
    for calendar, entries in buckets.values():
        rows, gaps = check_outcome_calendar([row for _, row in entries], calendar.sessions if calendar else None,
            covered_through=calendar.through if calendar else None)
        gaps = iter(gaps)
        for (position, _), row in zip(entries, rows):
            checked[position] = row
            # Buckets contain validated inputs only, so each projected skip
            # corresponds to the next gap returned by the existing checker.
            if row['status'] == 'skipped':
                invalid_by_position[position] = next(gaps)
    return checked, [invalid_by_position[i] for i in range(len(checked)) if i in invalid_by_position]


def check_outcome_calendar(outcomes, sessions, *, covered_through=None):
    """Require independent calendar proof without changing persisted prices.

    None means unavailable evidence; an empty sequence is a known calendar.
    """
    from ai_stock_sentinel.calibration.forward_validation import parse_date
    days = sorted(set(sessions)) if sessions is not None else None
    checked, invalid = [], []
    for row in outcomes:
        reason = None
        if row["status"] == "validated":
            if days is None or (covered_through is not None and any(
                    day is not None and day > covered_through for day in
                    (parse_date(row['signal_date']), parse_date(row.get('target_date'))))):
                reason = 'official_calendar_unavailable'
            else:
                from ai_stock_sentinel.daily_radar.window_identity import window_error
                signal, target = parse_date(row["signal_date"]), parse_date(row.get("target_date"))
                expected = [d for d in days if signal is not None and d > signal][:int(row["window_days"])]
                error = (window_error(row, signal=signal, sessions=expected,
                    basis='next_open' if row['validation_version'].startswith('daily-radar-next-open') else 'signal_close')
                    if signal in days and len(expected) == row['window_days'] else None)
                if signal not in days or len(expected) != row["window_days"] or target != expected[-1] or error:
                    reason = "historical_window_calendar_mismatch"
        if reason:
            invalid.append({"candidate_id":row["candidate_id"],"window_days":row["window_days"],
                            "validation_version":row["validation_version"],"reason":reason})
            row = dict(row) | {"status":"skipped","skip_reason":reason}
        checked.append(row)
    return checked, invalid
