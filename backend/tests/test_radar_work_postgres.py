from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import Session
import pytest

from test_radar_job_postgres import pg_engine, _migration
from test_radar_evidence_postgres import prepare
from ai_stock_sentinel.db.models import RadarJob, RadarWorkItem, RadarJobWork, RadarWorkAttempt, DailyRadarRun
from ai_stock_sentinel.daily_radar.work_queue import ensure_work, claim_work, finish_work, LostLease, database_now


def setup_queue(engine):
    prepare(engine)
    with engine.begin() as connection:
        _migration(connection, 'de5f6a7b8c9d_add_radar_durable_work.py')


def test_skip_locked_claims_distinct_units_and_migration_downgrade(pg_engine):
    setup_queue(pg_engine)
    with Session(pg_engine) as db:
        now = database_now(db)
        job = RadarJob(job_key='j' * 64, market='TW', run_date=date(2026, 11, 6), lanes=['publication'], scope={}, state='pending')
        db.add(job); db.flush()
        items = [ensure_work(db, identity={'n': i}, stage='test', lane='publication', run_date=job.run_date,
            scope={}, input_revision='a' * 64, retry_at=now) for i in range(2)]
        for item in items: db.add(RadarJobWork(job_id=job.id, work_id=item.id, required=True, active=True))
        db.commit()
        jid, ids = job.id, [item.id for item in items]
    with Session(pg_engine) as first, Session(pg_engine) as second:
        first.scalar(select(RadarWorkItem).where(RadarWorkItem.id == ids[0]).with_for_update())
        other = claim_work(second, job_id=jid)
        assert other.work_id == ids[1]
        second.commit()
        own = claim_work(first, job_id=jid)
        assert own.work_id == ids[0]
        first.commit()
    with pg_engine.begin() as connection:
        _migration(connection, 'de5f6a7b8c9d_add_radar_durable_work.py', 'downgrade')
        _migration(connection, 'de5f6a7b8c9d_add_radar_durable_work.py')


def test_stale_worker_rolls_back_domain_result_and_leaves_new_lease(pg_engine):
    setup_queue(pg_engine)
    with Session(pg_engine) as db:
        now = database_now(db)
        item = ensure_work(db, identity={'n': 1}, stage='test', lane='publication', run_date=date(2026, 11, 6),
            scope={}, input_revision='a' * 64, retry_at=now)
        db.commit()
        old = claim_work(db, work_id=item.id, now=now, lease_seconds=1)
        db.commit()
        new = claim_work(db, work_id=item.id, now=now + timedelta(seconds=2))
        db.commit()
    with Session(pg_engine) as db:
        with pytest.raises(LostLease), db.begin():
            db.add(DailyRadarRun(run_date=date(2026, 11, 6), market='TW', status='completed'))
            db.flush()
            finish_work(db, old, state='ready', now=now + timedelta(seconds=3))
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(DailyRadarRun)) == 0
        assert db.get(RadarWorkItem, new.work_id).lease_token == new.token


def test_real_postgres_runs_both_window_handlers_after_restart(pg_engine,monkeypatch):
    from ai_stock_sentinel.db.session import Base
    from test_radar_work_execution import test_real_window_handlers_resume_both_bases_without_replaying_finished_window
    setup_queue(pg_engine)
    Base.metadata.create_all(pg_engine,checkfirst=True)
    with Session(pg_engine) as db:
        test_real_window_handlers_resume_both_bases_without_replaying_finished_window(db,monkeypatch)


def test_postgres_old_cutoff_and_same_cutoff_cas_preserve_new_result(pg_engine):
    from test_radar_review_regressions import test_older_collected_skip_cannot_replace_newer_validated_outcome,test_result_changed_at_same_cutoff_is_not_overwritten_by_stale_collection
    setup_queue(pg_engine)
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    DailyRadarForwardValidationResult.__table__.create(pg_engine)
    with Session(pg_engine) as db:
        test_older_collected_skip_cannot_replace_newer_validated_outcome(db)
        from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
        db.query(DailyRadarForwardValidationResult).delete();db.commit()
        test_result_changed_at_same_cutoff_is_not_overwritten_by_stale_collection(db)


def test_shared_window_occupies_the_claiming_jobs_slot_only(pg_engine):
    setup_queue(pg_engine)
    with Session(pg_engine) as db:
        now = database_now(db)
        jobs = [RadarJob(job_key=str(i) * 64, market='TW', run_date=date(2026, 11, i + 6),
                         lanes=['signal_close'], scope={}, state='pending') for i in range(2)]
        db.add_all(jobs)
        db.flush()
        a, b = jobs
        items = [ensure_work(db, identity={'window': i}, stage='window', lane='signal_close', run_date=a.run_date,
                            scope={}, input_revision='a' * 64, retry_at=now) for i in range(2)]
        shared, other = items
        db.add_all([RadarJobWork(job_id=job.id, work_id=item.id, required=True, active=active)
                    for job, item, active in ((a, shared, True), (b, shared, False), (b, other, True))])
        ids = (a.id, b.id, shared.id, other.id)
        db.commit()
    with Session(pg_engine) as db:
        first = claim_work(db, job_id=ids[0], slot='window')
        assert first.work_id == ids[2]
        db.commit()
    with Session(pg_engine) as db:
        assert claim_work(db, job_id=ids[0], slot='window') is None
        second = claim_work(db, job_id=ids[1], slot='window')
        assert second.work_id == ids[3]
        db.commit()
