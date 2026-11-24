from datetime import date, timedelta

import pytest
from sqlalchemy import select

from ai_stock_sentinel.daily_radar import jobs
from ai_stock_sentinel.daily_radar.work_queue import claim_work, finish_work, occupied_slot
from ai_stock_sentinel.db.models import RadarJob, RadarJobWork, RadarWorkItem
from test_radar_jobs import NOW, db
from test_radar_job_postgres import pg_engine


def test_scoped_resume_adopts_legacy_work_and_live_lease(db):
    day = date(2026, 11, 6)
    legacy = jobs.create_job(db, run_date=day, now=NOW)
    old_id = legacy.id
    session = claim_work(db, job_id=old_id, now=NOW, slot='normal')
    assert session.stage == 'market-session'
    calendar = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))
    calendar.state, calendar.retry_at = 'waiting_provider', NOW + timedelta(minutes=12)
    db.commit()

    validation, = jobs.resume_jobs(db, owner='validation', now=NOW)
    radar, = jobs.resume_jobs(db, owner='radar', now=NOW)
    assert validation['run_date'] == radar['run_date'] == str(day)
    assert len({old_id, radar['job_id'], validation['job_id']}) == 3
    assert jobs.resume_jobs(db, owner='radar', now=NOW) == [radar]
    assert db.get(RadarJobWork, (validation['job_id'], calendar.id)).active
    assert calendar.state == 'waiting_provider' and calendar.attempt_count == 0
    assert db.get(RadarWorkItem, session.work_id).lease_token == session.token
    assert claim_work(db, job_id=old_id, now=NOW, slot='normal') is None
    assert claim_work(db, job_id=validation['job_id'], now=NOW, slot='normal') is None
    finish_work(db, session, state='ready', now=NOW)
    db.commit()
    jobs.reconcile_job(db, radar['job_id'], now=NOW)
    claim = claim_work(db, job_id=radar['job_id'], now=NOW, owner='radar', slot='normal')
    assert claim.stage in {'institutional', 'market-bars'}
    assert db.get(RadarWorkItem, session.work_id).attempt_count == 1
    assert db.get(RadarJob, old_id).state == 'split'


def test_resume_filters_before_limit_and_keeps_single_basis_intent(db):
    other = jobs.create_job(db, run_date=date(2026, 11, 4), lanes=['publication'], now=NOW)
    eligible = jobs.create_job(db, run_date=date(2026, 11, 5), lanes=['next_open'], now=NOW)
    assert jobs.resume_jobs(db, owner='validation', now=NOW, limit=1) == [
        {'job_id': eligible.id, 'run_date': '2026-11-05'}]
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    with pytest.raises(ValueError, match='radar_job_owner_mismatch'):
        advance_job(db, other.id, owner='validation', now=NOW)
    assert all(row.attempt_count == 0 for row in db.scalars(select(RadarWorkItem)))


def test_mixed_validation_cursor_continues_on_next_page(db, monkeypatch):
    from test_radar_bounded_planning import DAY, seed, active
    monkeypatch.setattr(jobs, 'WINDOW_PLAN_CANDIDATES', 2)
    seed(db, 8)
    mixed = jobs.create_job(db, run_date=DAY, now=NOW)
    first = mixed.scope['window_planning'].copy()
    original = {w.id for w in active(db, mixed)}
    assert first['processed_candidates'] == 2 and not first['complete']
    ref, = jobs.resume_jobs(db, owner='validation', now=NOW)
    child = db.get(RadarJob, ref['job_id'])
    assert child.scope['window_planning'] == first
    assert {w.id for w in active(db, child)} == original
    jobs.reconcile_job(db, child.id, now=NOW)
    progress = child.scope['window_planning']
    assert progress['revision'] == first['revision'] and progress['processed_candidates'] == 4
    assert progress['cursor'] > first['cursor']


def test_scoped_driver_requires_server_capability_before_submitting(tmp_path):
    from run_daily_radar_jobs import run
    def old_server(*args, **kwargs):
        return {'version': 'radar-durable-v1', 'enabled': True, 'parallel_window_advance': True}
    def unexpected(*args, **kwargs):
        pytest.fail('scoped driver submitted work to an incompatible server')
    with pytest.raises(ValueError, match='radar_scoped_resume_unavailable'):
        run(endpoint='https://example.invalid', token='test', output_dir=tmp_path,
            owner='validation', resume=True, read=old_server, write=unexpected)


def test_validation_completion_does_not_certify_publication(db):
    from test_radar_bounded_planning import DAY, seed
    seed(db, 1, done=True)
    job = jobs.create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    for work in db.scalars(select(RadarWorkItem)):
        work.state = 'ready'
    db.commit()
    jobs.reconcile_job(db, job.id, now=NOW, audit=True)
    status = jobs.job_status(db, job.id)
    assert status['intent_complete'] and not status['data_ready']
    assert status['last_audit']['data_ready'] is None


def test_validation_crons_are_disjoint_from_radar_and_keep_friday_cutoff():
    from pathlib import Path
    import yaml
    from resolve_daily_radar_run_date import resolve_run_date

    directory = Path(__file__).parents[2] / '.github/workflows'
    def cron_slots(name):
        workflow = yaml.load((directory / name).read_text(), Loader=yaml.BaseLoader)
        def values(field, size):
            if field == '*':
                return range(size)
            if field.startswith('*/'):
                return range(0, size, int(field[2:]))
            result = set()
            for part in field.split(','):
                bounds = list(map(int, part.split('-')))
                result.update(range(bounds[0], bounds[-1] + 1))
            return result
        slots = set()
        for trigger in workflow['on']['schedule']:
            minute, hour, _, _, weekday = trigger['cron'].split()
            current = {(d, h, m) for d in values(weekday, 7) for h in values(hour, 24) for m in values(minute, 60)}
            assert not slots.intersection(current) or name == 'daily-radar.yml'
            slots.update(current)
        return slots
    assert cron_slots('daily-radar-validation.yml').isdisjoint(cron_slots('daily-radar.yml'))
    for schedule in ('15 18 * * 1-5', '45 22 * * 1-5'):
        assert resolve_run_date(event_name='schedule', schedule=schedule,
            run_created_at='2026-11-14T00:10:00Z') == date(2026, 11, 13)


def test_simultaneous_postgres_cutover_keeps_one_successor_per_owner(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base

    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as session:
        original_id = jobs.create_job(session, run_date=date(2026, 11, 6), now=NOW).id
    barrier = Barrier(2)
    def resume(owner):
        with Session(pg_engine) as session:
            barrier.wait(timeout=10)
            ref, = jobs.resume_jobs(session, owner=owner, now=NOW)
            return ref['job_id']
    with ThreadPoolExecutor(max_workers=2) as workers:
        radar, validation = workers.map(resume, ['radar', 'validation'])
    with Session(pg_engine) as session:
        assert len(session.scalars(select(RadarJob)).all()) == 3
        status = jobs.job_status(session, original_id)
        assert status['successors'] == {'radar': radar, 'validation': validation}
        assert not status['runnable_now']


def test_handoff_preserves_normal_slot_while_old_provider_request_is_running(db):
    parent = jobs.create_job(db, run_date=date(2026, 11, 6), now=NOW)
    prior = claim_work(db, job_id=parent.id, now=NOW, slot='normal')
    finish_work(db, prior, state='ready', now=NOW)
    db.commit()
    jobs.reconcile_job(db, parent.id, now=NOW)
    held = claim_work(db, job_id=parent.id, now=NOW, slot='normal')
    assert held.stage == 'institutional'
    db.commit()
    pub, = jobs.resume_jobs(db, owner='radar', now=NOW)
    val, = jobs.resume_jobs(db, owner='validation', now=NOW)
    assert occupied_slot(db, val['job_id'], 'normal', NOW) is None
    assert claim_work(db, job_id=pub['job_id'], now=NOW, owner='radar', slot='normal') is None
    finish_work(db, held, state='ready', now=NOW)
    db.commit()
    independent = claim_work(db, job_id=pub['job_id'], now=NOW, owner='radar', slot='normal')
    assert independent.stage == 'institutional' and independent.work_id != held.work_id



def test_dated_registration_reuses_inherited_page_and_frozen_scope(db, monkeypatch):
    from test_radar_bounded_planning import DAY, seed
    monkeypatch.setattr(jobs, 'WINDOW_PLAN_CANDIDATES', 2)
    seed(db, 9)
    mixed = jobs.create_job(db, run_date=DAY, now=NOW)
    mixed.scope = {**mixed.scope, 'managed_symbols': ['2330.TW'], 'managed_deferred': 2}
    db.commit()
    validation = jobs.create_job(db, run_date=DAY, lanes=['next_open','signal_close'], owner='validation', now=NOW)
    radar = jobs.create_job(db, run_date=DAY, lanes=['publication','auxiliary'], owner='radar', now=NOW)
    assert mixed.scope['successors'] == {'validation':validation.id, 'radar':radar.id}
    assert validation.scope['window_planning']['processed_candidates'] >= 4
    assert radar.scope['managed_symbols'] == ['2330.TW']
    assert jobs.due_jobs(db, now=NOW) == []
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    with pytest.raises(ValueError, match='radar_job_owner_mismatch'):
        advance_job(db, validation.id, now=NOW)


def test_legacy_final_reconcile_cannot_requeue_retired_parent(db):
    mixed = jobs.create_job(db, run_date=date(2026,11,6), now=NOW)
    stale_scope = dict(mixed.scope)
    validation, = jobs.resume_jobs(db, owner='validation', now=NOW)
    mixed.scope = stale_scope
    # The preceding executable sets these fields from its old lane calculation.
    mixed.state, mixed.next_attempt_at = 'pending', NOW
    db.commit()
    assert jobs.resume_jobs(db, owner='validation', now=NOW) == [validation]
    assert jobs.job_status(db, mixed.id)['state'] == 'split'
    assert claim_work(db, job_id=mixed.id, now=NOW, slot='normal') is None


def test_large_inheritance_advances_in_bounded_pages(db, monkeypatch):
    from test_radar_bounded_planning import DAY, seed, active
    monkeypatch.setattr(jobs, 'INHERIT_PAGE_SIZE', 8)
    seed(db, 8)
    mixed = jobs.create_job(db, run_date=DAY, now=NOW)
    ids = {w.id for w in active(db,mixed)}
    ref, = jobs.resume_jobs(db, owner='validation', now=NOW)
    child = db.get(RadarJob,ref['job_id'])
    assert len(active(db,child)) <= 8 and child.scope['inherit_cursor'] > 0
    while child.scope.get('inherit_cursor') is not None:
        previous = len(active(db,child))
        jobs.reconcile_job(db,child.id,now=NOW)
        assert len(active(db,child))-previous <= 8
    assert {w.id for w in active(db,child)} == ids


def test_legacy_avwap_keeps_calendar_committed_after_read(pg_engine, monkeypatch):
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.daily_radar import refresh_router
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarRefreshStepRequest
    from ai_stock_sentinel.daily_radar.trading_calendar import save_calendar
    from ai_stock_sentinel.db.models import Base, DailyRadarPreparedRun
    from radar_calendar_support import calendar
    from test_daily_radar_api import FakePhase1AvwapDailyPriceProvider

    Base.metadata.create_all(pg_engine)
    day = date(2026, 6, 1)
    proof = calendar(day-timedelta(days=60), day)
    with Session(pg_engine) as db:
        row = DailyRadarPreparedRun(run_date=day,market='TW', selected_symbols=['2330.TW'],
                                   universe=[], symbol_count=1, step_statuses={})
        db.add(row)
        db.commit()
        row_id = row.id
        original = refresh_router.persist_phase1_avwap_refresh
        def persist(session, *args, **kwargs):
            result = original(session, *args, **kwargs)
            assert 'validation-calendar' not in session.get(DailyRadarPreparedRun,row_id).step_statuses
            with Session(pg_engine) as peer, peer.begin():
                assert save_calendar(peer, proof)
            return result
        monkeypatch.setattr(refresh_router,'persist_phase1_avwap_refresh',persist)
        refresh_router.refresh_daily_radar_avwap_endpoint(DailyRadarRefreshStepRequest(run_date=day), db,
                                                          FakePhase1AvwapDailyPriceProvider())
    with Session(pg_engine) as observer:
        steps = observer.get(DailyRadarPreparedRun,row_id).step_statuses
        assert steps['validation-calendar'] == proof.payload()
        assert steps['refresh-avwap']['status'] == 'completed'


def test_sqlite_cutover_between_select_and_claim_cas(db):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    day = date(2026,11,6)
    original_id = jobs.create_job(db, run_date=day, now=NOW).id
    cutovers = []
    def split_before_update(conn, cursor, statement, params, context, many):
        if not cutovers and statement.startswith('UPDATE radar_work_items SET state='):
            cutovers.append(True)
            with Session(db.get_bind()) as peer:
                jobs.resume_jobs(peer, owner='validation', now=NOW)
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', split_before_update)
    try:
        assert claim_work(db, job_id=original_id, now=NOW, slot='normal') is None
    finally:
        event.remove(engine,'before_cursor_execute',split_before_update)
    assert cutovers and all(w.attempt_count==0 for w in db.scalars(select(RadarWorkItem)))


def test_explicit_publication_recheck_survives_incomplete_handoff(db, monkeypatch):
    monkeypatch.setattr(jobs,'INHERIT_PAGE_SIZE',1)
    day = date(2026,11,6)
    mixed = jobs.create_job(db,run_date=day,lanes=['publication','next_open'],now=NOW)
    mixed.next_attempt_at = NOW + timedelta(hours=8)
    db.commit()
    job = jobs.create_job(db,run_date=day,lanes=['publication'],owner='radar',now=NOW,recheck_publication=True)
    assert job.id == mixed.scope['successors']['radar'] and job.scope.get('recheck_after_inherit') is True
    assert job.id in {row['job_id'] for row in jobs.resume_jobs(db,owner='radar',now=NOW)}
    while job.scope.get('inherit_cursor') is not None:
        jobs.reconcile_job(db,job.id,now=NOW)
    assert 'recheck_after_inherit' not in job.scope

def test_legacy_registration_cannot_bypass_adopted_validation(db):
    mixed = jobs.create_job(db, run_date=date(2026,11,6), now=NOW)
    assert jobs.resume_jobs(db, owner='validation', now=NOW)
    with pytest.raises(ValueError, match='radar_job_owner_mismatch'):
        jobs.create_job(db, run_date=mixed.run_date, lanes=['signal_close', 'next_open'], now=NOW)
    assert jobs.due_jobs(db,now=NOW) == []


def test_dated_registration_prefers_existing_adoption_over_earlier_backoff(db):
    day = date(2026,11,6)
    older = jobs.create_job(db, run_date=day, now=NOW)
    older.next_attempt_at = NOW + timedelta(hours=4)
    db.commit()
    repair = jobs.create_job(db, run_date=day, lanes=['publication','next_open','signal_close'], now=NOW)
    adopted, = jobs.resume_jobs(db, owner='validation', now=NOW)
    requested = jobs.create_job(db, run_date=day, lanes=['signal_close','next_open'], owner='validation', now=NOW)
    assert requested.id == adopted['job_id'] == repair.scope['successors']['validation']
    assert 'successors' not in older.scope


def test_dated_handoff_uses_distinct_pages_with_api_autoflush_disabled(db,monkeypatch):
    from test_radar_bounded_planning import DAY, seed
    from sqlalchemy.orm import Session
    monkeypatch.setattr(jobs,'INHERIT_PAGE_SIZE',2)
    seed(db,8)
    mixed=jobs.create_job(db,run_date=DAY,now=NOW)
    member_ids=db.scalars(select(RadarJobWork.work_id).where(RadarJobWork.job_id==mixed.id)
                          .order_by(RadarJobWork.work_id)).all()
    mixed.next_attempt_at=NOW+timedelta(hours=4)
    db.commit()
    with Session(db.get_bind(),autoflush=False) as api:
        child=jobs.create_job(api,run_date=DAY,lanes=['next_open','signal_close'],owner='validation',now=NOW)
        assert child.scope['inherit_cursor']==member_ids[3]
        assert child.id in {r['job_id'] for r in jobs.resume_jobs(api,owner='validation',now=NOW)}


@pytest.mark.parametrize('replace_symbols', [False, True])
def test_avwap_acceptance_matches_prepared_generation_and_reserves_sqlite_writer(db,monkeypatch,replace_symbols):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.daily_radar import refresh_router
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarRefreshStepRequest
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun
    from test_daily_radar_api import FakePhase1AvwapDailyPriceProvider

    day = date(2026,6,1)
    row = DailyRadarPreparedRun(run_date=day, market='TW',selected_symbols=['2330.TW'],universe=[],symbol_count=1,step_statuses={})
    db.add(row)
    db.commit()
    row_id = row.id
    def cache_only(*args):
        if replace_symbols:
            with Session(db.get_bind()) as peer, peer.begin():
                peer.get(DailyRadarPreparedRun,row_id).selected_symbols=['2317.TW']
        return SimpleNamespace(missing_symbols=[],missing_symbol_reasons={},skipped_symbol_reasons={},
                               fetched_symbols=[],reused_symbols=['2330.TW'])
    monkeypatch.setattr(refresh_router,'persist_phase1_avwap_refresh',cache_only)
    original=refresh_router.update_daily_radar_prepared_step_status
    def check_reserved(*args,**kwargs):
        with Session(db.get_bind()) as peer:
            peer.execute(text('PRAGMA busy_timeout=0'))
            with pytest.raises(OperationalError,match='locked'):
                peer.execute(text('UPDATE daily_radar_prepared_runs SET id=id WHERE 0'))
        return original(*args,**kwargs)
    monkeypatch.setattr(refresh_router,'update_daily_radar_prepared_step_status',check_reserved)
    def refresh():
        return refresh_router.refresh_daily_radar_avwap_endpoint(DailyRadarRefreshStepRequest(run_date=day),db,
                                                                 FakePhase1AvwapDailyPriceProvider())
    if replace_symbols:
        with pytest.raises(HTTPException) as exc:
            refresh()
        assert exc.value.status_code==409 and 'refresh-avwap' not in db.get(DailyRadarPreparedRun,row_id).step_statuses
    else:
        assert refresh().status=='completed'


def test_scoped_handoff_serializes_unowned_dated_registration(pg_engine,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base

    Base.metadata.create_all(pg_engine)
    day = date(2026,11,6)
    with Session(pg_engine) as db:
        jobs.create_job(db,run_date=day,now=NOW)
    splitting, registering, release = Event(), Event(), Event()
    split = jobs._split_due_job
    def hold(db,*args,**kwargs):
        result = split(db,*args,**kwargs)
        splitting.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(jobs,'_split_due_job',hold)
    def resume():
        with Session(pg_engine) as db:
            return jobs.resume_jobs(db,owner='validation',now=NOW)
    def register():
        with Session(pg_engine) as db:
            registering.set()
            try:
                jobs.create_job(db,run_date=day,lanes=['next_open','signal_close'],now=NOW)
            except ValueError as exc:
                return str(exc)
            return 'accepted'
    with ThreadPoolExecutor(max_workers=2) as workers:
        scoped = workers.submit(resume)
        assert splitting.wait(5)
        unowned = workers.submit(register)
        assert registering.wait(5)
        try:
            unowned.result(timeout=.4)
        except TimeoutError:
            pass
        finally:
            release.set()
        assert len(scoped.result(timeout=5))==1
        assert unowned.result(timeout=5)=='radar_job_owner_mismatch'


def test_dated_binding_survives_later_adoption_of_older_pure_job(db):
    day = date(2026,11,6)
    lanes=['signal_close','next_open']
    earlier = jobs.create_job(db,run_date=day,lanes=lanes,now=NOW)
    earlier.next_attempt_at=NOW+timedelta(hours=1)
    db.commit()
    mixed=jobs.create_job(db,run_date=day,now=NOW)
    original,=jobs.resume_jobs(db,owner='validation',now=NOW)
    bound=jobs.create_job(db,run_date=day,lanes=lanes,owner='validation',now=NOW)
    assert bound.id==original['job_id']==mixed.scope['successors']['validation']
    later=NOW+timedelta(hours=2)
    assert earlier.id in {r['job_id'] for r in jobs.resume_jobs(db,owner='validation',now=later)}
    assert jobs.create_job(db,run_date=day,lanes=lanes,owner='validation',now=later).id==bound.id


def test_bounded_resume_includes_newest_dated_validation_and_oldest_due(db):
    jobs_by_day = []
    for day in (1, 2, 3, 4):
        row = RadarJob(job_key=f'dated-{day}', market='TW', lanes=['signal_close'],
            run_date=date(2026, 11, day), next_attempt_at=NOW-timedelta(minutes=day), state='pending',
            scope={'scheduler_owner':'validation', 'dated_registration':True})
        db.add(row)
        jobs_by_day.append(row)
    db.commit()
    refs = jobs.resume_jobs(db, owner='validation', now=NOW, limit=2)
    assert [r['job_id'] for r in refs] == [jobs_by_day[-1].id, jobs_by_day[0].id]
