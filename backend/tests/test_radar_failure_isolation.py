"""A timeout is request-scoped; an explicit rate limit is provider-scoped."""
from datetime import timedelta
from urllib.error import HTTPError

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from test_radar_job_postgres import pg_engine
from test_radar_work_postgres import setup_queue
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
from ai_stock_sentinel.daily_radar.work_queue import database_now
from ai_stock_sentinel.db.models import RadarWorkItem


@pytest.mark.parametrize('status', [None, 429, 503])
def test_one_failure_does_not_poison_unrelated_requests(pg_engine, monkeypatch, status):
    setup_queue(pg_engine)
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    url = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY'
    def capture(code, fetch):
        return SourceSnapshotStore(pg_engine).capture(provider='twse',url=url,
            params={'stockNo':code,'date':'20261101'}, fetch=fetch)
    capture('cached', lambda: {'price':100})
    def fail():
        if status:
            raise HTTPError(url, status, 'busy', {'Retry-After':'172800'}, None)
        raise TimeoutError('one request')
    with pytest.raises((TimeoutError, HTTPError)):
        capture('failed', fail)
    assert capture('cached', lambda:pytest.fail('cache ignored'))[0] == {'price':100}
    completed=[]
    for n in range(120):
        if status:
            with pytest.raises(SourceDependencyBusy):
                capture(str(n), lambda:pytest.fail('cooldown ignored'))
        else:
            completed.append(capture(str(n),lambda:{'price':101})[0])
    with Session(pg_engine) as db:
        gate = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='provider-gate'))
        if status:
            assert gate.state=='waiting_provider'
            assert gate.retry_at >= database_now(db)+timedelta(seconds=172780)
        else:
            assert len(completed)==120
            assert gate.state=='ready'
    with pytest.raises(SourceDependencyBusy):
        capture('failed', lambda:pytest.fail('request cooldown ignored'))
    with Session(pg_engine) as db, db.begin():
        db.execute(update(RadarWorkItem).where(RadarWorkItem.state=='waiting_provider').values(retry_at=database_now(db)-timedelta(seconds=1)))
    assert capture('failed',lambda:{'price':102})[0]=={'price':102}


@pytest.mark.parametrize('status', [429, 503])
def test_index_transport_does_not_retry_before_explicit_provider_delay(status):
    from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider, ResearchSourceError, INDEX_URL
    calls=[]
    class Response:
        status_code=status
        headers={'Retry-After':'900'}
        def raise_for_status(self): raise CurlHTTPError('rate limited')
    def getter(*args,**kwargs): calls.append(1);return Response()
    provider=RecoveringResearchPriceProvider(official_getter=getter)
    with pytest.raises(ResearchSourceError) as caught:
        provider._request(getter,INDEX_URL,params={'date':'20261101'})
    assert caught.value.http_status==status
    assert caught.value.retry_after=='900'
    assert calls==[1]


def test_yahoo_explicit_rate_limit_still_closes_shared_gate(pg_engine, monkeypatch):
    from yfinance.exceptions import YFRateLimitError
    setup_queue(pg_engine)
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    store=SourceSnapshotStore(pg_engine)
    def fail():raise YFRateLimitError()
    with pytest.raises(YFRateLimitError):
        store.capture_sdk(operation='forward-adjusted-history',params={'symbol':'2330.TW','start_date':'2026-11-09','end_date':'2026-11-16','auto_adjust':True,'actions':False},fetch=fail)
    with pytest.raises(SourceDependencyBusy):
        store.capture_sdk(operation='forward-adjusted-history',params={'symbol':'2317.TW','start_date':'2026-11-09','end_date':'2026-11-16','auto_adjust':True,'actions':False},fetch=lambda:pytest.fail('rate limit ignored'))


def test_oversized_retry_after_keeps_a_durable_gate_instead_of_overflowing(pg_engine,monkeypatch):
    setup_queue(pg_engine);monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    url='https://www.twse.com.tw/exchangeReport/STOCK_DAY'
    def fail():raise HTTPError(url,429,'limit',{'Retry-After':str(10**100)},None)
    with pytest.raises(HTTPError):
        SourceSnapshotStore(pg_engine).capture(provider='twse',url=url,params={},fetch=fail)
    with Session(pg_engine) as db:
        assert all(w.state=='waiting_provider' and w.retry_at.year==9999 for w in db.scalars(select(RadarWorkItem)))
