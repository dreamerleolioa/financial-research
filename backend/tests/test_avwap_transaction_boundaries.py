"""Snapshot writes belong to the batch owner, including already flushed writes."""
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, event
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyRadarPreparedRun, Phase1AvwapSnapshot
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.daily_radar import refresh_router, dependencies
from ai_stock_sentinel.phase1_avwap.provider import ArchiveFirstDailyPriceProvider, DailyPriceHistoryResult
from ai_stock_sentinel.phase1_avwap.repository import upsert_phase1_avwap_snapshot
from ai_stock_sentinel.phase1_avwap.service import refresh_phase1_avwap_snapshots_for_symbols
from test_radar_jobs import db
from test_phase1_avwap import _bars

DAY = date(2026, 6, 5)
SYMBOLS = ['2330.TW', '2317.TW']


class Fallback:
    def __init__(self, db, *, missing_second=False, require_released=False):
        self.db = db
        self.missing_second = missing_second
        self.require_released = require_released
        self.calls = []

    def fetch_history_result(self, symbol, *, start_date, end_date):
        if self.require_released:
            assert not self.db.in_transaction(), 'caller read transaction spans remote fetch'
            assert self.db.get_bind().pool.checkedout() == 0
        self.calls.append(symbol)
        end = end_date - timedelta(days=1) if self.missing_second and symbol == SYMBOLS[1] else end_date
        return DailyPriceHistoryResult(_bars(end), 'fixture', 'fixture_daily_prices')


def rows(db):
    return {row.symbol: row for row in db.scalars(select(Phase1AvwapSnapshot)).all()}


def seed(db, *, reuse=False):
    symbols = ['2454.TW', *SYMBOLS] if reuse else list(SYMBOLS)
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=symbols,
        symbol_count=len(symbols), universe=[], step_statuses={}, errors=[]))
    if reuse:
        upsert_phase1_avwap_snapshot(db, symbol='2454.TW', data_date=DAY, payload={'existing': True})
    db.commit()


def test_shared_provider_preserves_flushed_caller_writes(db, monkeypatch):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    upsert_phase1_avwap_snapshot(db, symbol='2330.TW', data_date=DAY, payload={'caller_owned': True})
    assert not (db.new or db.dirty or db.deleted)
    fallback = Fallback(db)
    provider = ArchiveFirstDailyPriceProvider(db, provider_mode='remote_only', fallback_provider=fallback)
    provider.fetch_history_result('2317.TW', start_date=DAY-timedelta(days=120), end_date=DAY)
    assert set(rows(db)) == {'2330.TW'}
    db.commit()
    with Session(db.get_bind()) as reader:
        assert rows(reader)['2330.TW'].payload == {'caller_owned': True}


@pytest.mark.parametrize('durable', [False, True])
@pytest.mark.parametrize('missing_second,reuse', [(False, False), (True, False), (False, True)])
def test_refresh_endpoint_counts_match_committed_batch(db, monkeypatch, durable, missing_second, reuse):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', str(durable).lower())
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN', 'local-avwap-test')
    seed(db, reuse=reuse)
    fallback = Fallback(db, missing_second=missing_second)
    provider = ArchiveFirstDailyPriceProvider(db, provider_mode='archive_first', fallback_provider=fallback)
    app = FastAPI()
    app.include_router(refresh_router.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[dependencies.get_phase1_avwap_daily_price_provider] = lambda: provider
    with TestClient(app) as client:
        response = client.post('/internal/daily-radar/refresh-avwap', json={'market': 'TW', 'run_date': str(DAY)},
            headers={'Authorization': 'Bearer local-avwap-test'})
    assert response.status_code == 200
    result = response.json()
    assert result['run_date'] == str(DAY)
    assert result['status'] == ('failed' if missing_second else 'completed')
    assert result['records_written'] == len(result['fetched_symbols']) == 2
    assert result['reused_symbols'] == (['2454.TW'] if reuse else [])
    with Session(db.get_bind()) as reader:
        saved = rows(reader)
        assert set(saved) == set(SYMBOLS) | ({'2454.TW'} if reuse else set())
        assert saved['2330.TW'].freshness == 'fresh'
        assert saved['2317.TW'].freshness == ('missing' if missing_second else 'fresh')
        assert saved['2330.TW'].payload['source']['provider'] == 'fixture'
        assert set(result['missing_symbols']) == ({'2317.TW'} if missing_second else set())
        prepared = reader.scalar(select(DailyRadarPreparedRun))
        assert prepared.step_statuses['refresh-avwap']['status'] == result['status']


def test_shared_batch_does_not_rollback_preexisting_flushed_work(db, monkeypatch):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    upsert_phase1_avwap_snapshot(db, symbol='2454.TW', data_date=DAY, payload={'caller_owned': True})
    result = refresh_phase1_avwap_snapshots_for_symbols(db, symbols=SYMBOLS, data_date=DAY,
        provider=ArchiveFirstDailyPriceProvider(db, provider_mode='remote_only', fallback_provider=Fallback(db)))
    db.commit()
    assert result.fetched_symbols == SYMBOLS
    with Session(db.get_bind()) as reader:
        assert set(rows(reader)) == set(SYMBOLS) | {'2454.TW'}


def test_endpoint_collects_all_remote_inputs_before_first_snapshot_write(db, monkeypatch):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN', 'local-avwap-test')
    seed(db)
    fallback = Fallback(db, require_released=True)
    statements = []
    def before_write(connection, cursor, statement, *args):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            statements.append(statement)
            assert fallback.calls == SYMBOLS
    event.listen(db.get_bind(), 'before_cursor_execute', before_write)
    provider = ArchiveFirstDailyPriceProvider(db, provider_mode='archive_first', fallback_provider=fallback)
    app = FastAPI(); app.include_router(refresh_router.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[dependencies.get_phase1_avwap_daily_price_provider] = lambda: provider
    try:
        with TestClient(app) as client:
            response = client.post('/internal/daily-radar/refresh-avwap', json={'run_date': str(DAY)},
                headers={'Authorization': 'Bearer local-avwap-test'})
        assert response.status_code == 200 and response.json()['status'] == 'completed'
        assert statements and fallback.calls == SYMBOLS
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', before_write)


def test_unexpected_collection_failure_leaves_no_partial_batch_writes(db):
    fallback = Fallback(db)
    real = fallback.fetch_history_result
    def fetch(symbol, **kwargs):
        if symbol == SYMBOLS[1]:
            raise RuntimeError('simulated collection abort')
        return real(symbol, **kwargs)
    fallback.fetch_history_result = fetch
    with pytest.raises(RuntimeError, match='collection abort'):
        refresh_phase1_avwap_snapshots_for_symbols(db, symbols=SYMBOLS, data_date=DAY,
            provider=ArchiveFirstDailyPriceProvider(db, provider_mode='remote_only', fallback_provider=fallback))
    # Even an explicit caller commit after the collection error has no partial batch.
    db.commit()
    with Session(db.get_bind()) as reader:
        assert rows(reader) == {}


def test_snapshot_write_failure_rolls_back_the_batch_without_provider_commit(db, monkeypatch):
    from ai_stock_sentinel.phase1_avwap import service
    fallback = Fallback(db)
    write = service.upsert_phase1_avwap_snapshot
    def fail_second(session, **kwargs):
        assert fallback.calls == SYMBOLS
        if kwargs['symbol'] == SYMBOLS[1]:
            raise RuntimeError('simulated write abort')
        return write(session, **kwargs)
    monkeypatch.setattr(service, 'upsert_phase1_avwap_snapshot', fail_second)
    with pytest.raises(RuntimeError, match='write abort'):
        refresh_phase1_avwap_snapshots_for_symbols(db, symbols=SYMBOLS, data_date=DAY,
            provider=ArchiveFirstDailyPriceProvider(db, provider_mode='remote_only', fallback_provider=fallback))
    db.rollback()
    with Session(db.get_bind()) as reader:
        assert rows(reader) == {}


def test_one_shot_radar_helper_releases_before_collection_and_keeps_whole_batch(db, monkeypatch):
    from ai_stock_sentinel.daily_radar.pipeline_support import _refresh_phase1_avwap_for_daily_radar
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    seed(db)
    fallback = Fallback(db, require_released=True)
    _refresh_phase1_avwap_for_daily_radar(db, symbols=SYMBOLS, run_date=DAY,
        provider=ArchiveFirstDailyPriceProvider(db, provider_mode='archive_first', fallback_provider=fallback))
    assert fallback.calls == SYMBOLS
    db.commit()
    with Session(db.get_bind()) as reader:
        assert set(rows(reader)) == set(SYMBOLS)


def test_durable_avwap_handler_collects_archive_inputs_before_source_reservation(db, monkeypatch):
    from ai_stock_sentinel.phase1_avwap import provider as provider_module
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import WorkClaim
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    monkeypatch.setenv('DAILY_RADAR_AVWAP_PROVIDER_MODE', 'archive_first')
    seed(db)
    fallback = Fallback(db, require_released=True)
    monkeypatch.setattr(provider_module, 'TwseDailyPriceProvider', lambda **kwargs: fallback)
    claim = WorkClaim(1, 'test-token', 1, 'a'*64, 'avwap', 'auxiliary', DAY, {'symbol': SYMBOLS[0]})
    collected = WorkHandlers(db).collect(claim)
    assert fallback.calls == [SYMBOLS[0]]
    db.rollback()
    accepted = collected.apply(db)
    assert accepted['state'] == 'ready'
    db.commit()
    with Session(db.get_bind()) as reader:
        assert rows(reader)[SYMBOLS[0]].freshness == 'fresh'


def test_archive_and_fallback_inputs_collect_without_reopening_the_read_transaction(db):
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from ai_stock_sentinel.phase1_avwap.service import (
        prepare_phase1_avwap_refresh_for_symbols, collect_phase1_avwap_refresh, persist_phase1_avwap_refresh,
    )
    days = [DAY-timedelta(days=i) for i in reversed(range(120)) if (DAY-timedelta(days=i)).weekday() < 5][-60:]
    for day in days:
        db.add(TaiwanDailyBar(symbol=SYMBOLS[0], market='TW', trade_date=day, open=100, high=110, low=90,
            close=100, volume=1000, amount=100000, dataset='taiwan_market_daily_ohlcv',
            source_provider='twse', source_dataset='fixture_official', is_final=True))
    db.commit()
    fallback = Fallback(db, require_released=True)
    provider = ArchiveFirstDailyPriceProvider(db, provider_mode='archive_first', fallback_provider=fallback)
    preparation = prepare_phase1_avwap_refresh_for_symbols(db, symbols=SYMBOLS, data_date=DAY, provider=provider)
    db.rollback()
    def no_sql(*args):
        raise AssertionError('DB query in HTTP collection phase')
    event.listen(db.get_bind(), 'before_cursor_execute', no_sql)
    try:
        collection = collect_phase1_avwap_refresh(preparation)
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', no_sql)
    result = persist_phase1_avwap_refresh(db, collection)
    db.commit()
    assert result.fetched_symbols == SYMBOLS and result.missing_symbols == []
    assert fallback.calls == [SYMBOLS[1]]
    with Session(db.get_bind()) as reader:
        saved = rows(reader)
        assert saved[SYMBOLS[0]].payload['source']['trace']['route'] == 'archive_first'
        assert saved[SYMBOLS[1]].source_provider == 'fixture'
