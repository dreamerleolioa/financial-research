from datetime import datetime, timedelta, timezone, date
from itertools import permutations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.db.models import RadarSourceSnapshot, RadarInputManifest, RadarRunInput, DailyRadarRun
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore, fingerprint, canonical_json
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider


@compiles(JSONB, 'sqlite')
def jsonb_sqlite(type_, compiler, **kwargs):
    return 'JSON'


@pytest.fixture
def archive(tmp_path):
    engine = create_engine('sqlite+pysqlite:///' + str(tmp_path / 'archive.sqlite'))
    Base.metadata.create_all(engine, tables=[m.__table__ for m in
        (RadarSourceSnapshot, RadarInputManifest, RadarRunInput, DailyRadarRun)])
    yield engine
    engine.dispose()


def test_fingerprint_is_invariant_under_object_key_order_and_preserves_values():
    entries = [('日期', '2026-11-06'), ('prices', [1, 2.5]), ('empty', None), ('nested', {'b': False, 'a': '值'})]
    hashes = {fingerprint(dict(order)) for order in permutations(entries)}
    assert len(hashes) == 1
    assert fingerprint(dict(entries)) != fingerprint(dict(entries) | {'prices': [2.5, 1]})
    assert '日期' in canonical_json(dict(entries))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_non_finite_evidence_is_rejected(value):
    with pytest.raises(ValueError):
        fingerprint({'price': value})


def test_capture_reuses_cross_request_evidence_but_does_not_claim_coverage(archive):
    active = set()
    event.listen(archive, 'checkout', lambda conn, record, proxy: active.add(id(conn)))
    event.listen(archive, 'checkin', lambda conn, record: active.discard(id(conn)))
    def fetch():
        assert not active
        return {'stat': 'OK', 'data': []}
    kwargs = dict(provider='twse', url='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST',
        params={'date': '20261101', 'response': 'json'}, fetch=fetch)
    first, source = SourceSnapshotStore(archive).capture(**kwargs)
    again, proof = SourceSnapshotStore(archive).capture(**(kwargs | {'fetch': lambda: pytest.fail('cached report fetched twice')}))
    assert first == again
    assert source == proof
    assert proof['capture_state'] == 'captured'
    first['data'].append('caller mutation')
    assert SourceSnapshotStore(archive).capture(**kwargs)[0]['data'] == []


def test_expiry_creates_new_revision_and_preserves_old_payload(archive):
    clock = [datetime(2026, 11, 6, tzinfo=timezone.utc)]
    store = SourceSnapshotStore(archive, clock=lambda: clock[0], ttl=timedelta(seconds=10))
    kwargs = dict(provider='twse', url='https://www.twse.com.tw/report', params={}, fetch=lambda: {'price': 100})
    _, first = store.capture(**kwargs)
    clock[0] += timedelta(seconds=11)
    _, second = store.capture(**(kwargs | {'fetch': lambda: {'price': 101}}))
    assert second['snapshot_id'] != first['snapshot_id']
    assert second['snapshot_sha256'] != first['snapshot_sha256']
    with Session(archive) as db:
        assert db.get(RadarSourceSnapshot, first['snapshot_id']).payload == {'price': 100}


def test_two_provider_instances_share_report_but_revalidate_bad_month(archive):
    store = SourceSnapshotStore(archive)
    calls = []
    def getter(*args, **kwargs):
        calls.append(kwargs)
        return {'stat': 'OK', 'date': '20261001', 'data': []}
    for _ in range(2):
        provider = RecoveringResearchPriceProvider(source_store=store, official_getter=getter)
        with pytest.raises(ValueError, match='date_or_status_mismatch'):
            provider._official_month('TAIEX', date(2026, 11, 1))
    assert len(calls) == 1


def test_failed_transport_is_not_cached_as_empty_evidence(archive):
    store = SourceSnapshotStore(archive)
    kwargs = dict(provider='twse', url='https://www.twse.com.tw/report', params={})
    def fail():
        raise TimeoutError('unavailable')
    with pytest.raises(TimeoutError):
        store.capture(**kwargs, fetch=fail)
    with Session(archive) as db:
        assert list(db.scalars(select(RadarSourceSnapshot))) == []
    assert store.capture(**kwargs, fetch=lambda: {'data': []})[0] == {'data': []}


def test_adjusted_technical_history_is_archived_with_full_ohlc(archive, monkeypatch):
    import pandas as pd
    from ai_stock_sentinel.daily_radar.raw_data import YFinanceBatchTechnicalFetcher
    from test_daily_radar_technical_repair import _frame
    frame = _frame(end='2026-09-15')
    monkeypatch.setattr('ai_stock_sentinel.daily_radar.raw_data.yf.download',
        lambda *args, **kwargs: pd.concat({'2330.TW': frame}, axis=1))
    result = YFinanceBatchTechnicalFetcher(name_resolver=lambda _: None,
        source_store=SourceSnapshotStore(archive)).fetch(['2330.TW'], run_date=date(2026, 9, 15))
    proof = result['2330.TW']['source_provenance']['accepted_adjusted_history']
    with Session(archive) as db:
        payload = db.get(RadarSourceSnapshot, proof['snapshot_id']).payload
        assert payload['price_basis'] == 'adjusted_price'
        assert len(payload['records']) == 80
        assert set(payload['records'][0]) >= {'date', 'Open', 'High', 'Low', 'Close'}


def test_research_yahoo_batch_is_reused_across_provider_instances(archive, monkeypatch):
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    calls = []
    def fetch(self, symbols, **kwargs):
        calls.append(symbols)
        return {symbol: [{'date': '2026-11-06', 'close': 100, 'price_basis': 'unadjusted_price'}] for symbol in symbols}
    monkeypatch.setattr(YFinanceResearchPriceProvider, 'fetch', fetch)
    kwargs = dict(start_date=date(2026, 11, 6), end_date=date(2026, 11, 6))
    one = RecoveringResearchPriceProvider(source_store=SourceSnapshotStore(archive)).fetch(['2330.TW', '2317.TW'], **kwargs)
    two = RecoveringResearchPriceProvider(source_store=SourceSnapshotStore(archive)).fetch(['2317.TW', '2330.TW'], **kwargs)
    assert calls == [['2330.TW', '2317.TW']]
    assert one == two


def test_research_failed_miss_preserves_other_symbols_cache_hits(archive, monkeypatch):
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    store = SourceSnapshotStore(archive)
    store.capture_sdk(operation='research-unadjusted-history', params={'symbol': '2330.TW',
        'start_date': '2026-11-06', 'end_date': '2026-11-06', 'auto_adjust': False, 'actions': True},
        fetch=lambda: {'rows': [{'date': '2026-11-06', 'close': 100, 'price_basis': 'unadjusted_price'}]})
    def fail(self, symbols, **kwargs):
        assert symbols == ['2317.TW']
        raise RuntimeError('forward_price_provider_returned_no_data')
    monkeypatch.setattr(YFinanceResearchPriceProvider, 'fetch', fail)
    provider = RecoveringResearchPriceProvider(source_store=store)
    result = provider.fetch(['2330.TW', '2317.TW'], start_date=date(2026, 11, 6), end_date=date(2026, 11, 6))
    assert set(result) == {'2330.TW'}
    assert provider.diagnostics[-1]['symbols'] == ['2317.TW']


def test_one_shot_run_archives_with_a_single_connection(archive, monkeypatch):
    import pandas as pd
    from decimal import Decimal
    from sqlalchemy.pool import QueuePool
    from ai_stock_sentinel.daily_radar import run_router
    from ai_stock_sentinel.daily_radar.raw_data import YFinanceBatchTechnicalFetcher
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarRunRequest
    from ai_stock_sentinel.daily_radar.market_bar_provider import MarketDailyBar
    from ai_stock_sentinel.daily_radar.market_bar_repository import upsert_taiwan_daily_bars
    from test_daily_radar_api import (FakeUniverseProvider, FakeMarketIndexContextProvider,
        FakeBackgroundChipContextProvider, FakePhase1AvwapDailyPriceProvider, FakeMarketSessionProvider)
    from test_daily_radar_technical_repair import _frame
    # Reuse the file, but enforce the supported one-slot/no-overflow pool.
    engine = create_engine(archive.url, poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=.1)
    Base.metadata.create_all(engine)
    day = date(2026, 9, 15)
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', 'true')
    monkeypatch.setattr(run_router, 'load_market_exploration', lambda *args, **kwargs: ([], None))
    monkeypatch.setattr('ai_stock_sentinel.daily_radar.raw_data.yf.download',
        lambda *args, **kwargs: pd.concat({'2330.TW': _frame(end=str(day))}, axis=1))
    with Session(engine) as db:
        quote = _frame(end=str(day)).iloc[-1]
        upsert_taiwan_daily_bars(db, [MarketDailyBar(symbol='2330.TW', market='TW', name='fixture',
            trade_date=day, **{key: Decimal(str(quote[key.title()])) for key in ('open','high','low','close')},
            volume=1, amount=1, source_provider='twse', source_dataset='fixture')])
        db.commit()
        result = run_router.run_daily_radar_endpoint(payload=DailyRadarRunRequest(run_date=day, market='TW'), db=db,
            universe_provider=FakeUniverseProvider(),
            technical_fetcher=YFinanceBatchTechnicalFetcher(name_resolver=lambda _: None, source_store=SourceSnapshotStore(engine)),
            market_context_provider=FakeMarketIndexContextProvider(), background_context_provider=FakeBackgroundChipContextProvider(),
            phase1_avwap_provider=FakePhase1AvwapDailyPriceProvider(), market_session_provider=FakeMarketSessionProvider())
        # This price fixture may fail qualification; it must still reach
        # DB-only scoring with evidence and all prior context writes intact.
        assert not any(error['code'] == 'run_error' for error in result.errors)
        assert db.scalar(select(RadarSourceSnapshot.id).limit(1)) is not None
        from ai_stock_sentinel.db.models import SharedBackgroundContext, Phase1AvwapSnapshot
        assert db.scalar(select(SharedBackgroundContext.id).limit(1)) is not None
        assert db.scalar(select(Phase1AvwapSnapshot.id).limit(1)) is not None
    engine.dispose()


def test_covering_snapshot_is_reused_without_returning_future_prices(archive, monkeypatch):
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    store = SourceSnapshotStore(archive)
    store.capture_sdk(operation='research-unadjusted-history', params={'symbol': '2330.TW',
        'start_date': '2026-11-01', 'end_date': '2026-11-06', 'auto_adjust': False, 'actions': True},
        fetch=lambda: {'rows': [{'date': day, 'close': 100, 'price_basis': 'unadjusted_price'}
            for day in ['2026-11-02', '2026-11-05', '2026-11-06']]})
    monkeypatch.setattr(YFinanceResearchPriceProvider, 'fetch', lambda *args, **kwargs: pytest.fail('covering data re-fetched'))
    result = RecoveringResearchPriceProvider(source_store=store).fetch(['2330.TW'],
        start_date=date(2026, 11, 5), end_date=date(2026, 11, 5))
    assert [row['date'] for row in result['2330.TW']] == ['2026-11-05']


def test_twse_margin_worker_rechecks_cached_pre_roster_empty_response(archive, monkeypatch):
    from ai_stock_sentinel.daily_radar.source_snapshots import PARSER_VERSION

    policy = {key: '' for key in ('DAILY_RADAR_TW_OHLCV_PROVIDER_MODE', 'DAILY_RADAR_BACKGROUND_PROVIDER_MODE',
                                  'DAILY_RADAR_LENDING_PROVIDER_MODE', 'DAILY_RADAR_AVWAP_PROVIDER_MODE')}
    for key in policy:
        monkeypatch.delenv(key, raising=False)
    store = SourceSnapshotStore(archive)
    params = {'symbol': '2258.TW', 'context_type': 'full_margin', 'run_date': '2026-11-17'}
    operation = 'background-chip-context'
    legacy_request = {'provider': 'radar-provider-chain', 'operation': operation, 'params': params,
                      'policy': policy, 'parser_version': PARSER_VERSION, 'sdk_version': None}
    store.record(legacy_request, {'contexts': [{'missing_reason': 'finmind_no_data'}]})
    fresh = {'contexts': [{'eligibility': 'newly checked'}]}
    value, proof = store.capture_operation(operation=operation, params=params, fetch=lambda: fresh)
    assert value == fresh
    assert store.capture_operation(operation=operation, params=params, fetch=lambda: pytest.fail('unexpected fetch')) == (fresh, proof)


def test_late_intraday_capture_does_not_replace_after_date_confirmation(archive):
    params = {'symbol': '2258.TW', 'context_type': 'full_margin', 'run_date': '2026-11-17'}
    # 00:09 Taipei is still within a raw snapshot lifetime of the trading date.
    now = datetime(2026, 11, 17, 16, 9, tzinfo=timezone.utc)
    store = SourceSnapshotStore(archive, clock=lambda: now)
    def capture(value):
        return store.capture_operation(operation='background-chip-context', params=params, fetch=lambda: value)[0]
    assert capture({'contexts': ['preliminary']}) == {'contexts': ['preliminary']}
    now += timedelta(minutes=1)
    assert capture({'contexts': ['rechecked']}) == {'contexts': ['rechecked']}


def test_forward_quotes_with_unknown_open_origin_are_not_reused(archive):
    store = SourceSnapshotStore(archive)
    params = {"symbol":"6108.TW", "start_date":"2026-11-01", "end_date":"2026-11-18",
              "auto_adjust":True, "actions":False}
    req = store._sdk_request("forward-adjusted-history", params)
    req.pop("open_provenance_version")
    store._capture_request(req, fetch=lambda:{"rows":[{"date":"2026-11-16","open":12,"close":12}]})
    for start in ("2026-11-01", "2026-11-16"):
        assert store.lookup_sdk(operation="forward-adjusted-history",params=params | {"start_date":start}) is None


def test_scored_signal_retains_exact_quote_and_accepted_history(archive, monkeypatch):
    from copy import deepcopy
    import pandas as pd
    from ai_stock_sentinel.daily_radar.raw_data import YFinanceBatchTechnicalFetcher
    from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_cache_records
    from ai_stock_sentinel.daily_radar.scoring import score_daily_radar_record
    from test_daily_radar_technical_repair import _frame

    day = date(2026, 9, 15)
    frame = _frame(end=str(day))
    monkeypatch.setattr('ai_stock_sentinel.daily_radar.raw_data.yf.download',
                        lambda *args, **kwargs: pd.concat({'2330.TW': frame}, axis=1))
    technical = YFinanceBatchTechnicalFetcher(name_resolver=lambda _: None, source_store=SourceSnapshotStore(archive)).fetch(
        ['2330.TW'], run_date=day)['2330.TW']
    [record] = load_daily_radar_cache_records([dict(symbol='2330.TW', record_date=day, technical=technical, institutional={}, fundamental={})])
    snapshot = score_daily_radar_record(record)['input_snapshot']
    quote = snapshot['signal_quote']
    assert quote['symbol'] == '2330.TW' and quote['trade_date'] == str(day)
    assert quote['provider'] == 'yfinance' and quote['price_basis'] == 'adjusted_price'
    source = technical['source_provenance']['accepted_adjusted_history']
    assert quote['captured_at'] == source['captured_at']
    assert quote['history_receipt'] == source
    assert quote['ohlcv'] == {k: technical['ohlcv'][k] for k in ('open','high','low','close','volume')}
    assert snapshot['replay_input']['record']['signal_quote'] == quote
    old_price = quote['ohlcv']['close']
    technical['ohlcv']['close'] = old_price + 1
    assert quote['ohlcv']['close'] == old_price
    invalid = deepcopy(record)
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.raw_data import current_daily_radar_raw_rows
    raw = SimpleNamespace(symbol='2330.TW', record_date=day, technical=technical)
    assert not current_daily_radar_raw_rows([raw], run_date=day)
    raw.technical = {**technical, 'ohlcv': [1]}
    assert not current_daily_radar_raw_rows([raw], run_date=day)
    invalid['symbol'] = '1808.TW'
    with pytest.raises(ValueError, match='signal_quote'):
        score_daily_radar_record(invalid)


def test_repaired_signal_retains_official_price_provider_and_adjustment(monkeypatch):
    import pandas as pd
    from ai_stock_sentinel.daily_radar.raw_data import YFinanceBatchTechnicalFetcher
    from test_daily_radar_technical_repair import _repair_fixture, RUN_DATE
    original, raw, official = _repair_fixture(factor=0.95)
    monkeypatch.setattr('ai_stock_sentinel.daily_radar.raw_data.yf.download',
                        lambda symbols, **kw: pd.concat({'2330.TW': raw if kw.get('auto_adjust') is False else original}, axis=1))
    [technical] = YFinanceBatchTechnicalFetcher(
        name_resolver=lambda _: None, official_bar_loader=lambda *a, **kw: {'2330.TW': official},
    ).fetch(['2330.TW'], run_date=RUN_DATE).values()
    quote = technical['signal_quote']
    assert quote['provider'] == 'twse'
    assert quote['adjustment']['adjustment_factor'] == pytest.approx(0.95)
    assert datetime.fromisoformat(quote['captured_at']).tzinfo is not None
    assert quote['ohlcv']['close'] == technical['ohlcv']['close']
