"""Transaction contracts against an explicitly isolated local PostgreSQL database."""
import pytest
from sqlalchemy.orm import Session
from sqlalchemy import insert, select

from ai_stock_sentinel.db.models import Base
from test_radar_job_postgres import pg_engine
from test_radar_shared_provider_transactions import (
    test_price_providers_preserve_caller_writes as price_case,
    test_raw_collector_preserves_caller_writes as raw_case,
    test_raw_owner_can_release_read_connection_before_fetch as release_case,
)
from test_radar_collection_transactions import (
    test_collector_write_is_rejected_without_ready_attempt as mutation_case,
    test_default_collector_uses_separate_reader_and_keeps_owned_source_writes as reader_case,
    test_collection_rejects_recovery_before_dialect_call as recovery_case,
)


@pytest.mark.parametrize('kind', ['forward', 'research'])
@pytest.mark.parametrize('hit', [False, True])
def test_postgres_prices_preserve_flushed_caller_writes(pg_engine, monkeypatch, kind, hit):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        price_case(db, monkeypatch, True, hit, kind, True)


def test_postgres_raw_collection_preserves_flushed_caller_writes(pg_engine):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        raw_case(db, True, [])


def test_postgres_raw_collection_releases_read_connection(pg_engine):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        release_case(db)


@pytest.mark.parametrize('method', ['flush', 'sql', 'connection', 'driver', 'commit', 'connection_commit', 'swallowed_sql'])
def test_postgres_collection_rejects_writes(pg_engine, method):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        mutation_case(db, method)


@pytest.mark.parametrize('fail_apply', [False, True])
def test_postgres_collection_keeps_independent_source_writes(pg_engine, monkeypatch, fail_apply):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        reader_case(db, monkeypatch, fail_apply)


@pytest.mark.parametrize('method', ['commit_prepared', 'rollback_prepared', 'recover_twophase'])
def test_postgres_collection_blocks_recovery_api_before_dialect(pg_engine, monkeypatch, method):
    with Session(pg_engine, autoflush=False) as db:
        recovery_case(db, monkeypatch, method)


@pytest.mark.parametrize('swallow', [False, True])
def test_postgres_read_only_backstop_rejects_writable_select_cte(pg_engine, swallow):
    from datetime import date
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.jobs import create_job
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    from ai_stock_sentinel.daily_radar.work_handlers import Collected
    from ai_stock_sentinel.db.models import DailyRadarRun, RadarWorkAttempt
    from test_radar_jobs import NOW
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        day = date(2026, 11, 6)
        jid = create_job(db, run_date=day, lanes=['publication'], now=NOW).id
        def collect(claim):
            cte = insert(DailyRadarRun).values(run_date=day, market='TW', status='completed').returning(DailyRadarRun.id).cte('write_attempt')
            try:
                db.execute(select(cte.c.id)).all()
            except Exception:
                if not swallow:
                    raise
                db.rollback()
            return Collected(lambda owner: {'state': 'ready'})
        advance_job(db, jid, handlers=SimpleNamespace(collect=collect), now=NOW)
        attempt = db.scalar(select(RadarWorkAttempt))
        assert attempt.state == 'failed_internal' and attempt.reason == 'radar_collection_mutated_domain'
        assert db.scalar(select(DailyRadarRun)) is None


@pytest.mark.parametrize('escape', ['textual_commands', 'rollback_autocommit', 'rollback_restart', 'initial_autocommit', 'two_phase'])
def test_postgres_collection_protects_text_wrappers_and_connection_restarts(pg_engine, escape):
    from datetime import date
    from types import SimpleNamespace
    from sqlalchemy import text, update
    from ai_stock_sentinel.daily_radar.jobs import create_job
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    from ai_stock_sentinel.daily_radar.work_handlers import Collected
    from ai_stock_sentinel.db.models import DailyRadarRun, RadarWorkAttempt
    from test_radar_jobs import NOW
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        db.add(DailyRadarRun(run_date=date(2026, 11, 6), market='TW', status='completed'))
        db.commit()
        jid = create_job(db, run_date=date(2026, 11, 6), lanes=['publication'], now=NOW).id
        readonly_errors = []
        def collect(claim):
            try:
                if escape == 'initial_autocommit':
                    try:
                        db.connection(execution_options={'isolation_level': 'AUTOCOMMIT'})
                    except Exception:
                        pass  # No rollback: the failed acquisition may be cached.
                    db.execute(update(DailyRadarRun).values(status='failed'))
                elif escape == 'textual_commands':
                    for sql in ('COMMIT', "UPDATE daily_radar_runs SET status='failed' RETURNING id", 'COMMIT'):
                        db.execute(text(sql).columns())
                else:
                    connection = db.connection()
                    connection.rollback()
                    if escape == 'rollback_autocommit':
                        connection.execution_options(isolation_level='AUTOCOMMIT')
                    transaction = connection.begin_twophase() if escape == 'two_phase' else None
                    cte = update(DailyRadarRun).values(status='failed').returning(DailyRadarRun.id).cte('changed')
                    connection.execute(select(cte.c.id)).all()
                    if transaction is not None:
                        transaction.commit()
            except Exception as exc:
                readonly_errors.append(getattr(getattr(exc, 'orig', None), 'pgcode', None))
                db.rollback()
            def fail_acceptance(owner):
                raise ValueError('fixture_acceptance_failed')
            return Collected(fail_acceptance)
        advance_job(db, jid, handlers=SimpleNamespace(collect=collect), now=NOW)
        assert db.scalar(select(DailyRadarRun.status)) == 'completed'
        attempt = db.scalar(select(RadarWorkAttempt))
        assert attempt.state == 'failed_internal' and attempt.reason == 'radar_collection_mutated_domain'
        if escape == 'rollback_restart':
            assert readonly_errors == ['25006']


@pytest.mark.parametrize('api', ['driver', 'text', 'select'])
def test_postgres_collection_cannot_commit_a_select_prefixed_sql_batch(pg_engine, api):
    from datetime import date
    from types import SimpleNamespace
    from sqlalchemy import text, literal_column
    from ai_stock_sentinel.daily_radar.jobs import create_job
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    from ai_stock_sentinel.daily_radar.work_handlers import Collected
    from ai_stock_sentinel.db.models import DailyRadarRun, RadarWorkAttempt
    from test_radar_jobs import NOW
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        db.add(DailyRadarRun(run_date=date(2026, 11, 6), market='TW', status='completed'))
        db.commit()
        jid = create_job(db, run_date=date(2026, 11, 6), lanes=['publication'], now=NOW).id
        def collect(claim):
            sql = "SELECT 1; COMMIT; UPDATE daily_radar_runs SET status='failed' WHERE run_date='2026-11-06' AND market='TW'; COMMIT; SELECT 1"
            try:
                if api == 'driver':
                    db.connection().exec_driver_sql(sql)
                elif api == 'text':
                    db.execute(text(sql))
                else:
                    db.execute(select(literal_column(sql.removeprefix('SELECT '))))
            except Exception:
                db.rollback()  # A swallowed violation must still reject the attempt.
            def fail_acceptance(owner):
                raise ValueError('fixture_acceptance_failed')
            return Collected(fail_acceptance)
        advance_job(db, jid, handlers=SimpleNamespace(collect=collect), now=NOW)
        assert db.scalar(select(DailyRadarRun.status)) == 'completed', 'collection SQL committed domain data before acceptance'
        attempt = db.scalar(select(RadarWorkAttempt))
        assert attempt.state == 'failed_internal' and attempt.reason == 'radar_collection_mutated_domain'


@pytest.mark.parametrize('snapshots', [False, True])
@pytest.mark.parametrize('failure', ['none', 'market', 'write'])
def test_postgres_legacy_run_collects_before_writes_and_rolls_back_failures(pg_engine, monkeypatch, snapshots, failure):
    from test_radar_legacy_transactions import test_one_shot_collects_every_source_before_domain_write as run_case
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        run_case(db, monkeypatch, snapshots, True, failure)


@pytest.mark.parametrize('context', ['lending', 'full_margin'])
def test_postgres_legacy_context_releases_read_before_http(pg_engine, context):
    from test_radar_legacy_transactions import test_legacy_context_owner_releases_prepared_read_before_provider as context_case
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        context_case(db, context)


def test_postgres_market_bars_collect_before_writes(pg_engine):
    from test_radar_legacy_transactions import test_market_bar_batch_collects_all_futures_before_first_write as bars_case
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        bars_case(db)


@pytest.mark.parametrize('context', ['lending', 'full_margin'])
def test_postgres_context_acceptance_keeps_intervening_legacy_refresh(pg_engine, monkeypatch, context):
    from test_radar_legacy_transactions import test_durable_context_acceptance_reuses_intervening_legacy_refresh as reuse_case
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        reuse_case(db, monkeypatch, context)
