from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from urllib.error import HTTPError

import pytest
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from test_radar_job_postgres import pg_engine
from test_radar_work_postgres import setup_queue
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
from ai_stock_sentinel.daily_radar.work_queue import LostLease, database_now
from ai_stock_sentinel.db.models import RadarSourceSnapshot, RadarWorkItem


def enable(monkeypatch):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')


def test_shared_source_has_one_fetch_and_no_checked_out_connection(pg_engine, monkeypatch):
    setup_queue(pg_engine); enable(monkeypatch)
    entered, release = Event(), Event()
    def fetch():
        assert pg_engine.pool.checkedout() == 0
        entered.set()
        assert release.wait(timeout=10)
        return {'price': 100}
    kwargs = dict(provider='twse', url='https://www.twse.com.tw/report', params={})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: SourceSnapshotStore(pg_engine).capture(**kwargs, fetch=fetch))
        assert entered.wait(timeout=10)
        try:
            with pytest.raises(SourceDependencyBusy):
                SourceSnapshotStore(pg_engine).capture(**kwargs, fetch=lambda: pytest.fail('duplicate fetch'))
        finally:
            release.set()
        first = future.result(timeout=10)
    again = SourceSnapshotStore(pg_engine).capture(**kwargs, fetch=lambda: pytest.fail('cached fetch'))
    assert first == again


def test_429_honors_retry_after_and_does_not_become_empty_data(pg_engine, monkeypatch):
    setup_queue(pg_engine); enable(monkeypatch)
    kwargs = dict(provider='twse', url='https://www.twse.com.tw/report', params={})
    def fail():
        raise HTTPError(kwargs['url'], 429, 'rate limited', {'Retry-After': '900'}, None)
    with pytest.raises(HTTPError):
        SourceSnapshotStore(pg_engine).capture(**kwargs, fetch=fail)
    with Session(pg_engine) as db:
        now = database_now(db)
        rows = list(db.scalars(select(RadarWorkItem)))
        assert all(row.state == 'waiting_provider' for row in rows)
        assert all(row.retry_at >= now + timedelta(seconds=895) for row in rows)
        assert db.scalar(select(func.count()).select_from(RadarSourceSnapshot)) == 0
    with pytest.raises(SourceDependencyBusy):
        SourceSnapshotStore(pg_engine).capture(**kwargs, fetch=lambda: pytest.fail('cooldown ignored'))


def test_expired_source_response_cannot_replace_the_new_capture(pg_engine, monkeypatch):
    setup_queue(pg_engine); enable(monkeypatch)
    entered, release = Event(), Event()
    kwargs = dict(provider='twse', url='https://www.twse.com.tw/report', params={})
    def old_fetch():
        entered.set()
        assert release.wait(timeout=10)
        return {'price': 100}
    with ThreadPoolExecutor(1) as pool:
        old = pool.submit(lambda: SourceSnapshotStore(pg_engine).capture(**kwargs, fetch=old_fetch))
        assert entered.wait(timeout=10)
        with Session(pg_engine) as db, db.begin():
            past = database_now(db) - timedelta(seconds=1)
            db.execute(update(RadarWorkItem).where(RadarWorkItem.state == 'running').values(lease_expires_at=past))
        new = SourceSnapshotStore(pg_engine).capture(**kwargs, fetch=lambda: {'price': 101})
        release.set()
        with pytest.raises(LostLease):
            old.result(timeout=10)
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(RadarSourceSnapshot)) == 1
        assert db.get(RadarSourceSnapshot, new[1]['snapshot_id']).payload == {'price': 101}


def test_legacy_snapshot_acceptance_creates_a_source_lock_before_new_capture(pg_engine,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from threading import Event
    from ai_stock_sentinel.daily_radar.work_executor import fence_sources
    from ai_stock_sentinel.daily_radar import source_work
    from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    from test_radar_evidence_postgres import prepare
    prepare(pg_engine,full=True)
    old=SourceSnapshotStore(pg_engine)
    params={'symbol':'2330.TW','start_date':'2026-06-01','end_date':'2026-06-08','auto_adjust':False,'actions':True}
    request=old._sdk_request('research-unadjusted-history',params)
    old.record(request,{'rows':[{'date':'2026-06-01','close':100}]})
    key=fingerprint({'kind':'source_capture','request_hash':fingerprint(request)})
    with Session(pg_engine) as db:
        assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.work_key==key)) is None
        fence_sources(db,old)
        assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.work_key==key)) is not None
        monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
        entered=Event();real=source_work.ensure_work
        def ensure(*args,**kwargs):
            if kwargs['identity'].get('kind')=='source_capture':entered.set()
            return real(*args,**kwargs)
        monkeypatch.setattr(source_work,'ensure_work',ensure)
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(lambda:SourceSnapshotStore(pg_engine).capture_sdk(operation='research-unadjusted-history',params=params,
                fetch=lambda:{'rows':[{'date':'2026-06-01','close':101}]},reuse=False))
            assert entered.wait(5)
            with pytest.raises(TimeoutError):future.result(timeout=.1)
            db.commit()
            _,proof=future.result(timeout=10)
            assert proof['snapshot_sha256']!=next(iter(old.seen.values()))['snapshot_sha256']
