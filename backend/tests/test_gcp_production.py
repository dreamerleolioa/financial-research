import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml
from test_gcp_migration import pg17  # noqa: F401 - shared isolated PG17 fixture

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts/gcp'
IMAGE = 'asia-east1-docker.pkg.dev/financial-research-20261019/financial-research/backend@sha256:' + 'a' * 64


def load_verifier():
    spec = importlib.util.spec_from_file_location('production_service', SCRIPTS / 'verify_production_service.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def service():
    env = yaml.safe_load((SCRIPTS / 'production-env.yaml').read_text())
    secrets = {
        'DATABASE_URL': 'database-url', 'JWT_SECRET': 'jwt',
        'GOOGLE_CLIENT_ID': 'google-client-id', 'GOOGLE_CLIENT_SECRET': 'google-client-secret',
        'DAILY_RADAR_INTERNAL_TOKEN': 'internal-token', 'FINMIND_API_TOKEN': 'finmind-token',
    }
    return {
        'metadata': {'name': 'financial-research-prod', 'annotations': {}},
        'spec': {'template': {'metadata': {'annotations': {
            'run.googleapis.com/cloudsql-instances': 'financial-research-20261019:asia-east1:financial-postgres'}},
            'spec': {'serviceAccountName': 'financial-prod-api@financial-research-20261019.iam.gserviceaccount.com',
                     'containers': [{'image': IMAGE, 'env':
                         [{'name': k, 'value': str(v)} for k, v in env.items()] +
                         [{'name': k, 'valueFrom': {'secretKeyRef': {'name': 'financial-prod-' + v, 'key': '1'}}}
                          for k, v in secrets.items()]}]}}},
        'status': {'url': 'https://financial-research-prod-918645310958.asia-east1.run.app',
                   'latestReadyRevisionName': 'prod-00001', 'latestCreatedRevisionName': 'prod-00001',
                   'traffic': [{'revisionName': 'prod-00001', 'percent': 100}]},
    }


def test_exact_private_production_service_and_digest():
    assert load_verifier().verify(service(), {'bindings': []}, IMAGE).startswith('https://financial-research-prod-')


@pytest.mark.parametrize('change', ['test_db', 'migration', 'runtime', 'public', 'disabled_iam',
                                   'plaintext_secret', 'test_secret', 'image', 'traffic', 'url', 'pool'])
def test_wrong_target_or_weakened_service_is_rejected(change):
    data = service()
    policy = {'bindings': []}
    container = data['spec']['template']['spec']['containers'][0]
    values = {v['name']: v for v in container['env']}
    if change == 'test_db': values['EXPECTED_DATABASE_NAME']['value'] = 'financial_research_test'
    if change == 'migration': values['DATABASE_MIGRATION_MODE']['value'] = 'upgrade'
    if change == 'runtime': data['spec']['template']['spec']['serviceAccountName'] = 'financial-prod-migrator@example.invalid'
    if change == 'public': policy['bindings'] = [{'members': ['allAuthenticatedUsers']}]
    if change == 'disabled_iam': data['metadata']['annotations']['run.googleapis.com/invoker-iam-disabled'] = 'true'
    if change == 'plaintext_secret': values['DATABASE_URL'].update(value='postgresql://unsafe'); values['DATABASE_URL'].pop('valueFrom')
    if change == 'test_secret': values['DATABASE_URL']['valueFrom']['secretKeyRef']['name'] = 'financial-test-database-url'
    if change == 'image': container['image'] = IMAGE.replace('a' * 64, 'b' * 64)
    if change == 'traffic': data['status']['traffic'] = [{'revisionName': 'old', 'percent': 100}]
    if change == 'url': data['status']['url'] = 'https://other-project.run.app'
    if change == 'pool': values['DB_MAX_OVERFLOW']['value'] = '10'
    with pytest.raises(ValueError): load_verifier().verify(data, policy, IMAGE)


@pytest.mark.parametrize('event,ref,configured,dispatch,expected', [
    ('push', 'refs/heads/main', 'refs/heads/main', False, True),
    ('push', 'refs/heads/codex/gcp-test-migration', 'refs/heads/codex/gcp-test-migration', False, True),
    ('push', 'refs/heads/main', 'refs/heads/codex/gcp-test-migration', False, False),
    ('push', 'refs/heads/other', 'refs/heads/other', False, False),
    ('push', 'refs/heads/main', '', False, False),
    ('pull_request', 'refs/heads/main', 'refs/heads/main', True, False),
    ('workflow_dispatch', 'refs/heads/main', 'refs/heads/main', False, False),
    ('workflow_dispatch', 'refs/heads/main', 'refs/heads/main', True, True),
])
def test_production_job_event_gate(event, ref, configured, dispatch, expected):
    from test_deploy_workflow import _job_condition_matches
    job = yaml.load((ROOT / '.github/workflows/deploy.yml').read_text(), Loader=yaml.BaseLoader)['jobs']['deploy-gcp-production']
    condition = job['if'].replace('vars.GCP_PRODUCTION_DEPLOY_REF', repr(configured)).replace('inputs.deploy_gcp_production', 'inputs.deploy_gcp_test')
    assert _job_condition_matches(condition, event=event, ref=ref, deploy=dispatch) is expected
    assert job['environment'] == 'gcp-production'
    assert set(job['needs']) == {'test-backend', 'test-frontend', 'test-postgres-migration'}


def test_unconfirmed_production_migration_never_calls_cloud(tmp_path):
    fake = tmp_path / 'gcloud'
    fake.write_text('#!/bin/sh\ntouch "' + str(tmp_path / 'cloud-called') + '"\n')
    fake.chmod(0o700)
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'GCP_PROJECT_ID': 'financial-research-20261019'}
    env.pop('CONFIRM_PRODUCTION_WRITERS_DRAINED', None)
    result = subprocess.run(['bash', str(SCRIPTS / 'migrate-production.sh'), IMAGE], env=env, capture_output=True)
    assert result.returncode != 0
    assert b'explicitly confirm' in result.stderr
    assert not (tmp_path / 'cloud-called').exists()


def test_production_job_resolves_without_test_environment_variables():
    import re
    job = yaml.load((ROOT / '.github/workflows/deploy.yml').read_text(), Loader=yaml.BaseLoader)['jobs']['deploy-gcp-production']
    context = {
        'GCP_PRODUCTION_PROJECT_ID': 'financial-research-20261019',
        'GCP_PRODUCTION_WORKLOAD_IDENTITY_PROVIDER': 'production-provider',
        'GCP_PRODUCTION_DEPLOY_SERVICE_ACCOUNT': 'production-deployer',
    }
    resolved = {k: re.sub(r'\$\{\{ vars\.([A-Z_]+) \}\}', lambda m: context.get(m[1], ''), v)
                for k, v in job['env'].items()}
    assert resolved['GCP_PROJECT_ID'] == 'financial-research-20261019'
    assert resolved['GCP_WORKLOAD_IDENTITY_PROVIDER'] == 'production-provider'
    assert resolved['GCP_DEPLOY_SERVICE_ACCOUNT'] == 'production-deployer'


def test_production_verifier_is_fail_closed_with_python_optimization(tmp_path):
    data = service()
    data['metadata']['annotations']['run.googleapis.com/invoker-iam-disabled'] = 'true'
    a, b = tmp_path / 'service.json', tmp_path / 'iam.json'
    a.write_text(json.dumps(data)); b.write_text('{"bindings":[]}')
    result = subprocess.run([sys.executable, '-O', str(SCRIPTS / 'verify_production_service.py'), str(a), str(b)], capture_output=True)
    assert result.returncode != 0


def test_production_deploy_checks_existing_target_and_does_not_run_migrations(tmp_path):
    log = tmp_path / 'cloud.jsonl'
    current = service()
    executable = tmp_path / 'gcloud'
    executable.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
args=sys.argv[1:]
with open(os.environ['CLOUD_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[:2]==['auth','list']:print('financial-prod-deploy@financial-research-20261019.iam.gserviceaccount.com')
elif args[:3]==['run','services','describe']:print(os.environ['SERVICE_JSON'])
elif args[:3]==['run','services','get-iam-policy']:print('{"bindings":[]}')
''')
    executable.chmod(0o700)
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'GCP_PROJECT_ID': 'financial-research-20261019', 'CLOUD_LOG': str(log),
           'SERVICE_JSON': json.dumps(current)}
    result = subprocess.run(['bash', str(SCRIPTS / 'deploy-production.sh'), IMAGE],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    deploy = [call for call in calls if call[:2] == ['run', 'deploy']]
    assert len(deploy) == 1
    assert deploy[0][2] == 'financial-research-prod'
    # Updating the pre-checked private service preserves IAM. The deployer
    # intentionally lacks setIamPolicy; no access-policy mutation is requested.
    assert not any('allow-unauthenticated' in value or 'invoker-iam-check' in value for value in deploy[0])
    assert '--env-vars-file=scripts/gcp/production-env.yaml' in deploy[0]
    assert not any(call[:2] == ['run', 'jobs'] for call in calls)
    current['spec']['template']['spec']['containers'][0]['env'][0]['value'] = 'upgrade'
    env['SERVICE_JSON'] = json.dumps(current)
    log.unlink()
    result = subprocess.run(['bash', str(SCRIPTS / 'deploy-production.sh'), IMAGE], cwd=ROOT, env=env, capture_output=True)
    assert result.returncode != 0
    assert not any(json.loads(line)[:2] == ['run', 'deploy'] for line in log.read_text().splitlines())


def test_production_database_roles_enforce_real_permissions(pg17, tmp_path, monkeypatch):
    import test_gcp_migration as existing
    # Execute the complete privilege matrix against disposable fixture DBs.
    # Only environment-specific names change; all production SQL statements run.
    for source, target in [('bootstrap-production-roles.sql', 'bootstrap-test-roles.sql'),
                           ('configure-production-privileges.sql', 'configure-test-privileges.sql')]:
        text = (SCRIPTS / source).read_text().replace('financial_research', 'financial_research_test')
        text = text.replace('financial_prod_migrator', 'financial_test_migrator').replace(
            'financial_prod_api', 'financial_test_api').replace('financial_prod_debug_readonly', 'financial_debug_readonly')
        (tmp_path / target).write_text(text)
    monkeypatch.setattr(existing, 'SCRIPTS', tmp_path)
    existing.test_test_database_roles_enforce_real_permissions(pg17)
