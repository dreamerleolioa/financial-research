from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sqlalchemy import select

from ai_stock_sentinel.db.models import DailyRadarPreparedRun, RadarJobWork, RadarWorkItem
from ai_stock_sentinel.daily_radar.jobs import create_job, reconcile_job
from ai_stock_sentinel.daily_radar.readiness import inventory_items
from radar_calendar_support import calendar
from test_radar_jobs import db
from test_daily_radar_validation_read import add_run, add_candidate, add_result


DAY = date(2026, 6, 8)
NOW = datetime(2026, 6, 9, 2, tzinfo=timezone.utc)


def setup(db, *, diagnostic_gap=False):
    candidate = add_candidate(db, add_run(db))
    candidate.input_snapshot = dict(candidate.input_snapshot) | {
        'ohlcv': {'close': 100}, 'indicators': {'support_level': 96, 'resistance_level': 104}}
    candidate.data_dates = {'ohlcv': '2026-06-01'}
    if diagnostic_gap: add_result(db, candidate, diagnostic=False)
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=[], universe=[],
        symbol_count=0, step_statuses={'validation-calendar': calendar(DAY-timedelta(days=364), DAY).payload()}))
    db.commit()
    job = create_job(db, run_date=DAY, lanes=['signal_close'], now=NOW)
    for w in db.scalars(select(RadarWorkItem)):
        if w.stage != 'window': w.state = 'ready'
    db.commit()
    return candidate, job


def active(db, job):
    return db.scalars(select(RadarWorkItem).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True), RadarWorkItem.stage == 'window')).all()


def test_candidate_input_change_replaces_old_active_window_without_idle_wait(db):
    candidate, job = setup(db)
    old = active(db, job)[0]
    old.state = 'superseded'
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 101}}
    db.commit()
    for n in range(1, 5): reconcile_job(db, job.id, now=NOW+timedelta(minutes=30*n), audit=True)
    current = next(i for i in inventory_items(db, as_of_date=DAY)
        if i['return_basis'] == 'signal_close' and i['window_days'] == 5)
    rows = active(db, job)
    assert len(rows) == 1 and rows[0].id != old.id
    assert rows[0].scope['candidate_revision'] == current['candidate_revision']
    assert rows[0].state != 'superseded'


def test_periodic_result_audit_is_independent_of_recent_activity(db):
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    candidate, job = setup(db, diagnostic_gap=True)
    assert active(db, job)[0].state == 'needs_review'
    row = db.scalar(select(DailyRadarForwardValidationResult))
    row.outcome = dict(row.outcome) | {'observation_diagnostic': {
        'version': 'daily-radar-observation-v1', 'status': 'confirmed'}}
    db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=30), audit=True)
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=60), audit=True)
    assert active(db, job)[0].state == 'ready'


def test_display_name_change_does_not_create_a_new_validation_identity(db):
    candidate, job = setup(db)
    original_id = active(db, job)[0].id
    candidate.name = '補齊顯示名稱'; db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert [w.id for w in active(db, job)] == [original_id]


def test_current_identity_superseded_work_is_rearmed_after_replan(db):
    _, job = setup(db)
    work = active(db, job)[0]; work.state = 'superseded'; db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=1), audit=True)
    assert active(db, job)[0].state == 'pending'


def test_collection_checks_planned_candidate_revision_before_fetch(db, monkeypatch):
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    candidate, job = setup(db)
    scope = dict(active(db, job)[0].scope)
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 101}}
    db.commit()
    def unexpected(*a, **k): raise AssertionError('stale work fetched prices')
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', unexpected)
    with pytest.raises(LostLease, match='radar_window_inputs_changed'):
        WorkHandlers(db).collect_window(SimpleNamespace(scope=scope, run_date=DAY))


@pytest.mark.parametrize('change', ['name', 'price'])
def test_collection_acceptance_uses_semantic_candidate_revision(db, monkeypatch, change):
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from test_daily_radar_forward_validation import _price
    candidate, job = setup(db)
    scope = dict(active(db, job)[0].scope)
    rows = [_price(str(date(2026, 6, d)), 100, 110, 90, 100) for d in (1, 2, 3, 4, 5, 8)]
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', lambda self, symbols, **kw: {s: rows for s in symbols})
    bundle = WorkHandlers(db).collect_window(SimpleNamespace(scope=scope, run_date=DAY))
    if change == 'name': candidate.name = '只修正顯示名稱'
    else: candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 101}}
    db.commit()
    if change == 'name':
        assert bundle.apply(db)['state'] == 'ready'
        db.commit()
        assert db.scalar(select(DailyRadarForwardValidationResult)) is not None
    else:
        with pytest.raises(LostLease, match='radar_window_inputs_changed'): bundle.apply(db)
        db.rollback()
        assert db.scalar(select(DailyRadarForwardValidationResult)) is None


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_shadow_then_selected_has_range_independent_revision(db, monkeypatch, basis):
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from test_daily_radar_forward_validation import _price
    asof = date(2026, 6, 9)
    shadow = add_candidate(db, add_run(db), status='shadow')
    shadow.shadow_cohort = 'comparable'
    later = add_candidate(db, add_run(db, date(2026, 6, 2)))
    for candidate in (shadow, later):
        candidate.input_snapshot = dict(candidate.input_snapshot) | {
            'ohlcv': {'close': 100}, 'indicators': {'support_level': 96, 'resistance_level': 104}}
        candidate.data_dates = {'ohlcv': str(candidate.run.run_date)}
    db.add(DailyRadarPreparedRun(run_date=asof, market='TW', selected_symbols=[], universe=[], symbol_count=0,
        step_statuses={'validation-calendar': calendar(asof-timedelta(days=364), asof).payload()}))
    db.commit()
    job = create_job(db, run_date=asof, lanes=[basis], now=NOW+timedelta(days=1))
    scope = next(w.scope for w in active(db, job) if w.scope['candidate_id'] == shadow.id)
    rows = [dict(_price(str(date(2026, 6, d)), 100, 110, 90, 100), price_basis='unadjusted_price',
        price_source='fixture', corporate_actions_checked=True, dividends=0, stock_splits=0) for d in (1,2,3,4,5,8,9)]
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', lambda self, symbols, **kw: {s: rows for s in symbols})
    monkeypatch.setattr(RecoveringResearchPriceProvider, 'fetch', lambda self, symbols, **kw: {s: rows for s in symbols})
    monkeypatch.setattr(RecoveringResearchPriceProvider, 'repair', lambda self, series, **kw: series)
    bundle = WorkHandlers(db).collect_window(SimpleNamespace(scope=scope, run_date=asof))
    assert bundle.apply(db)['state'] == 'ready'


@pytest.mark.parametrize('status', ['selected', 'shadow'])
def test_only_selected_origin_changes_affect_semantic_revision(status):
    from ai_stock_sentinel.daily_radar.readiness import validation_candidate_revision
    candidate = {'selection_status': status, 'observation_origin': None}
    changed = candidate | {'observation_origin': {'candidate_id': 9, 'first_seen_date': '2026-06-02'}}
    assert (validation_candidate_revision(candidate) == validation_candidate_revision(changed)) == (status == 'shadow')
