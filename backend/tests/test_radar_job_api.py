from fastapi import FastAPI
from fastapi.testclient import TestClient
from ai_stock_sentinel.daily_radar.jobs_router import router
from ai_stock_sentinel.db.session import get_db
from test_radar_jobs import db


def test_job_api_auth_capability_exact_date_and_allowlisted_scope(db,monkeypatch):
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN','radar-test-token')
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_db]=lambda:db
    headers={'Authorization':'Bearer radar-test-token'}
    # SQLite connection is opened by the client thread; no connection crosses threads.
    db.rollback()
    with TestClient(app) as client:
        assert client.get('/internal/daily-radar/capabilities').status_code==401
        assert client.post('/internal/daily-radar/jobs',headers=headers,json={'run_date':'2026-11-06'}).status_code==503
        monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
        monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED','true')
        assert client.get('/internal/daily-radar/capabilities',headers=headers).json()['enabled'] is True
        assert client.post('/internal/daily-radar/jobs',headers=headers,json={}).status_code==422
        assert client.post('/internal/daily-radar/jobs',headers=headers,json={'run_date':'2026-11-06','symbols':['PRIVATE.TW']}).status_code==422
        response=client.post('/internal/daily-radar/jobs',headers=headers,json={'run_date':'2026-11-06'})
        assert response.status_code==202 and response.json()['data_ready'] is False
        jid=response.json()['job_id']
        assert response.json()['requested_run_date']==response.json()['effective_run_date']=='2026-11-06'
        assert client.post('/internal/daily-radar/jobs',headers=headers,json={'run_date':'2026-11-06'}).json()['job_id']==jid
        assert client.get('/internal/daily-radar/jobs/999999',headers=headers).status_code==404
