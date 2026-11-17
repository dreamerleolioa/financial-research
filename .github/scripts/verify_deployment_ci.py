"""Require successful CI for the Git tree of the commit being deployed."""

import json
import os
import subprocess


WORKFLOW = '.github/workflows/deploy.yml'
RECORD_JOB = 'Record verified CI tree'
REQUIRED_JOBS = ('Backend Tests', 'Frontend build', 'PostgreSQL 17 migration rehearsal', RECORD_JOB)


def recorded_tree_matches(api, run_id, tree):
    # Any job in the run can upload an artifact. Confirm the tree in the recording job's own result.
    # GitHub can reuse successful prerequisite jobs from an earlier attempt when rerunning failed jobs.
    pages = api(f'actions/runs/{run_id}/jobs?per_page=100&filter=all', paginate=True)
    latest_jobs = {}
    for page in pages:
        for job in page['jobs']:
            previous = latest_jobs.get(job['name'])
            if previous is None or job['run_attempt'] > previous['run_attempt']:
                latest_jobs[job['name']] = job
    if any(latest_jobs.get(name, {}).get('conclusion') != 'success' for name in REQUIRED_JOBS):
        return False
    return any(
        step['name'] == f'Publish verified CI tree {tree}' and step['conclusion'] == 'success'
        for step in latest_jobs[RECORD_JOB]['steps']
    )


def find_verified_run(api, repository, commit, tree):
    pulls = api(f'commits/{commit}/pulls?per_page=100', paginate=True)
    merged_heads = {
        (pr['head']['sha'], pr['head']['ref'], pr['head']['repo']['id'])
        for page in pulls
        for pr in page
        if pr['merged_at'] and pr['merge_commit_sha'] == commit
        and pr['base']['ref'] == 'main' and pr['base']['repo']['full_name'] == repository
        and pr['head']['repo'] is not None
    }

    # The artifact name locates candidate runs. Only metadata crosses from CI to CD.
    artifact_name = f'verified-ci-tree-{tree}'
    pages = api(f'actions/artifacts?name={artifact_name}&per_page=100', paginate=True)
    for page in pages:
        for artifact in page['artifacts']:
            if artifact['name'] != artifact_name or artifact['expired'] or not artifact.get('workflow_run'):
                continue
            run_id = artifact['workflow_run']['id']
            run = api(f'actions/runs/{run_id}')
            if run['path'] != WORKFLOW or run['status'] != 'completed' or run['conclusion'] != 'success':
                continue
            head_repository = run['head_repository']
            if head_repository is None:
                continue
            head = (run['head_sha'], run['head_branch'], head_repository['id'])
            matching_pr = run['event'] == 'pull_request' and head in merged_heads
            matching_dispatch = (run['event'] == 'workflow_dispatch' and run['head_branch'] == 'main'
                                 and run['head_sha'] == commit and head_repository['full_name'] == repository)
            if (matching_pr or matching_dispatch) and recorded_tree_matches(api, run_id, tree):
                return run['html_url']
    return None


def main():
    repository = os.environ['GITHUB_REPOSITORY']
    commit = os.environ['GITHUB_SHA']
    tree = subprocess.check_output(['git', 'rev-parse', f'{commit}^{{tree}}'], text=True).strip()

    def api(endpoint, *, paginate=False):
        command = ['gh', 'api', f'repos/{repository}/{endpoint}']
        if paginate:
            command.extend(['--paginate', '--slurp'])
        return json.loads(subprocess.check_output(command, text=True))

    run_url = find_verified_run(api, repository, commit, tree)
    if run_url is None:
        raise SystemExit(
            f'No successful CI record for main commit {commit} (tree {tree}). '
            'Run CI/CD on main with deploy_gcp_production unchecked, then retry this deployment '
            'while main still points to the same commit.'
        )
    print(f'Verified CI for main commit {commit} (tree {tree}): {run_url}')


if __name__ == '__main__':
    main()
