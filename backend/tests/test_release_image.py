import io
import json
from zipfile import ZipFile

import pytest

from release_image import RECEIPT, RELEASE_JOB, SAVE_STEP, verified_digest


COMMIT = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64
NAME = 'verified-backend-image-' + COMMIT


@pytest.mark.parametrize(('metadata', 'payload', 'saved', 'expected'), [
    ([], None, None, ''),
    ([], None, 'success', None),
    ([{'id': 12, 'name': NAME, 'expired': False}], DIGEST, 'success', DIGEST),
    ([{'id': 12, 'name': NAME, 'expired': True}], DIGEST, 'success', None),
    ([{'id': 12, 'name': 'different-release', 'expired': False}], DIGEST, 'success', None),
    ([{'id': 12, 'name': NAME, 'expired': False}], 'latest', 'success', None),
])
def test_release_retry_uses_only_valid_receipt_for_this_run(metadata, payload, saved, expected):
    def api(path, *, paginate=False):
        if path == f'actions/runs/345/artifacts?name={NAME}':
            return json.dumps({'total_count': len(metadata), 'artifacts': metadata})
        if path == 'actions/runs/345/jobs?filter=all&per_page=100':
            assert paginate
            return json.dumps([{'jobs': [{'name': RELEASE_JOB, 'steps': [
                {'name': SAVE_STEP, 'conclusion': saved},
            ]}]}])
        assert path == 'actions/artifacts/12/zip' and payload is not None
        result = io.BytesIO()
        with ZipFile(result, 'w') as archive:
            archive.writestr(RECEIPT, payload + '\n')
        return result.getvalue()

    if expected is None:
        with pytest.raises(ValueError):
            verified_digest(api, '345', COMMIT)
    else:
        assert verified_digest(api, '345', COMMIT) == expected
