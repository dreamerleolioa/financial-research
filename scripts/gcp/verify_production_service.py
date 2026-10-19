"""Fail closed on production target, private ingress, credentials and schema mode."""
import json
from pathlib import Path
import re

PROJECT = 'financial-research-20261019'
SERVICE = 'financial-research-prod'
IMAGE_PATTERN = r'asia-east1-docker\.pkg\.dev/financial-research-20261019/financial-research/backend@sha256:[a-f0-9]{64}'
SECRET_SUFFIXES = {
    'DATABASE_URL': 'database-url', 'JWT_SECRET': 'jwt',
    'GOOGLE_CLIENT_ID': 'google-client-id', 'GOOGLE_CLIENT_SECRET': 'google-client-secret',
    'DAILY_RADAR_INTERNAL_TOKEN': 'internal-token', 'FINMIND_API_TOKEN': 'finmind-token',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(service, policy, expected_image=None, *, access_mode='private'):
    require(service['metadata']['name'] == SERVICE, 'Wrong production service')
    require(access_mode in ('private', 'public'), 'Unknown production access mode')
    require(service['metadata'].get('annotations', {}).get(
        'run.googleapis.com/invoker-iam-disabled', 'false') ==
        ('true' if access_mode == 'public' else 'false'), 'Unexpected Invoker IAM mode')
    require(all(not {'allUsers', 'allAuthenticatedUsers'}.intersection(binding.get('members', []))
                for binding in policy.get('bindings', [])), 'Unexpected broad IAM binding')
    template = service['spec']['template']
    require(template['spec']['serviceAccountName'] == f'financial-prod-api@{PROJECT}.iam.gserviceaccount.com', 'Wrong runtime identity')
    require(template['metadata']['annotations']['run.googleapis.com/cloudsql-instances'] ==
            f'{PROJECT}:asia-east1:financial-postgres', 'Wrong SQL instance')
    containers = template['spec']['containers']
    require(len(containers) == 1, 'Unexpected sidecars')
    container = containers[0]
    require(re.fullmatch(IMAGE_PATTERN, container['image']) is not None, 'Image must be pinned in this project')
    if expected_image is not None:
        require(container['image'] == expected_image, 'Serving image differs from verified artifact')
    entries = container['env']
    env = {entry['name']: entry for entry in entries}
    require(len(env) == len(entries), 'Duplicate environment entries')
    maintenance = env.get('APP_MAINTENANCE_MODE', {}).get('value')
    require(maintenance in ('true', 'false') or (access_mode == 'private' and maintenance is None),
            'Explicit maintenance state is required for public access')
    for name, value in {'EXPECTED_DATABASE_NAME': 'financial_research', 'DATABASE_MIGRATION_MODE': 'verify',
                        'DB_POOL_SIZE': '5', 'DB_MAX_OVERFLOW': '0'}.items():
        require(env[name].get('value') == value, f'Unsafe production configuration: {name}')
    for name, suffix in SECRET_SUFFIXES.items():
        require('value' not in env[name], f'Plaintext secret: {name}')
        require(env[name]['valueFrom']['secretKeyRef'] ==
                {'name': 'financial-prod-' + suffix, 'key': '1'}, f'Wrong secret/version: {name}')
    status = service['status']
    ready = status['latestReadyRevisionName']
    require(status['latestCreatedRevisionName'] == ready, 'New revision is not ready')
    require(sum(traffic.get('percent', 0) for traffic in status['traffic']
                if traffic.get('revisionName') == ready) == 100, 'Traffic must serve the ready revision')
    url = status['url']
    require(re.fullmatch(r'https://financial-research-prod-(?:[a-z0-9]+-de\.a|918645310958\.asia-east1)\.run\.app', url) is not None,
            'Unexpected service URL')
    return url


def env_updates():
    # JSON is also valid YAML. Keep the gcloud baseline readable by the stdlib
    # verifier without adding a runtime dependency or a second source of values.
    values = json.loads(Path(__file__).with_name('production-env.yaml').read_text())
    require('APP_MAINTENANCE_MODE' not in values, 'Maintenance state is operator managed')
    require(not set(values).intersection(SECRET_SUFFIXES), 'Secrets must use pinned Secret Manager references')
    require(all(re.fullmatch('[A-Z_]+', key) and isinstance(value, str)
                and '|' not in value and '\n' not in value for key, value in values.items()),
            'Invalid deployment environment value')
    # A non-destructive delta deliberately never contains the maintenance key.
    # gcloud merges it with its fresh service read, preserving resourceVersion
    # conflict detection if an operator updates the service concurrently.
    return '^|^' + '|'.join(key+'='+value for key, value in values.items())


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('service', type=Path)
    parser.add_argument('policy', type=Path)
    parser.add_argument('image', nargs='?')
    parser.add_argument('--access-mode', choices=('private', 'public'), default='private')
    parser.add_argument('--write-env-updates', type=Path)
    args = parser.parse_args()
    service = json.loads(args.service.read_text())
    url = verify(service, json.loads(args.policy.read_text()), args.image, access_mode=args.access_mode)
    if args.write_env_updates:
        args.write_env_updates.write_text(env_updates())
    print(url)
