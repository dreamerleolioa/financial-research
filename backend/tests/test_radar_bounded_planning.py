from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from ai_stock_sentinel.db.models import DailyRadarPreparedRun, DailyRadarForwardValidationResult, RadarWorkItem, RadarJobWork
from ai_stock_sentinel.daily_radar import jobs
from ai_stock_sentinel.daily_radar.work_executor import advance_job
from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_VALIDATION_WINDOWS
from radar_calendar_support import calendar
from test_daily_radar_validation_read import add_run, add_candidate
from test_radar_jobs import db
from test_radar_job_postgres import pg_engine


DAY = date(2026, 11, 6)
SIGNAL = date(2026, 6, 1)
NOW = datetime(2026, 11, 7, 2, tzinfo=timezone.utc)


def seed(db, count, *, done=False):
    run = add_run(db, SIGNAL, count=count)
    candidates = []
    for i in range(count):
        c = add_candidate(db, run, f'{1200+i}.TW')
        c.input_snapshot = dict(c.input_snapshot) | {'ohlcv': {'close': 100}}
        c.data_dates = {'ohlcv': '2026-05-29'}
        candidates.append(c)
        if done:
            for version in (FORWARD_VALIDATION_VERSION, RESEARCH_VALIDATION_VERSION):
                for window in DAILY_RADAR_VALIDATION_WINDOWS:
                    db.add(DailyRadarForwardValidationResult(candidate_id=c.id, window_days=window,
                        validation_version=version, status='skipped', signal_date=SIGNAL,
                        benchmark_symbol='TAIEX', evaluation_as_of_date=DAY,
                        skip_reason='stale_candidate_price', outcome={}))
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=[], universe=[], symbol_count=0,
        step_statuses={'validation-calendar': calendar(DAY-timedelta(days=364), DAY).payload()}))
    db.commit()
    return run, candidates


def active(db, job):
    return db.scalars(select(RadarWorkItem).join(RadarJobWork).where(RadarJobWork.job_id == job.id,
        RadarJobWork.active.is_(True), RadarWorkItem.stage == 'window')).all()


def finish_plan(db, job):
    for _ in range(60):
        if (job.scope.get('window_planning') or {}).get('complete'): return
        jobs.reconcile_job(db, job.id, now=NOW)
    raise AssertionError('bounded plan did not finish')


@pytest.mark.parametrize('count', [20, 200])
def test_registration_limits_window_materialization_and_sql_calls(db, count):
    seed(db, count)
    queries = []
    engine = db.get_bind()
    def record(*a): queries.append(a[2])
    event.listen(engine, 'before_cursor_execute', record)
    try: job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    finally: event.remove(engine, 'before_cursor_execute', record)
    assert len(active(db, job)) <= 50
    assert len(queries) < 300
    assert job.scope['window_planning']['complete'] is False
    assert job.scope['intent_complete'] is False


@pytest.mark.parametrize('count', [20, 200])
def test_planning_only_advances_resume_and_keep_every_window(db, count):
    seed(db, count, done=True)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    assert len(active(db, job)) <= 50
    for w in db.scalars(select(RadarWorkItem)): w.state = 'ready'
    db.commit()
    assert jobs.job_status(db, job.id)['runnable_now'] is True
    assert jobs.due_jobs(db, now=NOW)[0]['job_id'] == job.id
    def unexpected(*a): raise AssertionError('complete saved outcomes fetched providers')
    for _ in range(count // 5 + 1):
        result = advance_job(db, job.id, handlers=SimpleNamespace(collect=unexpected), now=NOW)
        assert result['advanced_count'] == 0
        if result['intent_complete']: break
        assert result['runnable_now'] is True
    assert result['intent_complete'] is True
    assert result['runnable_now'] is False
    rows = active(db, job)
    assert len(rows) == count * 10
    assert {(w.lane, w.scope['window_days']) for w in rows} == {
        (basis, window) for basis in ('signal_close', 'next_open') for window in (5, 10, 20, 40, 60)}
    assert job.scope['window_planning']['processed_candidates'] == count


def test_input_change_mid_plan_restarts_epoch_and_retires_old_members(db):
    _, candidates = seed(db, 20)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    epoch = job.scope['window_planning']['revision']
    previous = {w.id for w in active(db, job) if w.scope['candidate_id'] == candidates[0].id}
    candidates[0].input_snapshot = dict(candidates[0].input_snapshot) | {'ohlcv': {'close': 101}}
    db.commit(); jobs.reconcile_job(db, job.id, now=NOW)
    assert job.scope['window_planning']['revision'] != epoch
    assert not previous.intersection(w.id for w in active(db, job))
    finish_plan(db, job)
    assert len(active(db, job)) == 200


def test_name_and_result_writes_do_not_restart_an_unfinished_plan(db):
    _, candidates = seed(db, 20)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    epoch = job.scope['window_planning']['revision']
    candidates[0].name = 'display only'
    db.add(DailyRadarForwardValidationResult(candidate_id=candidates[0].id, window_days=5,
        validation_version=FORWARD_VALIDATION_VERSION, status='skipped', signal_date=SIGNAL,
        evaluation_as_of_date=DAY, skip_reason='stale_candidate_price', outcome={}))
    db.commit(); jobs.reconcile_job(db, job.id, now=NOW)
    assert job.scope['window_planning']['revision'] == epoch
    assert job.scope['window_planning']['processed_candidates'] == 10


def test_origin_change_outside_lookback_restarts_current_inventory(db):
    _, candidates = seed(db, 20)
    old = add_run(db, date(2024, 6, 3)); add_candidate(db, old, candidates[0].symbol); db.commit()
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    epoch = job.scope['window_planning']['revision']
    old.status = 'invalidated'; db.commit(); jobs.reconcile_job(db, job.id, now=NOW)
    assert job.scope['window_planning']['revision'] != epoch
    finish_plan(db, job)
    assert len(active(db, job)) == 200


def test_keyset_does_not_skip_zero_or_negative_candidate_ids(db):
    _, candidates = seed(db, 20)
    candidates[0].id, candidates[1].id = -2, 0
    db.commit()
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    finish_plan(db, job)
    assert len(active(db, job)) == 200
    assert {-2, 0} <= {w.scope['candidate_id'] for w in active(db, job)}


def test_incomplete_plan_never_calls_final_readiness_or_reports_complete(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness
    seed(db, 20, done=True)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    for w in db.scalars(select(RadarWorkItem)): w.state = 'ready'
    db.commit()
    def unexpected(*a, **k): raise AssertionError('incomplete plan audited as complete')
    monkeypatch.setattr(readiness, 'read_readiness', unexpected)
    jobs.reconcile_job(db, job.id, now=NOW, audit=True)
    assert job.scope['window_planning']['complete'] is False
    assert job.scope['intent_complete'] is False and job.scope['audit_ready'] is False


def test_new_candidate_mid_plan_restarts_and_keeps_full_cohort(db):
    run, _ = seed(db, 20)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    old = job.scope['window_planning']['revision']
    add_candidate(db, run, '1301.TW'); run.candidate_count = 21; db.commit()
    jobs.reconcile_job(db, job.id, now=NOW)
    assert job.scope['window_planning']['revision'] != old
    finish_plan(db, job)
    assert len(active(db, job)) == 210


def test_page_filter_keeps_full_pool_counts_and_explicit_empty_scope(db):
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.readiness import inventory_items
    _, candidates = seed(db, 20)
    scoped = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY,
        candidate_ids=[candidates[0].id])
    assert len(scoped) == 1 and scoped[0]['daily_selected_pool_count'] == 20
    assert list(inventory_items(db, as_of_date=DAY, candidate_ids=[])) == []
    assert len(list(inventory_items(db, as_of_date=DAY, candidate_ids=[candidates[0].id]))) == 10
    assert len(list(inventory_items(db, as_of_date=DAY))) == 200


def test_unused_large_snapshot_fields_do_not_restart_plan(db):
    _, candidates = seed(db, 20)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    revision = job.scope['window_planning']['revision']
    candidates[0].input_snapshot = dict(candidates[0].input_snapshot) | {'unused_history': 'x'*100_000}
    db.commit(); jobs.reconcile_job(db, job.id, now=NOW)
    assert job.scope['window_planning']['revision'] == revision
    assert job.scope['window_planning']['processed_candidates'] == 10


def test_postgres_epoch_uses_only_validation_inputs(pg_engine):
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base
    from ai_stock_sentinel.daily_radar.planning_inventory import inventory_input_revision
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        _, candidates = seed(db, 20)
        first = inventory_input_revision(db, as_of_date=DAY)
        candidates[0].name = 'display only'
        candidates[0].input_snapshot = dict(candidates[0].input_snapshot) | {'unused_history': 'x'*100_000}
        db.commit()
        assert inventory_input_revision(db, as_of_date=DAY) == first
        candidates[0].input_snapshot = dict(candidates[0].input_snapshot) | {'ohlcv': {'close': 101}}
        db.commit()
        assert inventory_input_revision(db, as_of_date=DAY) != first


def test_postgres_concurrent_pages_resume_without_skipping_candidates(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base, RadarJob
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        seed(db, 20, done=True)
        job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
        job_id = job.id
    start = Barrier(2)
    def page(_):
        with Session(pg_engine) as db:
            start.wait(timeout=10)
            jobs.reconcile_job(db, job_id, now=NOW)
    with ThreadPoolExecutor(max_workers=2) as executor: list(executor.map(page, range(2)))
    with Session(pg_engine) as db:
        job = db.get(RadarJob, job_id)
        assert job.scope['window_planning']['processed_candidates'] == 15
        assert len(active(db, job)) == 150
        finish_plan(db, job)
        assert len(active(db, job)) == 200


def test_postgres_last_page_rechecks_input_epoch_before_completing(pg_engine, monkeypatch):
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base, DailyRadarCandidate
    from ai_stock_sentinel.daily_radar import readiness
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        _, candidates = seed(db, 6)
        first_id = candidates[0].id
        job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
        original = readiness.inventory_items
        changed = []
        def inventory(*a, **k):
            yield from original(*a, **k)
            if not changed:
                with Session(pg_engine) as other:
                    candidate = other.get(DailyRadarCandidate, first_id)
                    candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 101}}
                    other.commit()
                changed.append(True)
        monkeypatch.setattr(readiness, 'inventory_items', inventory)
        jobs.reconcile_job(db, job.id, now=NOW)
        assert job.scope['window_planning']['complete'] is False
        assert job.scope['intent_complete'] is False
        finish_plan(db, job)
        assert len(active(db, job)) == 60


def test_missing_calendar_requeues_its_owner_and_stops_window_claims(db):
    seed(db, 20)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    row = db.scalar(select(DailyRadarPreparedRun)); row.step_statuses = {}
    calendar_work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))
    calendar_work.state, calendar_work.result = 'ready', {}
    db.commit(); jobs.reconcile_job(db, job.id, now=NOW, audit=True)
    assert job.scope['window_planning']['state'] == 'waiting_calendar'
    assert calendar_work.state == 'pending'
    assert active(db, job) == []
    assert job.scope['intent_complete'] is False


def test_calendar_conflict_stays_blocked_despite_covering_saved_proof(db):
    from ai_stock_sentinel.daily_radar.work_handlers import Collected
    from ai_stock_sentinel.daily_radar.trading_calendar import save_calendar
    seed(db, 20)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))
    for other in db.scalars(select(RadarWorkItem)):
        if other.id != work.id: other.state = 'ready'
    # Collected before a concurrent publisher's covering proof was committed.
    captured = calendar(DAY-timedelta(days=364), DAY, closed=(SIGNAL,))
    def collect(claim):
        assert claim.stage == 'calendar'
        return Collected(lambda db: save_calendar(db, captured))
    db.commit()
    advance_job(db, job.id, handlers=SimpleNamespace(collect=collect), now=NOW)
    assert (work.state, work.reason, work.retry_at) == ('needs_review', 'calendar_evidence_conflict', None)
    jobs.reconcile_job(db, job.id, now=NOW)
    assert work.state == 'needs_review'
    assert job.scope['intent_complete'] is False


def test_explicit_calendar_resolution_retries_owner_before_completion(db):
    seed(db, 20)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))
    # Simulate the separately authorized review/repair resetting its owner.
    work.state, work.reason, work.retry_at = 'pending', None, NOW
    for other in db.scalars(select(RadarWorkItem)):
        if other.id != work.id: other.state = 'ready'
    db.commit(); jobs.reconcile_job(db, job.id, now=NOW)
    assert work.state == 'pending'
    assert job.scope['intent_complete'] is False
    # The real calendar handler must accept the proof before the owner is ready.
    advance_job(db, job.id, now=NOW)
    assert work.state == 'ready'
    finish_plan(db, job)
    assert len(active(db, job)) == 200


def test_late_page_preserves_earliest_result_audit_for_repaired_outcomes(db):
    _, candidates = seed(db, 6, done=True)
    row = db.scalar(select(DailyRadarForwardValidationResult).where(
        DailyRadarForwardValidationResult.candidate_id == candidates[0].id,
        DailyRadarForwardValidationResult.validation_version == FORWARD_VALIDATION_VERSION,
        DailyRadarForwardValidationResult.window_days == 5))
    row.status, row.target_date = 'validated', date(2026, 6, 9)
    db.commit()
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    work = next(w for w in active(db, job) if w.scope['candidate_id'] == candidates[0].id
        and w.lane == 'signal_close' and w.scope['window_days'] == 5)
    assert work.state == 'needs_review'
    # An authorized repair updates the result after this page was registered.
    row.status, row.target_date = 'skipped', None
    db.commit()
    later = NOW + timedelta(minutes=51)
    jobs.reconcile_job(db, job.id, now=later)
    assert job.scope['window_planning']['complete'] is True
    assert job.scope['window_inventory_audited_at'] == NOW.isoformat()
    jobs.reconcile_job(db, job.id, now=later)
    assert work.state == 'ready'
    assert job.scope['window_planning']['processed_candidates'] == 5
