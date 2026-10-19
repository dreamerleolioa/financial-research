"""Plan by default. Operator-only access/maintenance transition, separate from CI."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROJECT = 'financial-research-20261019'
ACCOUNT = 'tonyliao1994@gmail.com'
SERVICE = 'financial-research-prod'
REGION = 'asia-east1'
SPEC = importlib.util.spec_from_file_location('service_verifier', Path(__file__).with_name('verify_production_service.py'))
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def cloud(*args, raw=False):
    result = subprocess.run(['gcloud', *args, '--project='+PROJECT, '--account='+ACCOUNT,
                             '--quiet', '--verbosity=error'] + ([] if raw else ['--format=json']),
                            text=True, capture_output=True, check=True, timeout=600,
                            env={**os.environ, 'CLOUDSDK_CORE_LOG_HTTP':'false'})
    return result.stdout.strip() if raw else json.loads(result.stdout or 'null')


def describe():
    data = cloud('run','services','describe',SERVICE,'--region='+REGION)
    policy = cloud('run','services','get-iam-policy',SERVICE,'--region='+REGION)
    return data, policy


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe_maintenance(url, token=None):
    headers = {'X-Serverless-Authorization':'Bearer '+token} if token else {}
    try:
        with build_opener(NoRedirect()).open(Request(url+'/auth/me',headers=headers),timeout=40) as response:
            status, body = response.status, response.read(4096)
    except HTTPError as error:
        status, body = error.code, error.read(4096)
    if status != 503 or json.loads(body) != {'detail':'Service temporarily unavailable for maintenance'}:
        raise ValueError('Image did not prove the maintenance admission gate')


def apply(args):
    if os.getenv('CONFIRM_PRODUCTION_ENTRY_CHANGE') != 'yes':
        raise ValueError('Explicit production entry approval required')
    if args.maintenance == 'false' and os.getenv('CONFIRM_PRODUCTION_CUTOVER_APPROVED') != 'yes':
        raise ValueError('Releasing maintenance is a separately approved formal cutover')
    if args.journal is None:
        raise ValueError('Pass a new private journal path')
    os.umask(0o077)
    with args.journal.open('x') as handle:
        json.dump({'status':'preflight'},handle)
    state = {'status':'preflight','completed':[]}
    def save():
        args.journal.write_text(json.dumps(state,indent=2)+'\n')
    def update(name, *options):
        state.update(status='running',current_action=name);save()
        cloud('run','services','update',SERVICE,'--region='+REGION,*options)
        state['completed'].append(name);state.pop('current_action',None);save()
    try:
        data, policy = describe()
        current = 'public' if data['metadata'].get('annotations',{}).get('run.googleapis.com/invoker-iam-disabled') == 'true' else 'private'
        url = verifier.verify(data,policy,args.expected_image,access_mode=current)
        # First gate all work while the existing IAM policy still protects the service.
        update('enter-maintenance','--update-env-vars=APP_MAINTENANCE_MODE=true')
        data, policy = describe()
        verifier.verify(data,policy,args.expected_image,access_mode=current)
        token = cloud('auth','print-identity-token',raw=True) if current == 'private' else None
        probe_maintenance(url,token)
        update('configure-access','--no-invoker-iam-check' if args.access == 'public' else '--invoker-iam-check')
        data, policy = describe()
        verifier.verify(data,policy,args.expected_image,access_mode=args.access)
        if args.access == 'public':
            probe_maintenance(url)
        if args.maintenance == 'false':
            update('release-maintenance','--update-env-vars=APP_MAINTENANCE_MODE=false')
        data, policy = describe()
        verifier.verify(data,policy,args.expected_image,access_mode=args.access)
        values = {e['name']:e.get('value') for e in data['spec']['template']['spec']['containers'][0]['env']}
        if values['APP_MAINTENANCE_MODE'] != args.maintenance:
            raise ValueError('Maintenance state differs')
        state['status']='verified';save()
        print('PRODUCTION_ENTRY_VERIFIED access='+args.access+' maintenance='+args.maintenance)
    except BaseException as error:
        state.update(status='stopped_no_automatic_retry',error_type=type(error).__name__);save()
        raise


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--access',choices=('private','public'),required=True)
    p.add_argument('--maintenance',choices=('true','false'),default='true')
    p.add_argument('--expected-image',required=True)
    p.add_argument('--journal',type=Path)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args(argv)
    if not verifier.re.fullmatch(verifier.IMAGE_PATTERN,args.expected_image):
        raise ValueError('Pass a reviewed digest in the production image repository')
    if args.apply:
        apply(args)
    else:
        print(json.dumps({'mode':'plan_only','service':SERVICE,'access':args.access,
                          'maintenance':args.maintenance,'image':args.expected_image,
                          'frontend_or_schedule_changes':False}))


if __name__ == '__main__':
    main()
