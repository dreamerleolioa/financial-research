"""Versioned, standard-library-only contract shared by the backend and Actions."""
import hashlib
import json


COMPARISON_VERSION = 'radar-gap-comparison-v1'
GAP_FIELDS = ('candidate_id', 'candidate_revision', 'calendar_revision', 'return_basis',
              'validation_version', 'window_days', 'expected_target_date', 'state', 'reason', 'gap_revision')


def validation_gap_key(gap):
    return tuple(gap[field] for field in GAP_FIELDS)


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def baseline_digest(as_of_date, hashes):
    return digest([COMPARISON_VERSION, as_of_date, sorted(hashes)])
