from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from ai_stock_sentinel.db.models import RadarJob, RadarWorkItem, RadarJobWork, RadarWorkAttempt, DailyRadarRun
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.daily_radar.work_queue import ensure_work, claim_work, finish_work, LostLease, retry_delay


@compiles(JSONB, 'sqlite')
def jsonb(type_, compiler, **kwargs):
    return 'JSON'


NOW = datetime(2026, 11, 6, 17, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine('sqlite+pysqlite:///' + str(tmp_path / 'queue.sqlite'))
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        RadarJob, RadarWorkItem, RadarJobWork, RadarWorkAttempt, DailyRadarRun)])
    yield engine
    engine.dispose()


def work(db, key='one', **kwargs):
    return ensure_work(db, identity={'stage': key}, stage='test', lane='publication', run_date=date(2026, 11, 6),
        scope={'symbol': '2330.TW'}, input_revision='a' * 64, retry_at=NOW, **kwargs)


def test_work_identity_is_idempotent_and_attempts_are_durable(engine):
    with Session(engine) as db:
        first = work(db)
        assert work(db).id == first.id
        db.commit()
        claim = claim_work(db, work_id=first.id, now=NOW)
        db.commit()
    with Session(engine) as db:
        assert claim_work(db, work_id=claim.work_id, now=NOW + timedelta(seconds=1)) is None
        finish_work(db, claim, state='ready', result={'complete': True}, now=NOW + timedelta(seconds=2))
        db.commit()
    with Session(engine) as db:
        assert db.get(RadarWorkItem, claim.work_id).state == 'ready'
        assert db.get(RadarWorkAttempt, claim.attempt_id).result == {'complete': True}


def test_expired_worker_cannot_commit_domain_writes_or_replace_new_owner(engine):
    with Session(engine) as db:
        item = work(db)
        db.commit()
        old = claim_work(db, work_id=item.id, now=NOW, lease_seconds=1)
        db.commit()
        new = claim_work(db, work_id=item.id, now=NOW + timedelta(seconds=2))
        db.commit()
    with Session(engine) as db:
        with pytest.raises(LostLease), db.begin():
            db.add(DailyRadarRun(run_date=date(2026, 11, 6), market='TW', status='completed'))
            db.flush()
            finish_work(db, old, state='ready', now=NOW + timedelta(seconds=3))
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(DailyRadarRun)) == 0
        assert db.get(RadarWorkItem, new.work_id).lease_token == new.token
        assert db.get(RadarWorkAttempt, old.attempt_id).state == 'lost_lease'


def test_provider_wait_obeys_retry_at_without_losing_original_date(engine):
    with Session(engine) as db:
        item = work(db)
        db.commit()
        first = claim_work(db, work_id=item.id, now=NOW)
        finish_work(db, first, state='waiting_provider', reason='source_timeout',
            retry_at=NOW + timedelta(hours=1), now=NOW)
        db.commit()
        assert claim_work(db, work_id=item.id, now=NOW + timedelta(minutes=59)) is None
        again = claim_work(db, work_id=item.id, now=NOW + timedelta(hours=1))
        assert again.run_date == date(2026, 11, 6)
        assert again.token != first.token


def test_review_states_do_not_blindly_retry_and_backoff_is_bounded(engine):
    with Session(engine) as db:
        item = work(db, state='needs_review')
        db.commit()
        assert claim_work(db, work_id=item.id, now=NOW + timedelta(days=20)) is None
    assert retry_delay(1) == 60
    assert retry_delay(10**9) == 3600
    assert retry_delay(4, progressed=True) == 60
    assert retry_delay(2, retry_after=900) == 900
