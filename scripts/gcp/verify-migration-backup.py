"""Verify the actual backup bundle and restored table manifest before cloud writes."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify(backup, restored_manifest, manifest_sha, bundle_sha, from_revision):
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    require(all(re.fullmatch('[a-f0-9]{64}', value) for value in (manifest_sha, bundle_sha)), 'Pass trusted backup SHA256 values')
    require(digest(backup / 'manifest.jsonl') == manifest_sha, 'Backup manifest checksum differs')
    require(digest(backup / 'SHA256SUMS') == bundle_sha, 'Backup bundle checksum differs')
    required = {'database.sql.gz', 'manifest.jsonl', 'tables.txt', 'sequence-state.sql', 'started-at.txt'}
    checked = set()
    for line in (backup / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split(None, 1)
        name = name.strip()
        require(name in required and name not in checked, 'Unexpected backup checksum entry')
        require(digest(backup / name) == checksum, 'Backup file checksum differs: ' + name)
        checked.add(name)
    require(checked == required, 'Incomplete backup bundle')
    spec = importlib.util.spec_from_file_location('backup_compare', Path(__file__).with_name('compare-manifests.py'))
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    source = compare.read_manifest(backup / 'manifest.jsonl')
    target = compare.read_manifest(restored_manifest)
    require(not compare.compare(source, target), 'Restored table manifest differs from backup')
    require(set(source) == set((backup / 'tables.txt').read_text().splitlines()), 'Backup table list differs from manifest')
    version = source.get('public.alembic_version', {})
    row = json.dumps({'version_num': from_revision}, separators=(',', ':')).encode()
    expected_content = hashlib.md5(hashlib.md5(row).hexdigest().encode()).hexdigest()
    require(version.get('rows') == 1 and version.get('content_md5') == expected_content,
            'Backup does not contain the approved starting schema revision')
    started = datetime.fromisoformat((backup / 'started-at.txt').read_text().strip().replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    require(started.tzinfo is not None and now - timedelta(hours=6) <= started <= now,
            'Backup is stale or has an invalid timestamp; create a new drained backup')
    contract_path = Path(__file__).resolve().parents[2] / 'backend/src/ai_stock_sentinel/operations/preimage.py'
    spec = importlib.util.spec_from_file_location('backup_preimage', contract_path)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    states = contract.sequence_states((backup / 'sequence-state.sql').read_text())
    require({row[0] for row in states} == {row[0] for item in source.values() for row in item['sequences']},
            'Backup sequence inventory differs from manifest')
    return {'backup_manifest_sha256': manifest_sha, 'backup_bundle_sha256': bundle_sha,
            'from_revision': from_revision, 'table_count': len(source),
            'backup_preimage_sha256': contract.fingerprint(source, states),
            'approval_expires_at': (started + timedelta(hours=6)).isoformat(),
            'backup_tables_sha256': hashlib.sha256(json.dumps(sorted(source), separators=(',', ':')).encode()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--restored-manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--bundle-sha256', required=True)
    parser.add_argument('--from-revision', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.backup, args.restored_manifest, args.manifest_sha256,
                            args.bundle_sha256, args.from_revision)))
