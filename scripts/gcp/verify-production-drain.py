"""Verify Cloud Run admission/instance drain and the six writer workflow gates."""
import json
from pathlib import Path
import sys

WRITERS = {'daily-radar.yml', 'daily-radar-chip-context.yml', 'fundamental-data.yml',
           'active-etf-holdings.yml', 'analysis-forward-validation.yml', 'monthly-analysis-calibration.yml'}


def verify(service, workflows):
    annotations = service['metadata'].get('annotations', {})
    env = {x['name']: x.get('value') for x in service['spec']['template']['spec']['containers'][0]['env']}
    if (env.get('APP_MAINTENANCE_MODE') != 'true'
            or annotations.get('run.googleapis.com/scalingMode') != 'manual'
            or annotations.get('run.googleapis.com/manualInstanceCount') != '0'
            or any(x.get('tag') for x in service['status']['traffic'])):
        raise ValueError('Enter maintenance and disable all service instances/tags before migration')
    selected = [x for x in workflows['workflows'] if x['path'].removeprefix('.github/workflows/') in WRITERS]
    if (len(selected) != len(WRITERS)
            or {x['path'].removeprefix('.github/workflows/') for x in selected} != WRITERS
            or any(x['state'] not in ('disabled_manually', 'disabled_inactivity') for x in selected)):
        raise ValueError('Pause all six writer workflows before migration')


if __name__ == '__main__':
    verify(json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text()))
    print('Cloud Run and writer workflow drain gates verified; DB connections are rechecked inside the migration transaction.')
