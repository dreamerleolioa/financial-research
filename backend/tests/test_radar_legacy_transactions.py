"""Legacy Radar owners finish provider I/O before any domain writes."""
from datetime import date
from decimal import Decimal
from threading import Event

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, func

from ai_stock_sentinel.daily_radar import run_router
from ai_stock_sentinel.daily_radar.background_context import update_background_chip_context_cache
from ai_stock_sentinel.daily_radar.market_bar_provider import MarketDailyBar
from ai_stock_sentinel.daily_radar.market_bar_service import refresh_taiwan_market_bars
from ai_stock_sentinel.daily_radar.pipeline_support import _refresh_daily_radar_context_step
from ai_stock_sentinel.daily_radar.repository import upsert_shared_background_context
from ai_stock_sentinel.daily_radar.schemas import DailyRadarRunRequest, DailyRadarRefreshStepRequest
from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarPreparedRun, Phase1AvwapSnapshot, SharedBackgroundContext, StockRawData
from test_radar_jobs import db
from test_daily_radar_api import (FakeUniverseProvider, FakeBatchTechnicalFetcher,
    FakePhase1AvwapDailyPriceProvider, FakeMarketIndexContextProvider,
    BaselineZeroBackgroundChipContextProvider, FakeMarketSessionProvider)

DAY = date(2026, 5, 29)


def released(db):
    assert not db.in_transaction(), 'caller transaction spans provider I/O'
    assert db.get_bind().pool.checkedout() == 0


@pytest.mark.parametrize('snapshots', [False, True])
@pytest.mark.parametrize('durable', [False, True])
@pytest.mark.parametrize('failure', ['none', 'market', 'write'])
def test_one_shot_collects_every_source_before_domain_write(db, monkeypatch, snapshots, durable, failure):
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', str(snapshots).lower())
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', str(durable).lower())
    monkeypatch.setattr(run_router, 'load_market_exploration', lambda *args, **kwargs: ([], None))
    seen = set()
    def checked(obj, method, label):
        original = getattr(obj, method)
        def call(*args, **kwargs):
            released(db)
            seen.add(label)
            if label == 'market' and failure == 'market':
                raise RuntimeError('fixture market outage')
            return original(*args, **kwargs)
        setattr(obj, method, call)
        return obj
    def before_write(connection, cursor, statement, *args):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            assert seen == {'raw', 'avwap', 'chip', 'market'}
    event.listen(db.get_bind(), 'before_cursor_execute', before_write)
    if failure == 'write':
        from ai_stock_sentinel.daily_radar import background_context
        upsert = background_context.upsert_shared_background_context
        writes = []
        def fail_second(*args, **kwargs):
            writes.append(kwargs['context_type'])
            if len(writes) == 2:
                raise RuntimeError('fixture context write failure')
            return upsert(*args, **kwargs)
        monkeypatch.setattr(background_context, 'upsert_shared_background_context', fail_second)
    kwargs = dict(payload=DailyRadarRunRequest(run_date=DAY, market='TW'), db=db,
        universe_provider=FakeUniverseProvider(), market_session_provider=FakeMarketSessionProvider(),
        technical_fetcher=checked(FakeBatchTechnicalFetcher(), 'fetch', 'raw'),
        phase1_avwap_provider=checked(FakePhase1AvwapDailyPriceProvider(), 'fetch_history', 'avwap'),
        background_context_provider=checked(BaselineZeroBackgroundChipContextProvider(), 'fetch', 'chip'),
        market_context_provider=checked(FakeMarketIndexContextProvider(), 'build', 'market'))
    try:
        if failure != 'none':
            with pytest.raises(HTTPException) as error:
                run_router.run_daily_radar_endpoint(**kwargs)
            assert error.value.status_code == 503
            db.commit()
        else:
            result = run_router.run_daily_radar_endpoint(**kwargs)
            assert not any(error['code'] == 'run_error' for error in result.errors)
        assert seen == {'raw', 'avwap', 'chip', 'market'}
        for model in (DailyRadarRun, Phase1AvwapSnapshot, SharedBackgroundContext, StockRawData):
            count = db.scalar(select(func.count()).select_from(model))
            assert count == 0 if failure != 'none' else count > 0
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', before_write)


@pytest.mark.parametrize('context', ['lending', 'full_margin'])
def test_legacy_context_owner_releases_prepared_read_before_provider(db, context):
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=['2330.TW'],
        symbol_count=1, universe=[], errors=[], step_statuses={}))
    db.commit()
    class CheckedProvider(BaselineZeroBackgroundChipContextProvider):
        def fetch(self, **kwargs):
            released(db)
            return super().fetch(**kwargs)
    result = _refresh_daily_radar_context_step(db,
        payload=DailyRadarRefreshStepRequest(run_date=DAY), provider=CheckedProvider(),
        context_type=context, step='refresh-' + context.replace('_', '-'))
    assert result.status == 'completed' and result.records_written == 1


@pytest.mark.parametrize('fail_second', [False, True])
def test_background_batches_collect_before_writing_and_preserve_reuse(db, fail_second):
    provider = BaselineZeroBackgroundChipContextProvider()
    payload = provider.fetch(symbols=['2330.TW'], context_types=['lending'], run_date=DAY, market='TW')[0]
    upsert_shared_background_context(db, **vars(payload))
    db.commit()
    provider.calls.clear()
    original = provider.fetch
    def fetch(**kwargs):
        if fail_second and provider.calls:
            provider.calls.append(kwargs)
            raise TimeoutError('fixture second batch outage')
        return original(**kwargs)
    provider.fetch = fetch
    def before_write(connection, cursor, statement, *args):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            assert len(provider.calls) == 2, 'a later provider batch is still outstanding'
    event.listen(db.get_bind(), 'before_cursor_execute', before_write)
    try:
        result = update_background_chip_context_cache(db, run_date=DAY, market='TW',
            symbols=['2330.TW', '2317.TW'], context_types=['lending', 'full_margin'],
            provider=provider, reuse_same_day_fresh=True, require_same_day_fresh=True)
        db.commit()
        assert result['status'] == ('failed' if fail_second else 'completed')
        assert result['records_written'] == (1 if fail_second else 3)
        assert result['reused_symbols'] == ['2330.TW']
        assert provider.calls[0]['symbols'] == ['2317.TW']
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', before_write)


def test_market_bar_batch_collects_all_futures_before_first_write(db):
    first_write, second_done = Event(), Event()
    class Provider:
        def fetch_market(self, *, market, trade_date):
            if market == 'TWO':
                first_write.wait(.2)
                second_done.set()
            return [MarketDailyBar(symbol='2330.TW' if market == 'TW' else '6488.TWO',
                market=market, name='fixture', trade_date=trade_date,
                open=Decimal('100'), high=Decimal('102'), low=Decimal('99'), close=Decimal('101'),
                volume=1000, amount=101000, source_provider='twse' if market == 'TW' else 'tpex', source_dataset='fixture')]
    def before_write(connection, cursor, statement, *args):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            first_write.set()
            assert second_done.is_set(), 'write transaction overlaps outstanding provider work'
    event.listen(db.get_bind(), 'before_cursor_execute', before_write)
    try:
        result = refresh_taiwan_market_bars(db, start_date=DAY, end_date=DAY, provider=Provider(), max_workers=2)
        db.commit()
        assert result['status'] == 'completed' and result['records_written'] == 2
    finally:
        first_write.set()
        event.remove(db.get_bind(), 'before_cursor_execute', before_write)


@pytest.mark.parametrize('dry_run', [False, True])
def test_name_backfill_owner_resolves_names_without_holding_read_transaction(db, dry_run):
    from ai_stock_sentinel.daily_radar.maintenance_router import backfill_daily_radar_names_endpoint
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarNameBackfillRequest
    from test_daily_radar_name_backfill import _daily_radar_run, _daily_radar_candidate, _raw_data
    run = _daily_radar_run(db)
    candidate = _daily_radar_candidate(db, run, symbol='2330.TW', name='2330.TW')
    raw = _raw_data(db, symbol='2330.TW', name='2330.TW')
    db.commit()
    calls = []
    def resolver(symbol):
        released(db)
        calls.append(symbol)
        return '台積電'
    result = backfill_daily_radar_names_endpoint(payload=DailyRadarNameBackfillRequest(dry_run=dry_run),
        db=db, name_resolver=resolver)
    assert calls == ['2330.TW']
    assert result.updated_candidates == result.updated_raw_rows == 1
    assert candidate.name == ('2330.TW' if dry_run else '台積電')
    assert raw.technical['name'] == ('2330.TW' if dry_run else '台積電')


def test_market_bar_unexpected_collection_error_cannot_leave_a_partial_batch(db):
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    db.add(Phase1AvwapSnapshot(symbol='2454.TW', data_date=DAY, payload={'caller_owned': True}))
    db.flush()
    first_fetched = Event()
    class Provider:
        def fetch_market(self, *, market, trade_date):
            if market == 'TWO':
                assert first_fetched.wait(2)
                raise RuntimeError('fixture collection failure')
            first_fetched.set()
            return [MarketDailyBar(symbol='2330.TW', market='TW', name='fixture', trade_date=trade_date,
                open=Decimal('100'), high=Decimal('102'), low=Decimal('99'), close=Decimal('101'),
                volume=1000, amount=101000, source_provider='twse', source_dataset='fixture')]
    with pytest.raises(RuntimeError, match='collection failure'):
        refresh_taiwan_market_bars(db, start_date=DAY, end_date=DAY, provider=Provider(), max_workers=2)
    db.commit()  # A shared collector must preserve caller work, not save partial batches.
    assert db.scalar(select(func.count()).select_from(TaiwanDailyBar)) == 0
    assert db.scalar(select(Phase1AvwapSnapshot)).payload == {'caller_owned': True}


def test_durable_context_acceptance_requires_collected_inputs(db):
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=['2330.TW'],
        symbol_count=1, universe=[], errors=[], step_statuses={}))
    db.commit()
    class UnexpectedNetwork:
        def fetch(self, **kwargs):
            pytest.fail('acceptance must never invoke a live provider')
    with pytest.raises(TypeError, match='requires_collected_payloads'):
        _refresh_daily_radar_context_step(db, payload=DailyRadarRefreshStepRequest(run_date=DAY),
            provider=UnexpectedNetwork(), context_type='lending', step='refresh-lending', commit=False)
    db.rollback()


@pytest.mark.parametrize('context', ['lending', 'full_margin'])
def test_durable_context_acceptance_reuses_intervening_legacy_refresh(db, monkeypatch, context):
    from dataclasses import replace
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.jobs import prepared_generation
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import WorkClaim
    row = DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=['2330.TW'],
        symbol_count=1, universe=[], errors=[], step_statuses={'prepare-universe': {'updated_at': 'first'}})
    db.add(row)
    db.commit()
    generation = prepared_generation(row)
    db.rollback()
    class Provider(BaselineZeroBackgroundChipContextProvider):
        def __init__(self, revision):
            super().__init__()
            self.revision = revision
        def fetch(self, **kwargs):
            return [replace(p, payload=dict(p.payload) | {'revision': self.revision}) for p in super().fetch(**kwargs)]
    monkeypatch.setattr('ai_stock_sentinel.daily_radar.default_background_context.DefaultBackgroundChipContextProvider',
        lambda **kwargs: Provider('captured'))
    handler = WorkHandlers(db)
    handler.store = SimpleNamespace(capture_operation=lambda **kwargs: (kwargs['fetch'](), {}))
    stage = context.replace('_', '-')
    claim = WorkClaim(1, 'test-token', 1, 'a' * 64, stage, 'publication', DAY,
        {'symbol': '2330.TW', 'generation': generation})
    collected = handler._collect_context(claim, context)
    legacy = _refresh_daily_radar_context_step(db, payload=DailyRadarRefreshStepRequest(run_date=DAY),
        provider=Provider('legacy'), context_type=context, step='refresh-' + stage)
    assert legacy.status == 'completed' and prepared_generation(row) == generation
    accepted = collected.apply(db)
    db.commit()
    assert accepted['state'] == 'ready'
    assert row.step_statuses['refresh-' + stage]['status'] == 'completed'
    assert row.step_statuses['refresh-' + stage]['records_written'] == 0
    assert db.scalar(select(SharedBackgroundContext)).payload['revision'] == 'legacy'


@pytest.mark.parametrize('dry_run', [False, True])
def test_name_backfill_cli_releases_read_transaction_before_resolving(db, monkeypatch, capsys, dry_run):
    import importlib.util
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import DailyRadarCandidate
    from test_daily_radar_name_backfill import _daily_radar_run, _daily_radar_candidate, _raw_data
    script = Path(__file__).parents[1] / 'scripts/backfill_daily_radar_symbol_names.py'
    spec = importlib.util.spec_from_file_location('radar_name_backfill_cli', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = _daily_radar_run(db)
    _daily_radar_candidate(db, run, symbol='2330.TW', name='2330.TW')
    _raw_data(db, symbol='2330.TW', name='2330.TW')
    db.commit()
    engine, calls = db.get_bind(), []
    def resolver(symbol):
        released(db)
        calls.append(symbol)
        return '台積電'
    original = module.backfill_daily_radar_symbol_names
    def backfill(*args, **kwargs):
        kwargs.setdefault('name_resolver', resolver)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'backfill_daily_radar_symbol_names', backfill)
    monkeypatch.setattr(module, 'get_daily_radar_symbol_name_resolver', lambda: resolver, raising=False)
    monkeypatch.setattr(module, '_get_session_local', lambda: lambda: db)
    monkeypatch.setattr(module, '_parse_args', lambda: SimpleNamespace(limit=None, dry_run=dry_run))
    module.main()
    result = json.loads(capsys.readouterr().out)
    assert calls == ['2330.TW']
    assert result['scanned'] == result['updated_candidates'] == result['updated_raw_rows'] == 1
    assert result['unresolved_symbols'] == []
    with Session(engine) as reader:
        assert reader.scalar(select(DailyRadarCandidate.name)) == ('2330.TW' if dry_run else '台積電')
