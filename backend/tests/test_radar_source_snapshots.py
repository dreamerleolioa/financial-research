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
    from sqlalchemy.pool import QueuePool
    from ai_stock_sentinel.daily_radar import run_router
    from ai_stock_sentinel.daily_radar.raw_data import YFinanceBatchTechnicalFetcher
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarRunRequest
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
