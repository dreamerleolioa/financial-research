from collections import Counter
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import jobs, work_executor
from ai_stock_sentinel.db.models import Base, RadarJob, RadarWorkAttempt, RadarWorkItem
from test_radar_job_postgres import pg_engine
from test_radar_bounded_planning import DAY, NOW, active, finish_plan, seed
from test_radar_jobs import db
from test_next_open_history_repair import db as replay_db


def test_slow_result_audit_keeps_planned_windows_and_resumes_after_restart(db, monkeypatch):
    monkeypatch.setattr(jobs, 'WINDOW_PLAN_CANDIDATES', 3)
    seed(db, 10)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close'], now=NOW)
    finish_plan(db, job)
    planned = dict(job.scope['window_planning'])
    members = {w.id for w in active(db, job)}
    later = NOW + timedelta(minutes=51)
    for page in (1, 2, 3):
        jobs.reconcile_job(db, job.id, now=later)
        assert job.scope['window_planning'] == planned
        assert job.scope['window_result_audit']['processed_candidates'] == 3 * page
        assert {w.id for w in active(db, job)} == members
        db.expire_all()
        later += timedelta(minutes=22)
    jobs.reconcile_job(db, job.id, now=later)
    assert 'window_result_audit' not in job.scope
    assert job.scope['window_inventory_audited_at'] == (NOW + timedelta(minutes=51)).isoformat()
    jobs.reconcile_job(db, job.id, now=later + timedelta(minutes=1))
    assert job.scope['window_result_audit']['processed_candidates'] == 3
    assert {w.id for w in active(db, job)} == members


def test_batch_commits_each_window_and_leaves_provider_failure_retryable(db):
    seed(db, 4)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close'], now=NOW, owner='validation')
    finish_plan(db, job)
    seen = []

    def collect(claim):
        seen.append(claim.work_id)
        if len(seen) == 2:
            raise TimeoutError('source outage')
        return SimpleNamespace(apply=lambda db: {'state': 'ready', 'result': {}})

    result = work_executor.advance_job(db, job.id, now=NOW, window_only=True, owner='validation',
                                       max_windows=4, handlers=SimpleNamespace(collect=collect))
    assert result['advanced_count'] == 4 and result['settled_windows'] == 3
    assert len(set(seen)) == 4
    assert db.get(RadarWorkItem, seen[1]).state == 'waiting_provider'
    assert all(db.get(RadarWorkItem, key).state == 'ready' for key in (seen[0], *seen[2:]))
    assert db.query(RadarWorkAttempt).count() == 4


def test_resume_reserves_capacity_for_newest_oldest_and_intermediate_dates(tmp_path):
    from run_daily_radar_jobs import run
    refs = [{'job_id': n, 'run_date': f'2026-11-{n + 15:02d}'} for n in range(1, 9)]
    calls = Counter()
    normal_visits = []

    def read(url, *args, **kwargs):
        return {'enabled': True, 'version': 'radar-durable-v1', 'scoped_resume': True,
                'parallel_window_advance': True, 'validation_window_batch': True}

    def write(url, *args, **kwargs):
        if '/resume?' in url:
            return {'jobs': list(reversed(refs))}
        parts = urlsplit(url)
        assert parse_qs(parts.query)['max_windows'] == ['8']
        key = int(parts.path.split('/')[-2])
        day = refs[key - 1]['run_date']
        calls[key] += 1
        if 'window_only' not in parse_qs(parts.query):
            normal_visits.append(key)
        return {'requested_run_date': day, 'effective_run_date': day, 'advanced_count': 8,
                'settled_windows': 7, 'runnable_now': True}
    res = run(endpoint='https://test.invalid', token='', output_dir=tmp_path, resume=True, owner='validation',
              max_work=150, max_seconds=1200, read=read, write=write)
    assert res['advanced_requests'] == sum(calls.values()) == 150
    assert calls[8] >= 60 and calls[1] >= 30 and min(calls.values()) >= 1
    assert res['settled_windows'] == 1050
    assert set(normal_visits[:len(refs)]) == set(calls)


def test_postgres_interrupted_batch_resumes_only_unsettled_windows(pg_engine):
    class Interrupted(BaseException):
        pass

    seen = []

    def collect(claim):
        seen.append(claim.work_id)
        if len(seen) == 2:
            raise Interrupted()
        return SimpleNamespace(apply=lambda db: {'state': 'ready'})

    handlers = SimpleNamespace(collect=collect)
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        seed(db, 1)
        job = jobs.create_job(db, run_date=DAY, lanes=['signal_close'], now=NOW, owner='validation')
        finish_plan(db, job)
        job_id = job.id
        with pytest.raises(Interrupted):
            work_executor.advance_job(db, job_id, now=NOW, window_only=True, owner='validation',
                                      max_windows=8, handlers=handlers)

    with Session(pg_engine) as db:
        assert db.get(RadarWorkItem, seen[0]).state == 'ready'
        assert db.get(RadarWorkItem, seen[1]).state == 'running'
        early = work_executor.advance_job(db, job_id, now=NOW, window_only=True, owner='validation',
                                          max_windows=8, handlers=handlers)
        assert early['advance_slot_busy'] and early['advanced_count'] == 0
        retry = work_executor.advance_job(db, job_id, now=NOW + timedelta(minutes=5),
                                          window_only=True, owner='validation', max_windows=8, handlers=handlers)
        assert retry['settled_windows'] == 4
        assert seen.count(seen[0]) == 1 and seen.count(seen[1]) == 2
        assert {w.state for w in active(db, db.get(RadarJob, job_id))} == {'ready'}


def test_batch_admission_deadline_stops_after_committed_window(db, monkeypatch):
    seed(db, 1)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close'], now=NOW, owner='validation')
    finish_plan(db, job)
    ticks = iter([0, work_executor.BATCH_ADMISSION_SECONDS])
    monkeypatch.setattr(work_executor, 'monotonic', lambda: next(ticks))
    handlers = SimpleNamespace(collect=lambda claim: SimpleNamespace(apply=lambda db: {'state': 'terminal'}))
    result = work_executor.advance_job(db, job.id, now=NOW, window_only=True, owner='validation',
                                       max_windows=8, handlers=handlers)
    assert result['advanced_count'] == result['settled_windows'] == 1
    assert Counter(w.state for w in active(db, job)) == {'pending': 4, 'terminal': 1}


def test_result_audit_discards_cursor_when_candidate_inputs_change(db, monkeypatch):
    monkeypatch.setattr(jobs, 'WINDOW_PLAN_CANDIDATES', 2)
    _, candidates = seed(db, 5)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close'], now=NOW)
    finish_plan(db, job)
    later = NOW + timedelta(hours=1)
    jobs.reconcile_job(db, job.id, now=later)
    old_revision = job.scope['window_result_audit']['revision']
    candidates[0].input_snapshot = dict(candidates[0].input_snapshot, ohlcv={'close': 105})
    db.commit()
    jobs.reconcile_job(db, job.id, now=later)
    assert 'window_result_audit' not in job.scope
    assert job.scope['window_planning']['revision'] != old_revision
    assert job.scope['window_planning']['processed_candidates'] == 2
    assert not job.scope.get('intent_complete')


def test_flow_includes_unsampled_and_known_gaps(db):
    from ai_stock_sentinel.daily_radar.readiness import validation_inventory
    from ai_stock_sentinel.radar_gap_contract import digest, validation_gap_key
    from test_daily_radar_validation_read import add_candidate, add_run

    seed(db, 1, done=True)
    add_candidate(db, add_run(db, date(2026, 10, 30)), '2615.TW')
    add_candidate(db, add_run(db, date(2026, 10, 29)), '2615.TW')
    db.commit()
    full = validation_inventory(db, as_of_date=DAY, sample_limit=None)['signal_close']
    assert full['flow'] == {'maturity_known': True, 'matured_on_run_date': 1, 'settled_from_that_date': 0,
                            'unresolved': 2, 'oldest_unresolved_target': '2026-11-05', 'pending_by_action': {'unprocessed': 2}}
    known = {digest(validation_gap_key(g)) for g in full['gaps']}
    compact = validation_inventory(db, as_of_date=DAY, sample_limit=0, known_hashes=known)['signal_close']
    assert compact['flow'] == full['flow']
    assert compact['comparison'] == {'known': 2, 'new': 0} and compact['gaps'] == []


def test_completed_dates_release_their_share_and_same_date_jobs_share_capacity(tmp_path):
    from run_daily_radar_jobs import run
    dates = ('16', '17', '18', '19', '20', '23', '23', '24')
    refs = [{'job_id': i, 'run_date': f'2026-11-{day}'} for i, day in enumerate(dates)]
    calls = Counter()

    def read(url, *args, **kwargs):
        return {'enabled': True, 'version': 'radar-durable-v1', 'scoped_resume': True,
                'parallel_window_advance': True, 'validation_window_batch': True}

    def write(url, *args, **kwargs):
        if '/resume?' in url:
            return {'jobs': refs}
        key = int(urlsplit(url).path.split('/')[-2])
        calls[key] += 1
        done = dates[key] in ('16', '24')
        return {'requested_run_date': refs[key]['run_date'], 'effective_run_date': refs[key]['run_date'],
                'advanced_count': 1, 'intent_complete': done, 'runnable_now': not done}

    run(endpoint='https://test.invalid', token='', output_dir=tmp_path, resume=True, owner='validation',
        max_work=150, max_seconds=1200, read=read, write=write)
    assert calls[1] >= 30 and min(calls[5], calls[6]) >= 30
    assert calls[0] == calls[7] == 1


def test_followup_claim_rolls_back_when_admission_expires(db, monkeypatch):
    seed(db, 1)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close'], now=NOW, owner='validation')
    finish_plan(db, job)
    clock = [0]
    monkeypatch.setattr(work_executor, 'monotonic', lambda: clock[0])
    claim_work = work_executor.claim_work

    def slow_claim(*args, **kwargs):
        claim = claim_work(*args, **kwargs)
        if kwargs.get('windows_only'):
            clock[0] = work_executor.BATCH_ADMISSION_SECONDS + 1
        return claim

    monkeypatch.setattr(work_executor, 'claim_work', slow_claim)
    handlers = SimpleNamespace(collect=lambda claim: SimpleNamespace(apply=lambda db: {'state': 'ready'}))
    result = work_executor.advance_job(db, job.id, now=NOW, owner='validation', window_only=True,
                                       max_windows=8, handlers=handlers)
    assert result['settled_windows'] == 1
    assert db.query(RadarWorkAttempt).count() == 1
    assert Counter(w.state for w in active(db, job)) == {'pending': 4, 'ready': 1}


def test_flow_marks_maturity_unknown_without_calendar(db):
    from ai_stock_sentinel.daily_radar.readiness import validation_inventory
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun

    seed(db, 1)
    db.query(DailyRadarPreparedRun).delete()
    db.commit()
    report = validation_inventory(db, as_of_date=DAY)['signal_close']
    assert not report['flow']['maturity_known']
    assert report['flow']['matured_on_run_date'] is None
    assert report['flow']['settled_from_that_date'] is None
    assert report['flow']['unresolved'] == report['unknown_maturity_count'] == 5


def test_resume_admits_each_due_date_before_duplicate_jobs(db):
    dates = (10, 12, 23, *(20 for _ in range(18)), 24)
    for key, day in enumerate(dates):
        db.add(RadarJob(job_key=f'share-{key}', market='TW', lanes=['signal_close'],
            run_date=date(2026, 11, day), state='pending', next_attempt_at=NOW-timedelta(minutes=key),
            scope={'scheduler_owner': 'validation', 'dated_registration': day == 24}))
    db.commit()
    refs = jobs.resume_jobs(db, owner='validation', now=NOW)
    assert len(refs) == len({job['job_id'] for job in refs}) == 20
    assert {ref['run_date'] for ref in refs} == {str(date(2026, 11, day)) for day in dates}


def test_window_only_completion_keeps_first_normal_visit():
    from threading import Event
    from time import monotonic
    from radar_window_driver import advance_windows

    observed = Event()
    normal = []
    errors = []
    refs = [{'job_id': n, 'run_date': f'2026-11-{n}'} for n in (20, 23)]

    def write(url, *args, **kwargs):
        parsed = urlsplit(url)
        key = int(parsed.path.split('/')[-2])
        window = 'window_only' in parse_qs(parsed.query)
        if window:
            assert key == 20
        else:
            normal.append(key)
            if key == 23:
                assert observed.wait(3)
        return {'requested_run_date': f'2026-11-{key}', 'effective_run_date': f'2026-11-{key}',
                'advanced_count': int(window), 'intent_complete': True, 'runnable_now': False}

    def record(job, slot, status, error):
        if error:
            errors.append(error)
        if slot == 'window':
            observed.set()

    advance_windows(refs, endpoint='https://test.invalid', token='', write=write, limit=3,
                    deadline=monotonic()+1200, seconds=180, clock=monotonic, record=record,
                    submitted=lambda *args: None, owner='validation', window_batch=True)
    assert not errors and normal == [23, 20]


def test_postgres_followup_yields_when_another_request_owns_job_lock(pg_engine):
    from sqlalchemy import select, text
    from ai_stock_sentinel.daily_radar.work_queue import claim_work

    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as holder, Session(pg_engine) as follower:
        seed(holder, 1)
        job = jobs.create_job(holder, run_date=DAY, lanes=['signal_close'], now=NOW, owner='validation')
        finish_plan(holder, job)
        holder.scalar(select(RadarJob.id).where(RadarJob.id == job.id).with_for_update())
        follower.execute(text("SET LOCAL lock_timeout = '500ms'"))
        assert claim_work(follower, job_id=job.id, now=NOW, slot='window', owner='validation', windows_only=True) is None
        assert follower.query(RadarWorkAttempt).count() == 0


def test_two_reservations_on_same_date_leave_room_for_another_due_date(db):
    for key, day, state, dated in ((1, 23, 'pending', True), (2, 23, 'pending', False),
                                   (3, 22, 'ready', False)):
        db.add(RadarJob(job_key=f'same-date-{key}', market='TW', lanes=['signal_close'],
            run_date=date(2026, 11, day), state=state, next_attempt_at=NOW,
            scope={'scheduler_owner': 'validation', 'dated_registration': dated}))
    db.commit()
    refs = jobs.resume_jobs(db, owner='validation', now=NOW.replace(day=24), limit=2)
    assert [job['run_date'] for job in refs] == ['2026-11-23', '2026-11-22']


def test_validation_resume_registers_an_archived_open_day_once(replay_db):
    from ai_stock_sentinel.daily_radar.jobs import create_job, resume_jobs, OWNER_LANES
    from ai_stock_sentinel.daily_radar.work_queue import aware

    db = replay_db

    day = date(2026, 11, 26)
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    create_job(db, run_date=day, lanes=['publication'], owner='radar', now=now)
    works = list(db.scalars(select(RadarWorkItem).where(RadarWorkItem.run_date == day)))
    session = next(w for w in works if w.stage == 'market-session')
    session.state = 'ready'
    session.result = {'status': 'open', 'run_date': str(day), 'market': 'TW', 'provider': 'twse', 'dataset': 'MI_INDEX'}
    archives = [w for w in works if w.stage == 'market-bars']
    assert {w.scope['market'] for w in archives} == {'TW', 'TWO'}
    archives[0].state = 'ready'
    archives[0].result = {'records_written': 100}
    db.commit()
    assert not resume_jobs(db, owner='validation', now=now)
    archives[1].state = 'ready'
    archives[1].result = {'records_written': 100}
    db.commit()
    open_session = dict(session.result)
    for invalid in ({'status': 'closed'}, {'run_date': '2026-11-25'}, {'provider': 'other'}):
        session.result = open_session | invalid
        db.commit()
        assert not resume_jobs(db, owner='validation', now=now)
    session.result = open_session
    archive_scope = dict(archives[1].scope)
    archives[1].scope = archive_scope | {'trade_date': '2026-11-25'}
    db.commit()
    assert not resume_jobs(db, owner='validation', now=now)
    archives[1].scope = archive_scope
    db.commit()
    jobs = resume_jobs(db, owner='validation', now=now)
    assert [r['run_date'] for r in jobs] == [str(day)]
    job_id = jobs[0]['job_id']
    assert db.get(RadarJob, job_id).scope['dated_registration']
    cal = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))
    assert aware(cal.not_before) <= now
    resume_jobs(db, owner='validation', now=now)
    dated = create_job(db, run_date=day, lanes=OWNER_LANES['validation'], owner='validation', now=now + timedelta(hours=8))
    assert dated.id == job_id


def test_existing_dated_job_can_advance_only_after_both_archives(replay_db):
    from ai_stock_sentinel.daily_radar.work_queue import aware, claim_work
    db = replay_db
    day = date(2026, 11, 26)
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    from radar_calendar_support import calendar
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun
    db.add(DailyRadarPreparedRun(run_date=day, market='TW', selected_symbols=[], universe=[], symbol_count=0,
        step_statuses={'validation-calendar': calendar(day - timedelta(days=364), day).payload()}))
    db.commit()
    validation = jobs.create_job(db, run_date=day, lanes=jobs.OWNER_LANES['validation'], owner='validation', now=now)
    assert not validation.scope.get('window_planning', {}).get('processed_candidates')
    assert not validation.scope.get('window_plan')
    cal = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))
    assert aware(cal.not_before) == jobs.stage_time(day, 'window')
    assert claim_work(db, job_id=validation.id, owner='validation', now=now) is None
    jobs.create_job(db, run_date=day, lanes=['publication'], owner='radar', now=now)
    for work in db.scalars(select(RadarWorkItem).where(RadarWorkItem.run_date == day)):
        if work.stage == 'market-session':
            work.state, work.result = 'ready', {'status': 'open', 'market': 'TW', 'run_date': str(day),
                                                'provider': 'twse', 'dataset': 'MI_INDEX'}
        if work.stage == 'market-bars':
            work.state, work.result = 'ready', {'records_written': 10}
    db.commit()
    jobs.resume_jobs(db, owner='validation', now=now)
    assert validation.scope.get('window_planning', {}).get('complete')
    cal.state, cal.retry_at, cal.not_before = 'pending', now, jobs.stage_time(day, 'market-bars')
    db.commit()
    assert claim_work(db, job_id=validation.id, owner='validation', now=now).work_id == cal.id
    db.rollback()
    archive = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'market-bars'))
    archive.state = 'waiting_provider'
    db.commit()
    assert claim_work(db, job_id=validation.id, owner='validation', now=now) is None
    assert claim_work(db, job_id=validation.id, owner='validation', now=jobs.stage_time(day, 'window')).work_id == cal.id


def test_repair_retries_without_waiting_for_an_archive_range_writer(pg_engine):
    from sqlalchemy import text
    from ai_stock_sentinel.daily_radar.technical_rights_extension import verify_at_persistence, TechnicalRepairRejected
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from test_daily_radar_technical_repair import _repair_fixture
    Base.metadata.create_all(pg_engine)
    day = date(2026, 11, 26)
    previous = day - timedelta(days=1)
    _, _, bar = _repair_fixture('3706.TW')
    with Session(pg_engine) as writer, Session(pg_engine) as repair:
        values = vars(bar) | {key: float(getattr(bar, key)) for key in ('open', 'high', 'low', 'close')}
        writer.add_all([TaiwanDailyBar(**(values | {'trade_date': d})) for d in (previous, day)])
        writer.commit()
        writer.scalar(select(TaiwanDailyBar).where(TaiwanDailyBar.trade_date == previous).with_for_update())
        repair.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        with pytest.raises(TechnicalRepairRejected, match='rights_extension_inputs_busy'):
            verify_at_persistence(repair, '3706.TW', day, {'previous_anchor': {'date': str(previous)}})
