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
RELEASE_GATE_WORKFLOW = WORKFLOW.with_name("investment-discipline-release-gate.yml")
CI_JOBS = ('test-backend', 'test-frontend', 'test-postgres-migration')


def test_retired_cloud_test_environment_cannot_be_deployed_by_ci():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert 'deploy-gcp-test' not in workflow['jobs']
    assert 'deploy_gcp_test' not in workflow['on']['workflow_dispatch']['inputs']
    for job in workflow['jobs'].values():
        assert job.get('environment') != 'gcp-test'
        for step in job.get('steps', []):
            assert 'scripts/gcp/deploy-test.sh' not in step.get('run', '')
            assert 'scripts/gcp/migrate-test.sh' not in step.get('run', '')


def test_pages_artifact_name_is_unique_per_run_attempt() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["deploy-frontend"]["steps"]
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-pages-artifact@"))
    deploy = next(step for step in steps if step.get("uses", "").startswith("actions/deploy-pages@"))
    artifact_name = "github-pages-${{ github.run_attempt }}"

    assert upload["with"]["name"] == deploy["with"]["artifact_name"] == artifact_name


@pytest.mark.parametrize(("path", "job_name"), [
    (WORKFLOW, "test-frontend"),
    (WORKFLOW, "deploy-frontend"),
    (RELEASE_GATE_WORKFLOW, "frontend-build"),
])
def test_frontend_ci_uses_the_repository_tool_versions(path: Path, job_name: str) -> None:
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"][job_name]["steps"]
    pnpm = next(step for step in steps if step.get("uses", "").startswith("pnpm/action-setup@"))
    node = next(step for step in steps if step.get("uses", "").startswith("actions/setup-node@"))
    install = next(step for step in steps if step.get("run", "").startswith("pnpm install"))

    assert pnpm["with"]["package_json_file"] == "frontend/package.json"
    assert "version" not in pnpm["with"]
    assert node["with"]["node-version-file"] == "frontend/.node-version"
    assert node["with"]["cache-dependency-path"] == "frontend/pnpm-lock.yaml"
    assert steps.index(pnpm) < steps.index(node) < steps.index(install)
    assert install["run"] == "pnpm install --frozen-lockfile"


def test_deployment_reuses_all_ci_checks() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    recorder = jobs['record-ci-success']
    assert set(recorder['needs']) == set(CI_JOBS)
    record_step = next(step for step in recorder['steps'] if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert record_step['name'] == 'Publish verified CI tree ${{ steps.tree.outputs.sha }}'
    assert record_step['with']['name'] == 'verified-ci-tree-${{ steps.tree.outputs.sha }}'
    assert jobs['deploy-frontend']['needs'] == ['verify-deployment-ci', 'deploy-gcp-production']
    assert jobs['deploy-gcp-production']['needs'] == ['verify-deployment-ci']
    assert any(step.get('run') == 'python3 .github/scripts/verify_deployment_ci.py'
               for step in jobs['verify-deployment-ci']['steps'])
    frontend_commands = [step.get('run', '') for step in jobs['test-frontend']['steps']]
    for check in ["pnpm lint", "pnpm test:e2e", "pnpm build", "playwright install --with-deps chromium"]:
        assert any(check in command for command in frontend_commands)


@pytest.mark.parametrize('deployment', ['deploy-frontend', 'deploy-gcp-production'])
def test_deployment_rechecks_ci_on_job_reruns_before_using_deploy_credentials(deployment):
    job = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)['jobs'][deployment]
    assert job['permissions']['actions'] == 'read'
    assert job['permissions']['pull-requests'] == 'read'
    assert job['steps'][0]['uses'].startswith('actions/checkout@')
    check = job['steps'][1]
    assert check['if'] == "github.run_attempt != '1'"
    assert check['run'] == 'python3 .github/scripts/verify_deployment_ci.py'
    default_directory = job.get('defaults', {}).get('run', {}).get('working-directory', '${{ github.workspace }}')
    assert check.get('working-directory', default_directory) == '${{ github.workspace }}'


def test_gcp_deployment_waits_for_verification_and_environment_approval():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['deploy-gcp-production']
    assert job['needs'] == ['verify-deployment-ci']
    assert job['environment'] == 'gcp-production'
    token_steps = [step['with'] for step in job['steps']
                   if step.get('with', {}).get('token_format') == 'id_token']
    assert len(token_steps) == 1
    assert token_steps[0]['workload_identity_provider'] == '${{ env.GCP_WORKLOAD_IDENTITY_PROVIDER }}'
    assert token_steps[0]['id_token_audience'] == '${{ steps.deploy.outputs.service_url }}'
    assert any(step.get('run') == 'bash scripts/gcp/smoke-test.sh' for step in job['steps'])
    commands = [step.get('run', '') for step in job['steps']]
    assert 'GCP_MIGRATION_IMAGE="$image" bash scripts/gcp/test-migration.sh --image-only' in '\n'.join(commands)
    assert any('bash scripts/gcp/deploy-production.sh' in command for command in commands)


def test_production_release_couples_migration_to_verified_image_and_single_approval():
    jobs = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)['jobs']
    assert [name for name, job in jobs.items() if job.get('environment') == 'gcp-production'] == [
        'deploy-gcp-production'
    ]
    steps = jobs['deploy-gcp-production']['steps']
    by_name = {step.get('name'): step for step in steps}
    ordered = [
        'Verify the exact image against PostgreSQL 17', 'Save verified image for deployment retries',
        'Check schema for the approved release', 'Authenticate as the migration release identity',
        'Apply the eligible migration', 'Verify the schema and deploy the same image',
        'Verify private service health',
    ]
    assert [step['name'] for step in steps if step.get('name') in ordered] == ordered
    expected_digest = '${{ steps.previous-image.outputs.digest || steps.image.outputs.digest }}'
    for name in (ordered[2], ordered[4], ordered[5]):
        assert by_name[name]['env']['IMAGE_DIGEST'] == expected_digest
    for name in ordered[3:5]:
        assert by_name[name]['if'] == "steps.preflight.outputs.migration_required == 'true'"
    migration_auth = by_name[ordered[3]]['with']
    assert migration_auth['service_account'].startswith('financial-prod-migrate-release@')
    assert migration_auth['export_environment_variables'] == 'false'
    assert by_name[ordered[4]]['env']['CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE'] == (
        '${{ steps.migration-auth.outputs.credentials_file_path }}'
    )
    assert 'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE' not in by_name[ordered[5]]['env']


def _job_condition_matches(condition, *, event, ref, deploy, backend='success',
                           deploy_ref='refs/heads/main', ci='success', cancelled=False):
    """Evaluate the actual gate's small Boolean/equality expression, without eval."""
    values = {'github.event_name': event, 'github.ref': ref, 'inputs.deploy_gcp_production': deploy,
              'needs.deploy-gcp-production.result': backend, 'vars.GCP_PRODUCTION_DEPLOY_REF': deploy_ref,
              'needs.verify-deployment-ci.result': ci, 'cancelled()': cancelled}
    expression = re.sub('|'.join(map(re.escape, values)),
                        lambda match: repr(values[match[0]]), condition)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    tree = ast.parse(re.sub(r'!(?!=)', 'not ', expression), mode='eval')

    def read(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not read(node.operand)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            operation = all if isinstance(node.op, ast.And) else any
            return operation(read(value) for value in node.values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            if isinstance(node.ops[0], ast.Eq):
                return read(node.left) == read(node.comparators[0])
            if isinstance(node.ops[0], ast.NotEq):
                return read(node.left) != read(node.comparators[0])
        raise AssertionError(f'Unsupported deployment condition: {ast.dump(node)}')

    return bool(read(tree.body))


@pytest.mark.parametrize(('event', 'ref', 'deploy', 'run_ci', 'record_ci', 'verify_cd'), [
    ('pull_request', 'refs/pull/124/merge', False, True, True, False),
    ('push', 'refs/heads/main', False, False, False, True),
    ('push', 'refs/heads/codex/gcp-test-migration', False, True, False, False),
    ('workflow_dispatch', 'refs/heads/main', True, False, False, True),
    ('workflow_dispatch', 'refs/heads/main', False, True, True, False),
    ('workflow_dispatch', 'refs/heads/topic', False, True, False, False),
    ('workflow_dispatch', 'refs/heads/topic', True, False, False, False),
])
def test_ci_and_cd_do_not_repeat_checks(event, ref, deploy, run_ci, record_ci, verify_cd):
    jobs = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)['jobs']
    expected_jobs = dict.fromkeys(CI_JOBS, run_ci)
    expected_jobs.update({'record-ci-success': record_ci, 'verify-deployment-ci': verify_cd})
    for name, expected in expected_jobs.items():
        assert _job_condition_matches(jobs[name]['if'], event=event, ref=ref, deploy=deploy) is expected


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


@pytest.mark.parametrize(('backend', 'deploy_ref', 'ci', 'cancelled', 'pages_allowed'), [
    ('success', 'refs/heads/main', 'success', False, True),
    ('failure', 'refs/heads/main', 'success', False, False),
    ('skipped', 'refs/heads/main', 'success', False, False),
    ('skipped', '', 'success', False, True),
    ('skipped', '', 'failure', False, False),
    ('success', 'refs/heads/main', 'success', True, False),
])
def test_pages_waits_for_configured_backend_release(backend, deploy_ref, ci, cancelled, pages_allowed):
    job = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)['jobs']['deploy-frontend']
    assert _job_condition_matches(job['if'], event='push', ref='refs/heads/main', deploy=False,
                                  backend=backend, deploy_ref=deploy_ref, ci=ci, cancelled=cancelled) is pages_allowed


@pytest.mark.parametrize(('arguments', 'targets'), [
    ([], ['tests/test_gcp_migration.py', 'tests/test_gcp_production.py',
          'tests/test_gcp_schema_preflight.py', 'tests/test_gcp_online_migration.py',
          'tests/test_gcp_restore.py']),
    (['--image-only'], ['tests/test_gcp_migration.py::test_runtime_image_verifies_schema_without_ddl_permissions']),
])
def test_pg17_rehearsal_selects_the_ci_suite_or_the_single_image_check(tmp_path, arguments, targets):
    docker = tmp_path / 'docker'
    docker.write_text('#!/bin/sh\nif [ "$1" = run ]; then echo pg17-fixture; fi\n')
    docker.chmod(0o700)
    uv = tmp_path / 'uv'
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    uv.chmod(0o700)
    env = {**os.environ, 'PATH': f'{tmp_path}{os.pathsep}{os.environ["PATH"]}',
           'GCP_MIGRATION_IMAGE': 'runtime-image:fixture'}
    result = subprocess.run(['bash', 'scripts/gcp/test-migration.sh', *arguments],
                            cwd=REPO_ROOT, env=env, check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == ['run', 'pytest', *targets, '-q']


def test_image_only_rehearsal_requires_an_image_before_starting_postgres(monkeypatch):
    monkeypatch.delenv('GCP_MIGRATION_IMAGE', raising=False)
    result = subprocess.run(['bash', 'scripts/gcp/test-migration.sh', '--image-only'],
                            cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Set GCP_MIGRATION_IMAGE' in result.stderr


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
