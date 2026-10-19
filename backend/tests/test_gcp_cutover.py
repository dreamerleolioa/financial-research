"""Cutover safety contracts; no live cloud calls."""
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import yaml

from ai_stock_sentinel.api import app

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts/gcp'


@pytest.fixture(autouse=True)
def no_database_access():
    from ai_stock_sentinel.db.session import get_db
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.clear()
    def forbidden():
        raise AssertionError('Cutover admission tests must not open a database')
    app.dependency_overrides[get_db] = forbidden
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('path,method', [('/auth/google','POST'),('/analyze','POST'),
    ('/internal/daily-radar/run-scoring','POST'),('/daily-radar/latest','GET'),('/auth/me','GET')])
def test_maintenance_blocks_before_auth_database_or_provider(path, method, monkeypatch):
    monkeypatch.setenv('APP_MAINTENANCE_MODE', 'true')
    response = TestClient(app).request(method, path, json={})
    assert response.status_code == 503
    assert response.json() == {'detail': 'Service temporarily unavailable for maintenance'}
    assert response.headers['retry-after'] == '60'


def test_maintenance_keeps_health_and_cors_available_and_invalid_value_fails_closed(monkeypatch):
    monkeypatch.setenv('APP_MAINTENANCE_MODE', 'misspelled')
    client = TestClient(app)
    assert client.get('/health').json() == {'status': 'ok'}
    assert client.post('/auth/google',json={}).status_code == 503
    response = client.options('/analyze', headers={'Origin':'http://localhost:5173',
                                                 'Access-Control-Request-Method':'POST'})
    assert response.status_code == 200
    monkeypatch.setenv('APP_MAINTENANCE_MODE', 'false')
    assert client.get('/auth/me').status_code in (401,403)


def all_api_routes(routes):
    for route in routes:
        if hasattr(route, 'original_router'):
            yield from all_api_routes(route.original_router.routes)
        elif hasattr(route, 'dependant'):
            yield route


def dependency_calls(dependant):
    return [dependant.call] + [call for child in dependant.dependencies for call in dependency_calls(child)]


def test_public_entry_has_explicit_auth_boundaries_for_every_route():
    from ai_stock_sentinel.auth.dependencies import get_current_user
    from ai_stock_sentinel.calibration.auth import require_calibration_internal_auth
    public = {'/health','/auth/google','/auth/google/code',
              '/daily-radar/latest','/daily-radar/symbol/{symbol}','/daily-radar/validation','/daily-radar/{run_date}'}
    for route in all_api_routes(app.routes):
        calls = dependency_calls(route.dependant)
        if route.path.startswith('/internal/'):
            assert require_calibration_internal_auth in calls, route.path
        elif route.path not in public:
            assert get_current_user in calls, route.path


def test_internal_token_rejection_prevents_work_without_cloud_iam(monkeypatch):
    monkeypatch.setenv('APP_MAINTENANCE_MODE','false')
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN','cutover-fixture-token')
    client = TestClient(app)
    for route in all_api_routes(app.routes):
        if not route.path.startswith('/internal/'):continue
        for headers,expected in [({},401),({'Authorization':'Bearer wrong'},403)]:
            assert client.post(route.path,json={},headers=headers).status_code == expected, route.path


def test_public_service_is_only_accepted_with_explicit_mode_and_maintenance_flag():
    from test_gcp_production import service, IMAGE
    verifier=load('verify_production_service');data=service()
    data['metadata']['annotations']['run.googleapis.com/invoker-iam-disabled']='true'
    with pytest.raises(ValueError):verifier.verify(data,{'bindings':[]},IMAGE)
    with pytest.raises(ValueError):verifier.verify(data,{'bindings':[]},IMAGE,access_mode='public')
    data['spec']['template']['spec']['containers'][0]['env'].append({'name':'APP_MAINTENANCE_MODE','value':'true'})
    assert verifier.verify(data,{'bindings':[]},IMAGE,access_mode='public')
    with pytest.raises(ValueError):verifier.verify(data,{'bindings':[{'members':['allUsers']}]},IMAGE,access_mode='public')


def test_main_only_production_ci_does_not_deploy_feature_branch():
    from test_deploy_workflow import _job_condition_matches
    job=yaml.load((ROOT/'.github/workflows/deploy.yml').read_text(),Loader=yaml.BaseLoader)['jobs']['deploy-gcp-production']
    condition=job['if'].replace('vars.GCP_PRODUCTION_DEPLOY_REF',repr('refs/heads/codex/gcp-test-migration'))
    assert not _job_condition_matches(condition,event='push',ref='refs/heads/codex/gcp-test-migration',deploy=True)
    assert job['environment']=='gcp-production'


def test_ci_environment_delta_contains_baseline_without_operator_settings():
    verifier=load('verify_production_service')
    values=dict(item.split('=',1) for item in verifier.env_updates().removeprefix('^|^').split('|'))
    assert values==yaml.safe_load((SCRIPTS/'production-env.yaml').read_text())
    assert 'APP_MAINTENANCE_MODE' not in values


def test_ci_does_not_write_a_previously_observed_maintenance_value():
    verifier=load('verify_production_service')
    value=verifier.env_updates()
    assert value.startswith('^|^')
    assert 'APP_MAINTENANCE_MODE' not in value
    assert 'EXPECTED_DATABASE_NAME=financial_research' in value


@pytest.mark.parametrize('key,value', [('PGHOSTADDR','203.0.113.10'),('PGSERVICE','other'),
    ('PGSERVICEFILE','/tmp/other-pg-service'),('PGSYSCONFDIR','/tmp/other-libpq')])
def test_restore_rejects_connection_environment_overrides(key,value,monkeypatch):
    module=load('restore-production')
    for flag in ('CONFIRM_FINAL_CUTOVER_APPROVED','CONFIRM_SOURCE_WRITERS_DRAINED',
                 'CONFIRM_TARGET_WRITERS_DRAINED','CONFIRM_SCHEDULES_PAUSED'):
        monkeypatch.setenv(flag,'yes')
    monkeypatch.setenv('PGHOST','127.0.0.1')
    monkeypatch.setenv('CONFIRM_CLOUD_SQL_PROXY',module.INSTANCE)
    monkeypatch.setenv('PGDATABASE',module.DATABASE)
    monkeypatch.setenv('PGUSER',module.OWNER)
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError,match='override'):module.require_apply_approval()
