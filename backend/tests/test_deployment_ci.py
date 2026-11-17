import pytest

from verify_deployment_ci import find_verified_run

REPO = 'owner/repository'
COMMIT = '1' * 40
PR_HEAD = '2' * 40
TREE = '3' * 40
ARTIFACT_NAME = f'verified-ci-tree-{TREE}'
JOBS_ENDPOINT = 'actions/runs/123/jobs?per_page=100&filter=all'


@pytest.fixture
def ci_evidence():
    pull_request = {
        'merged_at': '2026-11-17T00:00:00Z',
        'merge_commit_sha': COMMIT,
        'base': {'ref': 'main', 'repo': {'full_name': REPO}},
        'head': {'sha': PR_HEAD, 'ref': 'fix/topic', 'repo': {'id': 12}},
    }
    artifact = {'name': ARTIFACT_NAME, 'expired': False, 'workflow_run': {'id': 123}}
    run = {
        'path': '.github/workflows/deploy.yml',
        'status': 'completed',
        'conclusion': 'success',
        'event': 'pull_request',
        'head_sha': PR_HEAD,
        'head_branch': 'fix/topic',
        'head_repository': {'id': 12, 'full_name': REPO},
        'html_url': 'https://github.com/owner/repository/actions/runs/123',
    }
    record_job = {
        'name': 'Record verified CI tree', 'conclusion': 'success', 'run_attempt': 2,
        'steps': [{'name': f'Publish verified CI tree {TREE}', 'conclusion': 'success'}],
    }
    required_jobs = [
        {'name': 'Backend Tests', 'conclusion': 'success', 'run_attempt': 1},
        {'name': 'Frontend build', 'conclusion': 'failure', 'run_attempt': 1},
        {'name': 'Frontend build', 'conclusion': 'success', 'run_attempt': 2},
        {'name': 'PostgreSQL 17 migration rehearsal', 'conclusion': 'success', 'run_attempt': 1},
    ]
    return {
        f'commits/{COMMIT}/pulls?per_page=100': [[pull_request]],
        f'actions/artifacts?name={ARTIFACT_NAME}&per_page=100': [{'artifacts': [artifact]}],
        'actions/runs/123': run,
        JOBS_ENDPOINT: [{'jobs': [record_job, *required_jobs]}],
    }


def verified_run(evidence):
    def api(endpoint, *, paginate=False):
        result = evidence[endpoint]
        assert paginate == isinstance(result, list)
        return result

    return find_verified_run(api, REPO, COMMIT, TREE)


def test_squash_merge_uses_ci_for_the_same_tree(ci_evidence):
    artifacts = ci_evidence[f'actions/artifacts?name={ARTIFACT_NAME}&per_page=100']
    artifacts.insert(0, {'artifacts': []})
    assert verified_run(ci_evidence) == ci_evidence['actions/runs/123']['html_url']


def test_ci_only_dispatch_can_certify_the_main_commit_without_a_pr(ci_evidence):
    ci_evidence[f'commits/{COMMIT}/pulls?per_page=100'] = [[]]
    ci_evidence['actions/runs/123'].update(event='workflow_dispatch', head_sha=COMMIT, head_branch='main')
    assert verified_run(ci_evidence) == ci_evidence['actions/runs/123']['html_url']


@pytest.mark.parametrize(('job_name', 'step_tree', 'step_result'), [
    ('Backend Tests', TREE, 'success'),
    ('Record verified CI tree', '4' * 40, 'success'),
    ('Record verified CI tree', TREE, 'skipped'),
])
def test_artifact_requires_a_successful_record_step_for_the_same_tree(ci_evidence, job_name, step_tree, step_result):
    job = ci_evidence[JOBS_ENDPOINT][0]['jobs'][0]
    job['name'] = job_name
    job['steps'] = [{'name': f'Publish verified CI tree {step_tree}', 'conclusion': step_result}]
    assert verified_run(ci_evidence) is None


def test_ci_requires_the_latest_result_of_each_required_job(ci_evidence):
    ci_evidence[JOBS_ENDPOINT].append({'jobs': [
        {'name': 'Frontend build', 'conclusion': 'skipped', 'run_attempt': 3},
    ]})
    assert verified_run(ci_evidence) is None


@pytest.mark.parametrize('invalid', [
    'no_artifact', 'different_tree', 'expired', 'failed', 'unfinished', 'wrong_workflow',
    'wrong_pr_head', 'unrelated_merge', 'unmerged_pr', 'wrong_base', 'wrong_head_repository',
    'wrong_manual_branch', 'wrong_manual_commit', 'main_push',
])
def test_deployment_requires_completed_ci_for_the_exact_content(ci_evidence, invalid):
    pages = ci_evidence[f'actions/artifacts?name={ARTIFACT_NAME}&per_page=100']
    artifact = pages[0]['artifacts'][0]
    pull_request = ci_evidence[f'commits/{COMMIT}/pulls?per_page=100'][0][0]
    run = ci_evidence['actions/runs/123']
    match invalid:
        case 'no_artifact':
            pages.clear()
        case 'different_tree':
            artifact['name'] = f'verified-ci-tree-{"0" * 40}'
        case 'expired':
            artifact['expired'] = True
        case 'failed':
            run['conclusion'] = 'failure'
        case 'unfinished':
            run['status'] = 'in_progress'
        case 'wrong_workflow':
            run['path'] = '.github/workflows/other.yml'
        case 'wrong_pr_head':
            run['head_sha'] = COMMIT
        case 'unrelated_merge':
            pull_request['merge_commit_sha'] = PR_HEAD
        case 'unmerged_pr':
            pull_request['merged_at'] = None
        case 'wrong_base':
            pull_request['base']['ref'] = 'topic'
        case 'wrong_head_repository':
            run['head_repository']['id'] = 99
        case 'wrong_manual_branch':
            run.update(event='workflow_dispatch', head_sha=COMMIT)
        case 'wrong_manual_commit':
            run.update(event='workflow_dispatch', head_branch='main')
        case 'main_push':
            run.update(event='push', head_sha=COMMIT, head_branch='main')
    assert verified_run(ci_evidence) is None
