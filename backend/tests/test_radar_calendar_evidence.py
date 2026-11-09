from datetime import date

import pytest
from sqlalchemy import select

from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar.trading_calendar import (
    CALENDAR_STEP, OfficialTradingCalendarProvider, TradingCalendar, load_calendar, save_calendar,
)
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, RadarJobWork, RadarWorkItem
from radar_calendar_support import calendar
from test_radar_jobs import db, NOW
from test_radar_job_postgres import pg_engine


def provider(monkeypatch, reports, *, price_fields=True):
    def capture(self, path, params):
        month = params['date'][:6]
        days = reports.get(month)
        if days is None:
            raise ValueError('report_not_published')
        return ({'stat': 'OK', 'date': params['date'], 'fields': ['日期'] + (
                    ['開盤指數', '最高指數', '最低指數', '收盤指數'] if price_fields else []),
                 'total': len(days), 'data': [[f'{d.year - 1911}/{d.month:02}/{d.day:02}'] + (
                    [100, 100, 100, 100] if price_fields else []) for d in days]},
                {'source_url': 'https://www.twse.com.tw' + path + '?response=json&date=' + params['date'],
                 'snapshot_sha256': 'a' * 64, 'captured_at': '2027-02-01T00:00:00+00:00'})
    monkeypatch.setattr(RecoveringResearchPriceProvider, 'capture_twse_report', capture)
    return OfficialTradingCalendarProvider()


def test_month_dates_prove_calendar_without_price_fields(monkeypatch):
    days = [date(2026, 6, d) for d in (1, 2, 3, 4, 5, 8)]
    result = provider(monkeypatch, {'202606': days}, price_fields=False).fetch(start_date=days[0], end_date=days[-1])
    assert result.sessions == tuple(days)
    assert result.reports[0]['session_dates'] == [str(d) for d in days]


def test_lagging_report_cannot_claim_asof_open_date(monkeypatch):
    days = [date(2026, 6, d) for d in (1, 2, 3, 4, 5)]
    with pytest.raises(ValueError):
        provider(monkeypatch, {'202606': days}).fetch(start_date=days[0], end_date=date(2026, 6, 8))


def test_month_start_holiday_keeps_uncropped_report_evidence(monkeypatch):
    result = provider(monkeypatch, {'202612': [date(2026, 12, 31)], '202701': [date(2027, 1, 4)]}).fetch(
        start_date=date(2026, 12, 31), end_date=date(2027, 1, 1))
    assert result.sessions == (date(2026, 12, 31),)
    assert {r['month'] for r in result.reports} == {'2026-12', '2027-01'}
    assert result.reports[-1]['session_dates'] == ['2027-01-04']


def test_closed_month_end_requires_later_publication_witness(monkeypatch):
    result = provider(monkeypatch, {'202601': [date(2026, 1, 30)], '202602': [date(2026, 2, 2)]}).fetch(
        start_date=date(2026, 1, 30), end_date=date(2026, 1, 31))
    assert result.sessions == (date(2026, 1, 30),)
    assert result.reports[-1]['month'] == '2026-02'


def test_legacy_calendar_is_not_accepted_as_complete_evidence():
    payload = calendar().payload() | {'version': 'twse-session-calendar-v1'}
    with pytest.raises(ValueError, match='version'):
        TradingCalendar.parse(payload)


def test_serialized_sessions_must_match_report_dates():
    payload = calendar().payload()
    payload['sessions'] = payload['sessions'][:-1]
    with pytest.raises(ValueError, match='sessions_mismatch'):
        TradingCalendar.parse(payload)


def test_lagging_serialized_calendar_is_rejected():
    payload = calendar(date(2026, 6, 1), date(2026, 6, 8)).payload()
    payload['reports'][0]['session_dates'] = payload['reports'][0]['session_dates'][:5]
    payload['sessions'] = payload['sessions'][:5]
    with pytest.raises(ValueError, match='publication_incomplete'):
        TradingCalendar.parse(payload)


def test_empty_required_month_is_not_proof_of_closure(monkeypatch):
    with pytest.raises(ValueError, match='report_incomplete'):
        provider(monkeypatch, {'202606': [], '202607': [date(2026, 7, 1)]}).fetch(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))


def test_empty_serialized_required_month_is_rejected():
    payload = calendar(date(2026, 6, 1), date(2026, 7, 1)).payload()
    payload['reports'][0]['session_dates'] = []
    payload['sessions'] = ['2026-07-01']
    with pytest.raises(ValueError, match='report_dates_invalid'):
        TradingCalendar.parse(payload)


def test_calendar_conflict_is_actionable_without_automatic_provider_retries(db):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.jobs import create_job
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    from ai_stock_sentinel.daily_radar.work_handlers import Collected
    job = create_job(db, run_date=date(2026, 11, 6), lanes=['signal_close'], now=NOW)
    for work in db.scalars(select(RadarWorkItem)):
        if work.stage != 'calendar': work.state = 'ready'
    db.commit()
    def apply(db):
        raise ValueError('calendar_evidence_conflict')
    advance_job(db, job.id, handlers=SimpleNamespace(collect=lambda claim: Collected(apply)), now=NOW)
    work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))
    assert (work.state, work.reason, work.retry_at) == ('needs_review', 'calendar_evidence_conflict', None)


def test_old_calendar_work_is_replaced_without_blocking_new_evidence(db):
    from ai_stock_sentinel.daily_radar.jobs import create_job, reconcile_job, unit
    job = create_job(db, run_date=date(2026, 11, 6), lanes=['signal_close'], now=NOW)
    old = unit(db, job, 'calendar', scope={'start_date': '2025-11-07'}, lane='validation', now=NOW)
    old.state = 'waiting_provider'
    db.commit()
    reconcile_job(db, job.id, now=NOW)
    active = db.scalars(select(RadarWorkItem).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True), RadarWorkItem.stage == 'calendar')).all()
    assert len(active) == 1
    assert active[0].id != old.id
    assert active[0].scope['calendar_version'] == 'twse-session-calendar-v2'


def test_cross_date_conflicting_calendar_is_rejected_before_save(db):
    first, second = date(2026, 6, 8), date(2026, 6, 9)
    for day in (first, second):
        db.add(DailyRadarPreparedRun(run_date=day, market='TW', selected_symbols=[], universe=[],
            symbol_count=0, step_statuses={}))
    db.commit()
    save_calendar(db, calendar(date(2026, 6, 1), first, closed=(first,)))
    db.commit()
    with pytest.raises(ValueError, match='calendar_evidence_conflict'):
        save_calendar(db, calendar(date(2026, 6, 1), second))
    db.rollback()
    assert load_calendar(db, start_date=date(2026, 6, 1), as_of_date=first) is not None
    row = db.scalar(select(DailyRadarPreparedRun).where(DailyRadarPreparedRun.run_date == second))
    assert CALENDAR_STEP not in row.step_statuses


def test_postgres_concurrent_calendar_acceptance_rejects_one_conflict(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base
    Base.metadata.create_all(pg_engine)
    first, second = date(2026, 6, 8), date(2026, 6, 9)
    with Session(pg_engine) as db:
        for day in (first, second):
            db.add(DailyRadarPreparedRun(run_date=day, market='TW', selected_symbols=[], universe=[],
                symbol_count=0, step_statuses={}))
        db.commit()
    barrier = Barrier(2)
    def accept(day):
        with Session(pg_engine) as db:
            barrier.wait(timeout=10)
            try:
                save_calendar(db, calendar(date(2026, 6, 1), day, closed=(first,) if day == first else ()))
                db.commit()
                return 'saved'
            except ValueError as exc:
                assert str(exc) == 'calendar_evidence_conflict'
                db.rollback()
                return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(accept, (first, second))) == ['conflict', 'saved']
