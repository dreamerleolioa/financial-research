"""Bind derived returns to the candidate actually used during evaluation."""
from collections.abc import Mapping

from ai_stock_sentinel.calibration.forward_validation import candidate_key
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

PROOF_KEY = 'validation_input'
VERSION = 'radar-validation-input-v1'
UNVERIFIED = 'validation_input_unverified'


def validation_candidate_revision(candidate):
    values = {k: v for k, v in candidate.items() if k != 'name'}
    # Evaluation may receive the full snapshot or its SQL projection. Exclude
    # unrelated replay/history blobs exactly as the projection does.
    def mapping(value):
        return value if isinstance(value, Mapping) else {}
    if 'input_snapshot' in values:
        snapshot = mapping(values['input_snapshot'])
        replay_context = mapping(mapping(snapshot.get('replay_input')).get('market_context'))
        values['input_snapshot'] = {
            'ohlcv': {'close': mapping(snapshot.get('ohlcv')).get('close')},
            'indicators': {k: mapping(snapshot.get('indicators')).get(k)
                for k in ('support_level', 'resistance_level', 'ma20', 'ma60')},
            'versions': snapshot.get('versions') or {}, 'selection_version': snapshot.get('selection_version'),
            'market_context': {'regime': mapping(snapshot.get('market_context')).get('regime')},
            'replay_input': {'market_context': {'benchmark': {
                'symbol': mapping(replay_context.get('benchmark')).get('symbol')}}},
        }
    if 'score_breakdown' in values:
        scores = mapping(values['score_breakdown'])
        market_details = mapping(mapping(scores.get('market_context')).get('details'))
        values['score_breakdown'] = {
            'relative_strength': {k: mapping(scores.get('relative_strength')).get(k)
                for k in ('freshness', 'relative_value')},
            'market_context': {'details': {'regime': market_details.get('regime')}},
        }
    if candidate.get('selection_status') == 'shadow':
        values.pop('observation_origin', None)
    return fingerprint(values)


def bind_validation_input(candidate, row):
    return dict(row) | {'outcome': dict(row.get('outcome') or {}) | {PROOF_KEY: {
        'version': VERSION, 'candidate_revision': validation_candidate_revision(candidate)}}}


def bind_validation_inputs(candidates, rows):
    by_key = {candidate_key(c): c for c in candidates}
    return [bind_validation_input(by_key[candidate_key(dict(row) | {'record_date': row.get('signal_date')})], row)
            for row in rows]


def validation_inputs_match(candidate, row):
    payload = row.get('outcome') or {}
    if not isinstance(payload, Mapping):
        return False
    proof = payload.get(PROOF_KEY)
    if proof is None:
        # Legacy terminal skips have no return to certify. Their current signal
        # date/event/window evidence is checked by terminal_integrity separately.
        return row.get('status') != 'validated' and row.get('skip_reason') != UNVERIFIED
    return (isinstance(proof, Mapping) and proof.get('version') == VERSION
            and proof.get('candidate_revision') == validation_candidate_revision(candidate))


def checked_validation_inputs(candidates, rows):
    by_key = {candidate_key(c): c for c in candidates}
    checked = []
    for row in rows:
        key = candidate_key(dict(row) | {'record_date': row.get('signal_date')})
        if key not in by_key or not validation_inputs_match(by_key[key], row):
            row = dict(row) | {'status': 'skipped', 'skip_reason': UNVERIFIED}
        checked.append(row)
    return checked
