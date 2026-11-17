"""Recover the verified image for this commit and workflow run on a retry."""
import io
import json
import os
import re
import subprocess
from zipfile import ZipFile


RECEIPT = 'verified-backend-image.txt'
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
RELEASE_JOB = 'Deploy GCP production backend'
SAVE_STEP = 'Save verified image for deployment retries'


def verified_digest(api, run_id, commit):
    name = f'verified-backend-image-{commit}'
    result = json.loads(api(f'actions/runs/{run_id}/artifacts?name={name}'))
    artifacts = result['artifacts']
    if result['total_count'] == 0 and not artifacts:
        # A build can be retried before it recorded an image. After an upload
        # succeeded, rebuilding would replace the digest a migration may have used.
        jobs = json.loads(api(f'actions/runs/{run_id}/jobs?filter=all&per_page=100', paginate=True))
        if any(
            step['name'] == SAVE_STEP and step['conclusion'] == 'success'
            for page in jobs for job in page['jobs'] if job['name'] == RELEASE_JOB
            for step in job.get('steps', [])
        ):
            raise ValueError('Previously verified release image receipt is missing')
        return ''
    if (result['total_count'] != 1 or len(artifacts) != 1
            or artifacts[0]['name'] != name or artifacts[0]['expired']):
        raise ValueError('Verified release image receipt is ambiguous or unavailable')
    artifact_id = artifacts[0]['id']
    if type(artifact_id) is not int or artifact_id <= 0:
        raise ValueError('Invalid release artifact ID')
    with ZipFile(io.BytesIO(api(f'actions/artifacts/{artifact_id}/zip'))) as archive:
        if archive.namelist() != [RECEIPT] or archive.getinfo(RECEIPT).file_size > 72:
            raise ValueError('Invalid verified release image artifact')
        digest = archive.read(RECEIPT).decode('ascii').strip()
    if not DIGEST.fullmatch(digest):
        raise ValueError('Invalid verified release image digest')
    return digest


def main():
    def api(path, *, paginate=False):
        command = ['gh', 'api', f'repos/{os.environ["GITHUB_REPOSITORY"]}/{path}']
        if paginate:
            command.extend(['--paginate', '--slurp'])
        return subprocess.check_output(command)

    digest = verified_digest(api, os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_SHA'])
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write(f'digest={digest}\n')


if __name__ == '__main__':
    main()
