"""Small identity of the actual calculation window, independent of its cutoff."""
from datetime import date
from collections.abc import Mapping

from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

KEY = 'validation_window'
VERSION = 'radar-validation-window-v1'


def identity(signal, sessions, basis):
    days = [str(d) for d in sessions]
    if not days or days != sorted(set(days)) or any(date.fromisoformat(d) <= date.fromisoformat(str(signal)) for d in days):
        raise ValueError('validation_window_dates_invalid')
    return {'version': VERSION, 'signal_date': str(signal), 'return_basis': basis,
            'window_days': len(days), 'entry_date': days[0], 'target_date': days[-1],
            'sessions_sha256': fingerprint(days)}


def bind_window(row, sessions, basis):
    if row['status'] != 'validated':
        return row
    signal = row['signal_date']
    days = sorted({str(d) for d in sessions if str(signal) < str(d) <= str(row['target_date'])})[:row['window_days']]
    if len(days) != row['window_days'] or days[-1] != str(row['target_date']):
        raise ValueError('validation_window_dates_invalid')
    return dict(row) | {'outcome': dict(row['outcome']) | {KEY: identity(signal, days, basis)}}


def window_error(row, *, signal, sessions, basis):
    payload = row.get('outcome') or {}
    expected = identity(signal, sessions, basis)
    if basis == 'next_open' and payload.get('entry_date') not in (None, expected['entry_date']):
        return 'calendar_window_mismatch'
    if KEY in payload:
        return None if payload[KEY] == expected else 'calendar_window_mismatch'
    # Legacy repairs already retained complete session dates. Do not infer them
    # from target dates or sparse prices. SQL readers project only this small list.
    evidence = []
    if row.get('window_session_dates') is not None:
        evidence.append(row['window_session_dates'])
    for key in ('reviewed_official_window', 'official_rights_recovery', 'official_corporate_action'):
        proof = payload.get(key)
        if isinstance(proof, Mapping) and proof.get('session_dates') is not None:
            evidence.append(proof['session_dates'])
    if not evidence:
        return 'calendar_window_unverified'
    return None if all(dates == [str(d) for d in sessions] for dates in evidence) else 'calendar_window_mismatch'
