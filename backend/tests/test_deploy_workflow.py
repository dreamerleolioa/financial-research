from pathlib import Path
import ast
import json
import os
import re
import subprocess
import sys

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def test_retired_cloud_test_environment_cannot_be_deployed_by_ci():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert 'deploy-gcp-test' not in workflow['jobs']
    assert 'deploy_gcp_test' not in workflow['on']['workflow_dispatch']['inputs']
    for job in workflow['jobs'].values():
        assert job.get('environment') != 'gcp-test'
        for step in job.get('steps', []):
            assert 'scripts/gcp/deploy-test.sh' not in step.get('run', '')
            assert 'scripts/gcp/migrate-test.sh' not in step.get('run', '')
    for required in ('test-backend', 'test-frontend', 'test-postgres-migration'):
        assert required in workflow['jobs']
        assert 'if' not in workflow['jobs'][required]


def test_pages_artifact_name_is_unique_per_run_attempt() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    artifact_name = "github-pages-${{ github.run_attempt }}"
    upload_step = workflow.split("uses: actions/upload-pages-artifact@v3", maxsplit=1)[1].split(
        "- name: Deploy to GitHub Pages", maxsplit=1
    )[0]
    deploy_step = workflow.split("uses: actions/deploy-pages@v4", maxsplit=1)[1]

    assert f"name: {artifact_name}" in upload_step
    assert f"artifact_name: {artifact_name}" in deploy_step


def test_pages_deployment_requires_frontend_verification_on_main_push() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    frontend_job = workflow.split("  test-frontend:\n", 1)[1].split("\n  deploy-frontend:", 1)[0]
    deploy_job = workflow.split("  deploy-frontend:\n", 1)[1]
    assert "needs: [test-backend, test-frontend]" in deploy_job
    for check in ["pnpm lint", "pnpm test:e2e", "pnpm build", "playwright install --with-deps chromium"]:
        assert check in frontend_job
    assert "if:" not in frontend_job


def test_gcp_deployment_waits_for_verification_and_environment_approval():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['deploy-gcp-production']
    assert set(job['needs']) == {'test-backend', 'test-frontend', 'test-postgres-migration'}
    assert job['environment'] == 'gcp-production'
    token_steps = [step['with'] for step in job['steps']
                   if step.get('with', {}).get('token_format') == 'id_token']
    assert len(token_steps) == 1
    assert token_steps[0]['workload_identity_provider'] == '${{ env.GCP_WORKLOAD_IDENTITY_PROVIDER }}'
    assert token_steps[0]['id_token_audience'] == '${{ steps.deploy.outputs.service_url }}'
    assert any(step.get('run') == 'bash scripts/gcp/smoke-test.sh' for step in job['steps'])


def _job_condition_matches(condition, *, event, ref, deploy):
    """Evaluate the actual gate's small Boolean/equality expression, without eval."""
    values = {'github.event_name': event, 'github.ref': ref,
              'inputs.deploy_gcp_production': deploy}
    expression = re.sub(r'github\.event_name|github\.ref|inputs\.deploy_gcp_production',
                        lambda match: repr(values[match[0]]), condition)
    tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')

    def read(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            operation = all if isinstance(node.op, ast.And) else any
            return operation(read(value) for value in node.values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return read(node.left) == read(node.comparators[0])
        raise AssertionError(f'Unsupported deployment condition: {ast.dump(node)}')

    return bool(read(tree.body))


@pytest.mark.parametrize(('event', 'ref', 'deploy', 'pages_allowed'), [
    ('push', 'refs/heads/codex/gcp-test-migration', False, False),
    ('push', 'refs/heads/main', False, True),
    ('push', 'refs/heads/other', True, False),
    ('push', 'refs/heads/codex/gcp-test-migration-extra', True, False),
    ('push', 'refs/tags/codex/gcp-test-migration', True, False),
    ('workflow_dispatch', 'refs/heads/codex/gcp-test-migration', True, False),
    ('workflow_dispatch', 'refs/heads/codex/gcp-test-migration', False, False),
    ('workflow_dispatch', 'refs/heads/main', True, False),
    ('pull_request', 'refs/heads/codex/gcp-test-migration', True, False),
    ('pull_request', 'refs/pull/123/merge', True, False),
    ('schedule', 'refs/heads/codex/gcp-test-migration', True, False),
    ('repository_dispatch', 'refs/heads/codex/gcp-test-migration', True, False),
])
def test_deployment_event_and_branch_boundaries(event, ref, deploy, pages_allowed):
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']['push']['branches']) == {'main', 'codex/gcp-test-migration'}
    assert 'tags' not in workflow['on']['push']
    assert 'deploy-gcp-test' not in workflow['jobs']
    assert _job_condition_matches(workflow['jobs']['deploy-frontend']['if'],
                                  event=event, ref=ref, deploy=deploy) is pages_allowed


@pytest.fixture
def fake_cloud_commands(tmp_path):
    log = tmp_path / "calls.jsonl"
    executable = tmp_path / "gcloud"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
with open(os.environ["CLOUD_CALL_LOG"], "a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:4] == ["run", "services", "describe"]:
    print("https://financial-test.example.run.app")
elif sys.argv[1:3] == ["auth", "print-identity-token"]:
    print("fake-test-token")
''')
    executable.chmod(0o700)
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/sh\nprintf '{\"status\":\"ok\"}'\n")
    curl.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "GCP_PROJECT_ID": "financial-research-20261019", "CLOUD_CALL_LOG": str(log)}
    return env, log


def test_deploy_script_uses_digest_test_service_and_private_access(fake_cloud_commands):
    env, log = fake_cloud_commands
    image = "asia-east1-docker.pkg.dev/financial-research-20261019/financial-research/backend@sha256:" + "a" * 64
    result = subprocess.run(["bash", "scripts/gcp/deploy-test.sh", image], cwd=REPO_ROOT,
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    deploy = json.loads(log.read_text().splitlines()[0])
    assert deploy[:3] == ["run", "deploy", "financial-research-test"]
    assert f"--image={image}" in deploy
    assert "--no-allow-unauthenticated" in deploy
    assert "--env-vars-file=scripts/gcp/test-env.yaml" in deploy
    assert not any(arg.startswith("--set-env-vars") for arg in deploy)
    assert not any('print-identity-token' in line for line in log.read_text().splitlines())


def test_private_smoke_check_refuses_missing_token(fake_cloud_commands):
    env, _ = fake_cloud_commands
    env['GCP_TEST_SERVICE_URL'] = 'https://financial-test.example.run.app'
    env.pop('GCP_TEST_ID_TOKEN', None)
    result = subprocess.run(['bash', 'scripts/gcp/smoke-test.sh'], cwd=REPO_ROOT,
                            env=env, capture_output=True)
    assert result.returncode != 0
    assert b'Mint an audience-specific ID token' in result.stderr


@pytest.mark.parametrize("image", ["latest", "gcr.io/other/backend@sha256:" + "a" * 64])
def test_deploy_script_refuses_unpinned_or_other_project_image(fake_cloud_commands, image):
    env, log = fake_cloud_commands
    result = subprocess.run(["bash", "scripts/gcp/deploy-test.sh", image], cwd=REPO_ROOT,
                            env=env, capture_output=True)
    assert result.returncode != 0
    assert not log.exists()


def test_migration_job_refuses_unconfirmed_writer_state(fake_cloud_commands):
    env, log = fake_cloud_commands
    env.pop("CONFIRM_TEST_WRITERS_DRAINED", None)
    result = subprocess.run(["bash", "scripts/gcp/migrate-test.sh", "unused"], cwd=REPO_ROOT,
                            env=env, capture_output=True)
    assert result.returncode != 0
    assert not log.exists()
