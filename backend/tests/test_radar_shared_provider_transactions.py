"""Shared collection interfaces must preserve transactions owned by their callers."""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
from ai_stock_sentinel.daily_radar.durable_providers import CachedForwardPrices
from ai_stock_sentinel.daily_radar.raw_data import fetch_daily_radar_raw_payloads
from ai_stock_sentinel.db.models import Phase1AvwapSnapshot
from test_radar_jobs import db

DAY = date(2026, 11, 6)
SYMBOL = '2330.TW'
PRICE = {'date': str(DAY), 'close': 100}


class SourceStore:
    def __init__(self, hit):
        self.hit = hit
        self.captures = 0

    def lookup_sdk(self, **kwargs):
        return ({'rows': [PRICE]}, {'snapshot_id': 1}) if self.hit else None

    def capture_sdk(self, *, fetch, **kwargs):
        self.captures += 1
        return fetch(), {'snapshot_id': 2}


def caller_write(db, flushed):
    row = Phase1AvwapSnapshot(symbol=SYMBOL, data_date=DAY, payload={'caller_owned': True})
    db.add(row)
    if flushed:
        db.flush()
        assert not (db.new or db.dirty or db.deleted)
    return row


def assert_caller_can_commit(db, row):
    assert db.in_transaction()
    with Session(db.get_bind()) as reader:
        assert reader.scalar(select(Phase1AvwapSnapshot)) is None, 'provider committed caller work'
    db.commit()
    with Session(db.get_bind()) as reader:
        saved = reader.scalar(select(Phase1AvwapSnapshot))
        assert saved is not None and saved.payload == {'caller_owned': True}


def test_raw_owner_can_release_read_connection_before_fetch(db):
    from ai_stock_sentinel.daily_radar.raw_data import LocalFirstBatchTechnicalFetcher, prepare_daily_radar_raw_payloads
    def fetch(symbols, **kwargs):
        assert not db.in_transaction()
        assert db.get_bind().pool.checkedout() == 0
        return {s: {'close': 100} for s in symbols}
    fetcher = LocalFirstBatchTechnicalFetcher(db, provider_mode='yfinance_only',
        fallback_fetcher=SimpleNamespace(fetch=fetch))
    collection = prepare_daily_radar_raw_payloads(db, DAY, [SYMBOL], technical_fetcher=fetcher)
    db.rollback()
    assert collection.fetch()[SYMBOL]['close'] == 100


@pytest.mark.parametrize('flushed', [False, True])
@pytest.mark.parametrize('symbols', [[], ['2317.TW']])
def test_raw_collector_preserves_caller_writes(db, flushed, symbols):
    row = caller_write(db, flushed)
    fetcher = SimpleNamespace(fetch=lambda symbols, **kwargs: {s: {'close': 100} for s in symbols})
    result = fetch_daily_radar_raw_payloads(db, DAY, symbols, technical_fetcher=fetcher)
    assert set(result) == set(symbols)
    assert_caller_can_commit(db, row)


@pytest.mark.parametrize('flushed', [False, True])
@pytest.mark.parametrize('hit', [False, True])
@pytest.mark.parametrize('kind', ['forward', 'research'])
@pytest.mark.parametrize('durable', [False, True])
def test_price_providers_preserve_caller_writes(db, monkeypatch, flushed, hit, kind, durable):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', str(durable).lower())
    row = caller_write(db, flushed)
    store = SourceStore(hit)
    fetch = lambda self, symbols, **kwargs: {s: [PRICE] for s in symbols}
    if kind == 'research':
        monkeypatch.setattr(YFinanceResearchPriceProvider, 'fetch', fetch)
        provider = RecoveringResearchPriceProvider(session=db, source_store=store)
    else:
        provider = CachedForwardPrices(db, store=store,
            provider=SimpleNamespace(fetch=lambda symbols, **kwargs: {s: [PRICE] for s in symbols}))
    result = provider.fetch([SYMBOL], start_date=DAY, end_date=DAY)
    assert result[SYMBOL][0]['close'] == 100
    assert store.captures == int(not hit)
    assert_caller_can_commit(db, row)


def test_research_official_request_preserves_flushed_caller_write(db):
    row = caller_write(db, True)
    provider = RecoveringResearchPriceProvider(session=db, source_store=SourceStore(True))
    assert provider._request(lambda *args, **kwargs: {'stat': 'OK'},
        'https://example.invalid/fixture', params={}) == {'stat': 'OK'}
    assert_caller_can_commit(db, row)


@pytest.mark.parametrize('snapshots', [False, True])
def test_research_repair_closes_owned_archive_reader_before_http(db, monkeypatch, snapshots):
    from test_research_price_recovery import DAY as research_day, official, actions
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', str(snapshots).lower())
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'false')
    def checked(getter):
        def fetch(*args, **kwargs):
            assert not db.in_transaction()
            assert db.get_bind().pool.checkedout() == 0
            return getter(*args, **kwargs)
        return fetch
    provider = RecoveringResearchPriceProvider(session=db, official_getter=checked(official),
        actions_getter=checked(actions))
    rows = provider.repair({}, required_dates_by_symbol={SYMBOL: [research_day]}, as_of_date=research_day)
    assert rows[SYMBOL][0]['close'] == 102
