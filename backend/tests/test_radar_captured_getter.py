from datetime import date
import pytest
from sqlalchemy import select
from ai_stock_sentinel.daily_radar.durable_providers import CapturedOfficialGetter
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
from ai_stock_sentinel.db.models import RadarWorkItem
from test_radar_jobs import db


def test_official_list_payload_is_cached_without_changing_its_shape(db,monkeypatch):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    calls=[]
    def fetch(*args,**kwargs):calls.append(kwargs);return [{'Code':'2330','Date':'1151106'}]
    getter=CapturedOfficialGetter(SourceSnapshotStore(db.get_bind()),getter=fetch)
    first=getter('https://www.tpex.org.tw/openapi/v1/example',params={},timeout=30)
    assert first==getter('https://www.tpex.org.tw/openapi/v1/example',params={},timeout=30)
    assert isinstance(first,list) and len(calls)==1


def test_total_budget_prevents_more_network_and_releases_host_gate(db,monkeypatch):
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    clock=[0.0];calls=[]
    def fetch(*args,**kwargs):
        calls.append(kwargs['timeout']);clock[0]=10;return {'stat':'OK'}
    getter=CapturedOfficialGetter(SourceSnapshotStore(db.get_bind()),getter=fetch,clock=lambda:clock[0],max_seconds=5)
    with pytest.raises(TimeoutError,match='budget_exhausted'):
        getter('https://www.twse.com.tw/report',params={'date':'20261106'},timeout=30)
    with pytest.raises(TimeoutError,match='budget_exhausted'):
        getter('https://www.twse.com.tw/other',params={'date':'20261106'},timeout=30)
    assert calls==[5]
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='provider-gate')).state=='ready'


def test_default_official_getter_does_not_hide_extra_transport_retries(db,monkeypatch):
    from types import SimpleNamespace
    from ai_stock_sentinel.data_sources import official_http
    from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
    calls=[]
    def get(*args,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=500,headers={},json=lambda:{'error':'temporary'})
    def sleep(*args):raise AssertionError('hidden transport retry')
    monkeypatch.setattr(official_http.curl_requests,'get',get)
    monkeypatch.setattr(official_http,'_sleep_before_retry',sleep)
    getter=CapturedOfficialGetter(SourceSnapshotStore(db.get_bind()))
    with pytest.raises(ResearchSourceError) as failure:
        getter('https://www.twse.com.tw/report',params={'date':'20261106'},timeout=45)
    assert failure.value.http_status==500 and len(calls)==1
