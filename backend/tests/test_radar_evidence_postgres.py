"""Real PostgreSQL evidence/atomic-publication regressions; local DSN guard reused."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier
import json
import pytest

from sqlalchemy import select, func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from test_radar_job_postgres import pg_engine, _migration
from ai_stock_sentinel.db.models import (
    DailyRadarRun, DailyRadarCandidate, Phase1AvwapSnapshot,
    RadarSourceSnapshot, RadarInputManifest, RadarRunInput,
)
from ai_stock_sentinel.daily_radar.input_manifests import save_manifest, serialize_publication
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
from ai_stock_sentinel.daily_radar.service import run_daily_radar
from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_fixture_records


def prepare(engine):
    for model in (DailyRadarRun, DailyRadarCandidate, Phase1AvwapSnapshot):
        model.__table__.create(engine)
    with engine.begin() as connection:
        _migration(connection, 'cd4e5f6a7b8c_add_radar_evidence_snapshots.py')


def test_evidence_migration_roundtrip_preserves_existing_runs(pg_engine):
    prepare(pg_engine)
    with Session(pg_engine) as db:
        row = DailyRadarRun(run_date=date(2026, 11, 6), market='TW', status='completed')
        db.add(row)
        db.commit()
    with pg_engine.begin() as connection:
        _migration(connection, 'cd4e5f6a7b8c_add_radar_evidence_snapshots.py', 'downgrade')
        _migration(connection, 'cd4e5f6a7b8c_add_radar_evidence_snapshots.py')
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(DailyRadarRun)) == 1


def test_concurrent_identical_scoring_publishes_one_atomic_revision(pg_engine, monkeypatch):
    prepare(pg_engine)
    root = Path(__file__).parent / 'fixtures/daily_radar'
    records = load_daily_radar_fixture_records(root)
    context = json.loads((root / 'market_context.json').read_text())
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', 'true')
    start = Barrier(2)
    def worker():
        with Session(pg_engine) as db:
            start.wait(timeout=10)
            run = run_daily_radar(date(2026, 5, 29), 'TW', session=db, records=records,
                market_context=context, allow_fixture_fallback=False)
            assert run.status == 'completed'
            run_id = run.id
            db.commit()
            return run_id
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: worker(), range(2)))
    assert len(set(ids)) == 1
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(DailyRadarRun)) == 1
        assert db.scalar(select(func.count()).select_from(RadarInputManifest)) == 1
        assert db.scalar(select(func.count()).select_from(RadarRunInput)) == 1


def test_evidence_capture_releases_all_connections_before_http(pg_engine):
    prepare(pg_engine)
    def fetch():
        assert pg_engine.pool.checkedout() == 0
        return {'stat': 'OK', 'data': []}
    store = SourceSnapshotStore(pg_engine)
    result = store.capture(provider='twse', url='https://www.twse.com.tw/report', params={}, fetch=fetch)
    assert result[1]['snapshot_id'] > 0
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(RadarSourceSnapshot)) == 1


def test_publication_reserves_history_before_freezing_inputs(pg_engine):
    prepare(pg_engine)
    with Session(pg_engine) as publishing:
        serialize_publication(publishing, market='TW', run_date=date(2026, 11, 6))
        with pg_engine.connect() as repairing:
            repairing.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError):
                repairing.execute(text('LOCK TABLE daily_radar_runs, daily_radar_candidates IN SHARE ROW EXCLUSIVE MODE'))
        publishing.rollback()


def test_cached_provenance_survives_jsonb_parameter_reordering(pg_engine):
    prepare(pg_engine)
    kwargs = dict(provider='twse', url='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST',
        params={'response': 'json', 'date': '20261101'}, fetch=lambda: {'stat': 'OK'})
    first = SourceSnapshotStore(pg_engine).capture(**kwargs)
    again = SourceSnapshotStore(pg_engine).capture(**kwargs)
    assert first == again
    assert first[1]['source_url'].endswith('?response=json&date=20261101')
