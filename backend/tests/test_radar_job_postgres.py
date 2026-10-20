"""Optional real PostgreSQL checks; only an isolated local radar_test database is accepted.

RADAR_TEST_POSTGRES_DSN=postgresql+psycopg2:///radar_test?host=/tmp/...&port=... uv run pytest tests/test_radar_job_postgres.py -q
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import importlib.util
import os
from pathlib import Path
from threading import Barrier, local
from tempfile import TemporaryDirectory
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_stock_sentinel.clock import today_taipei
from ai_stock_sentinel.data_sources.fundamental import router, service
from ai_stock_sentinel.data_sources.fundamental.normalizers import normalize_finmind_statement_rows
from ai_stock_sentinel.data_sources.fundamental.schemas import FundamentalBackfillRequest
from ai_stock_sentinel.db.models import (
    CompanyDividendEvent, CompanyFundamentalPeriod, DailyRadarPreparedRun, FundamentalBackfillJob, StockRawData,
)
from ai_stock_sentinel.db.session import Base


def _migration(connection, filename, direction='upgrade'):
    path = Path(__file__).parents[1] / 'alembic/versions' / filename
    spec = importlib.util.spec_from_file_location('radar_test_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, direction)()


NEW_MIGRATION = 'ab2c3d4e5f6a_scope_fundamental_backfill_jobs.py'


def _local_test_url(dsn):
    url = make_url(dsn)
    if (url.drivername not in ('postgresql', 'postgresql+psycopg2') or url.database != 'radar_test'
            or url.host or url.port or set(url.query) - {'host', 'port'}
            or any(os.getenv(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'))):
        raise ValueError('Only an explicit local test socket/database target is permitted')
    socket, port = url.query.get('host'), url.query.get('port')
    if (not isinstance(socket, str) or ',' in socket or not Path(socket).is_absolute()
            or not isinstance(port, str) or not port.isascii() or not port.isdecimal()
            or not 0 < int(port) < 65536):
        raise ValueError('A single absolute socket directory and explicit port are required')
    try:
        directory = Path(socket).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError('Test socket directory must exist') from exc
    temp_root = Path('/tmp').resolve(strict=True)
    if directory == temp_root or not directory.is_relative_to(temp_root) or not directory.is_dir():
        raise ValueError('Integration tests require database radar_test over an isolated /tmp Unix socket')
    # Forward only the checked, canonical target; libpq never receives an unchecked host list.
    return url.set(query={'host': str(directory), 'port': port})


@pytest.mark.parametrize('dsn', [
    'postgresql+psycopg2:///radar_test?host=/tmp/../var/run/postgresql',
    'postgresql+psycopg2:///radar_test?host=/tmp/missing,db.example.invalid',
    'postgresql+psycopg2:///radar_test?host=/tmp/socket&hostaddr=192.0.2.1',
    'postgresql+psycopg2:///radar_test?host=/tmp/socket&dbname=production',
    'postgresql+psycopg2:///radar_test?host=/tmp/socket&service=production',
])
def test_postgres_target_rejects_effective_address_overrides(dsn):
    with pytest.raises(ValueError):
        _local_test_url(dsn + '&port=55476')


def test_postgres_target_canonicalizes_socket_and_rejects_symlinks_and_environment(monkeypatch):
    for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'):
        monkeypatch.delenv(key, raising=False)
    with TemporaryDirectory(prefix='radar-target-', dir='/tmp') as directory:
        socket = Path(directory)
        dsn = f'postgresql+psycopg2:///radar_test?host={socket}/.&port=55476'
        assert _local_test_url(dsn).query['host'] == str(socket.resolve())
        link = socket / 'outside'
        link.symlink_to(Path(__file__).parents[1], target_is_directory=True)
        with pytest.raises(ValueError):
            _local_test_url(f'postgresql+psycopg2:///radar_test?host={link}&port=55476')
        for parameter in ('hostaddr=192.0.2.1', 'dbname=production', 'service=production',
                f'host={socket}', 'port=5432,55476'):
            with pytest.raises(ValueError):
                _local_test_url(dsn + '&' + parameter)
        for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'):
            monkeypatch.setenv(key, 'unsafe')
            with pytest.raises(ValueError):
                _local_test_url(dsn)
            monkeypatch.delenv(key)


@pytest.fixture
def pg_engine():
    dsn = os.getenv('RADAR_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('RADAR_TEST_POSTGRES_DSN is unset; real local PostgreSQL check')
    url = _local_test_url(dsn)
    admin = create_engine(url)
    schema = 'radar_six_' + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    try:
        with engine.begin() as connection:
            _migration(connection, '5f6a7b8c9d0e_add_fundamental_backfill_jobs.py')
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


def test_migration_preserves_legacy_jobs_and_scopes_running_uniqueness(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(text("INSERT INTO fundamental_backfill_jobs(id, raw_pool_date, symbols, next_after_symbol) "
            "VALUES ('legacy', '2026-10-15', '[\"2330.TW\",\"2454.TW\"]', '2330.TW')"))
        before = dict(connection.execute(text('SELECT * FROM fundamental_backfill_jobs')).mappings().one())
        _migration(connection, NEW_MIGRATION)
        after = dict(connection.execute(text('SELECT * FROM fundamental_backfill_jobs')).mappings().one())
        assert {key: after[key] for key in before} == before
        assert after['purpose'] == 'maintenance' and after['radar_stage'] is None
        connection.execute(text("INSERT INTO fundamental_backfill_jobs(id,purpose,radar_stage,raw_pool_date,symbols) "
            "VALUES ('radar','radar_ttm_eps','prepared','2026-10-15','[]')"))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("INSERT INTO fundamental_backfill_jobs(id,purpose,radar_stage,raw_pool_date,symbols) "
                "VALUES ('duplicate','radar_ttm_eps','prepared','2026-10-15','[]')"))
        connection.execute(text("INSERT INTO fundamental_backfill_jobs(id,purpose,radar_stage,raw_pool_date,symbols) "
            "VALUES ('other-stage','radar_ttm_eps','raw_pool','2026-10-15','[]')"))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("INSERT INTO fundamental_backfill_jobs(id,purpose,radar_stage,symbols) "
                "VALUES ('undated','radar_ttm_eps','prepared','[]')"))
        _migration(connection, NEW_MIGRATION, 'downgrade')
        restored = dict(connection.execute(text("SELECT * FROM fundamental_backfill_jobs WHERE id='legacy'")).mappings().one())
        assert restored == before


def test_concurrent_page_fetches_have_no_open_transaction_and_only_one_cursor_commits(pg_engine, monkeypatch):
    with pg_engine.begin() as connection:
        _migration(connection, NEW_MIGRATION)
    Base.metadata.create_all(pg_engine, tables=[CompanyFundamentalPeriod.__table__, CompanyDividendEvent.__table__,
        DailyRadarPreparedRun.__table__, StockRawData.__table__])
    day = today_taipei()
    with Session(pg_engine) as session:
        session.add(DailyRadarPreparedRun(run_date=day, market='TW', selected_symbols=['2330.TW', '2454.TW'],
            universe=[], symbol_count=2, step_statuses={'prepare-universe': {'status': 'completed'}}))
        # Retained report jobs must not enter the maintenance queue.
        session.add(FundamentalBackfillJob(id=str(uuid4()), symbols=['3008.TW'],
            raw_pool_date=date(2026, 10, 14), purpose='radar_ttm_eps', radar_stage='prepared'))
        job = service.create_fundamental_backfill_job(session, symbols=['2330.TW', '2454.TW'], raw_pool_date=None)
        job_id = job.id
        session.commit()
    barrier = Barrier(2, timeout=15)
    state = local()
    class Historical:
        def fetch_periods(self, symbol):
            assert not state.session.in_transaction()
            barrier.wait()
            return normalize_finmind_statement_rows([
                {'date': d, 'type': 'EPS', 'value': 1}
                for d in ['2024-12-31', '2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30']
            ], symbol=symbol)
    monkeypatch.setattr(service, 'MopsHistoricalEpsProvider', Historical)
    class Dividends:
        def fetch_dividend_rows(self, symbol):
            assert not state.session.in_transaction()
            return []
        def fetch_statement_rows(self, symbol):
            pytest.fail('Seven MOPS quarters already satisfy maintenance')
    monkeypatch.setattr(service, '_bounded_fundamental_backfill_provider', Dividends)
    def worker():
        with Session(pg_engine) as session:
            state.session = session
            try:
                return router.backfill_fundamentals_endpoint(
                    FundamentalBackfillRequest(job_id=job_id, limit=1), db=session)
            except HTTPException as exc:
                return exc.detail
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert sum(result.get('status') == 'ok' for result in results) == 1, results
    assert sum(result.get('code') == 'fundamental_backfill_cursor_mismatch' for result in results) == 1, results
    with Session(pg_engine) as session:
        jobs = session.scalars(select(FundamentalBackfillJob).where(FundamentalBackfillJob.purpose == 'maintenance')).all()
        assert len(jobs) == 1 and jobs[0].next_after_symbol == '2330.TW'
        assert len(session.scalars(select(CompanyFundamentalPeriod)).all()) == 7
