"""Collectors cannot publish writes, including flushed or swallowed mutations."""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import InvalidRequestError

from ai_stock_sentinel.daily_radar.jobs import create_job
from ai_stock_sentinel.daily_radar.work_executor import advance_job
from ai_stock_sentinel.daily_radar.work_handlers import Collected
from ai_stock_sentinel.db.models import DailyRadarRun, RadarWorkAttempt, RadarSourceSnapshot
from test_radar_jobs import db, NOW

DAY = date(2026, 11, 6)


def test_collection_read_values_can_contain_semicolons(db):
    from sqlalchemy import literal
    from ai_stock_sentinel.daily_radar.collection_transactions import read_only_collection
    with read_only_collection(db):
        assert db.scalar(select(literal('value;with;separators'))) == 'value;with;separators'
        assert db.scalar(text('SELECT 1;   ')) == 1


@pytest.mark.parametrize('method', ['commit_prepared', 'rollback_prepared', 'recover_twophase'])
def test_collection_rejects_recovery_before_dialect_call(db, monkeypatch, method):
    from ai_stock_sentinel.daily_radar.collection_transactions import read_only_collection, CollectionMutationError
    calls = []
    # Recovery bypasses ordinary ConnectionEvents. Observe the mutation boundary
    # without requiring an existing prepared transaction or changing server config.
    dialect_method = {'commit_prepared': 'do_commit_twophase', 'rollback_prepared': 'do_rollback_twophase',
        'recover_twophase': 'do_recover_twophase'}[method]
    monkeypatch.setattr(db.get_bind().dialect, dialect_method, lambda *args, **kwargs: calls.append(method))
    connection = None
    with pytest.raises(CollectionMutationError):
        with read_only_collection(db):
            connection = db.connection()
            try:
                call = getattr(connection, method)
                call() if method == 'recover_twophase' else call('fixture-xid', recover=True)
            except CollectionMutationError:
                pass  # Even a swallowed recovery attempt must reject the scope.
    assert calls == []
    assert method not in connection.__dict__, 'a scoped recovery override leaked after cleanup'


@pytest.mark.parametrize('method', ['pending', 'flush', 'sql', 'connection', 'driver', 'commit', 'connection_commit', 'swallowed_flush', 'swallowed_sql'])
def test_collector_write_is_rejected_without_ready_attempt(db, method):
    jid = create_job(db, run_date=DAY, lanes=['publication'], now=NOW).id
    def collect(claim):
        try:
            if method == 'connection_commit':
                db.connection().commit()
            elif method in {'sql', 'connection', 'driver', 'swallowed_sql'}:
                statement = text("INSERT INTO daily_radar_runs (run_date, market, status) VALUES ('2026-11-06', 'TW', 'completed')")
                if method == 'driver':
                    db.connection().exec_driver_sql(str(statement))
                else:
                    (db.connection() if method == 'connection' else db).execute(statement)
            else:
                db.add(DailyRadarRun(run_date=DAY, market='TW', status='completed'))
                if method == 'commit':
                    db.commit()
                elif method != 'pending':
                    db.flush()
        except Exception:
            if not method.startswith('swallowed_'):
                raise
            db.rollback()
        return Collected(lambda session: {'state': 'ready', 'result': {'target_ready': True}})
    result = advance_job(db, jid, handlers=SimpleNamespace(collect=collect), now=NOW)
    assert result['data_ready'] is False
    assert db.scalar(select(func.count()).select_from(DailyRadarRun)) == 0
    attempt = db.scalar(select(RadarWorkAttempt))
    assert attempt.state == 'failed_internal'
    assert attempt.reason == 'radar_collection_mutated_domain'


@pytest.mark.parametrize('fail_apply', [False, True])
def test_default_collector_uses_separate_reader_and_keeps_owned_source_writes(db, monkeypatch, fail_apply):
    from ai_stock_sentinel.daily_radar import work_executor
    from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
    readers = []
    class Handler:
        def __init__(self, reader):
            assert reader is not db
            readers.append(reader)
            self.db = reader
            self.store = SourceSnapshotStore(reader.get_bind())

        def collect(self, claim):
            assert self.db.scalar(select(func.count()).select_from(DailyRadarRun)) == 0
            self.db.rollback()
            self.store.record({'provider': 'fixture', 'operation': 'transaction-test', 'parser_version': 'fixture-v1'}, {'rows': []})
            # This second transaction must receive the same read-only protection.
            assert self.db.scalar(select(func.count()).select_from(RadarSourceSnapshot)) == 1
            def apply(owner):
                assert not self.db.in_transaction()
                with pytest.raises(InvalidRequestError):
                    self.db.execute(select(1))  # A closed reader cannot escape into acceptance.
                owner.add(DailyRadarRun(run_date=DAY, market='TW', status='completed'))
                if fail_apply:
                    owner.flush()
                    raise ValueError('fixture_apply_failed')
                return {'state': 'ready', 'result': {'status': 'open'}}
            return Collected(apply)
    monkeypatch.setattr(work_executor, 'WorkHandlers', Handler)
    jid = create_job(db, run_date=DAY, lanes=['publication'], now=NOW).id
    advance_job(db, jid, now=NOW)
    assert len(readers) == 1
    attempt = db.scalar(select(RadarWorkAttempt))
    assert attempt.state == ('waiting_provider' if fail_apply else 'ready'), (attempt.state, attempt.reason, attempt.result)
    assert db.scalar(select(func.count()).select_from(DailyRadarRun)) == int(not fail_apply)
    assert db.scalar(select(func.count()).select_from(RadarSourceSnapshot)) == 1
