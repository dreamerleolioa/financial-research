"""Reason-specific contracts for persisted terminal validation outcomes."""
from datetime import datetime
import re
from urllib.parse import urlsplit

from ai_stock_sentinel.calibration.forward_validation import number, parse_date
from ai_stock_sentinel.calibration import official_corporate_actions as actions
from ai_stock_sentinel.daily_radar.forward_validation import _proven_termination


def _source(proof):
    if (not isinstance(proof, dict) or not isinstance(proof.get('source_url'), str)
            or not isinstance(proof.get('captured_at'), str)):
        return False
    try:
        url = urlsplit(proof.get('source_url', ''))
        captured = datetime.fromisoformat(proof['captured_at'])
        return (url.scheme == 'https' and url.hostname in {'www.twse.com.tw', 'www.tpex.org.tw'}
                and captured.tzinfo is not None
                and re.fullmatch(r'[0-9a-f]{64}', str(proof.get('snapshot_sha256', ''))) is not None)
    except (ValueError, TypeError, KeyError):
        return False


def terminal_evidence_matches(candidate, row, *, basis, sessions, as_of_date):
    """Missing proof is unresolved; signal-level reasons need no future prices."""
    signal, target = parse_date(candidate.get('record_date')), sessions[-1]
    payload = row.get('outcome') or {}
    if not isinstance(payload, dict):
        return False
    evaluation = parse_date(row.get('evaluation_as_of_date'))
    if evaluation is None or evaluation > as_of_date or evaluation < signal:
        return False
    reason = row['skip_reason']
    observed = parse_date((candidate.get('data_dates') or {}).get('ohlcv'))
    if reason == 'stale_candidate_price':
        return observed is not None and observed < signal
    if reason == 'candidate_price_date_missing':
        return basis == 'next_open' and observed is None
    if reason == 'future_candidate_price':
        return basis == 'next_open' and observed is not None and observed > signal
    if row.get('target_date') is not None and parse_date(row['target_date']) != target:
        return False
    if payload.get('expected_target_date') is not None and parse_date(payload['expected_target_date']) != target:
        return False
    if reason == 'trading_terminated_in_window':
        return parse_date(row.get('target_date')) == target and _proven_termination(
            symbol=candidate['symbol'], signal_date=signal, target_date=target,
            evaluation_date=evaluation, payload=payload)
    if (basis != 'next_open' or payload.get('return_basis') != 'next_open'
            or payload.get('price_basis') != 'unadjusted_price' or evaluation < target
            or parse_date(payload.get('expected_target_date')) != target):
        return False
    days = [str(d) for d in sessions]
    if reason == 'stock_split_in_window':
        event = payload.get('split_event') or {}
        if not isinstance(event, dict):
            return False
        ratio = number(event.get('ratio'))
        return (payload.get('session_dates') == days and event.get('date') in days
                and isinstance(event.get('symbol'), str)
                and event['symbol'] in (candidate['symbol'], row.get('benchmark_symbol'))
                and ratio is not None and ratio > 0 and ratio != 1
                and isinstance(event.get('price_source'), str) and bool(event['price_source'])
                and event.get('corporate_actions_checked') is True)
    if reason not in actions.TERMINAL_REASONS:
        return False
    proof = payload.get(actions.PROVENANCE_KEY) or {}
    if not isinstance(proof, dict):
        return False
    event = proof.get('event') or {}
    if not isinstance(event, dict):
        return False
    effective = parse_date(event.get('effective_date'))
    ratio, previous, cash = (number(event.get(k)) for k in ('share_ratio', 'previous_close', 'cash_per_share'))
    sources = proof.get('sources')
    adjusted = proof.get('yahoo_adjusted_dates')
    if (proof.get('evidence_version') != actions.EVIDENCE_VERSION or proof.get('session_dates') != days
            or event.get('symbol') != candidate['symbol'] or effective not in sessions
            or previous is None or previous <= 0 or ratio is None or cash is None or cash < 0
            or not isinstance(sources, list) or not sources or not all(_source(s) for s in sources)
            or not isinstance(adjusted, list) or any(d not in days or d >= str(effective) for d in adjusted)):
        return False
    if reason == actions.STOCK_DISTRIBUTION:
        return (event.get('type') == 'stock_distribution' and ratio > 1 and bool(adjusted)
                and event.get('halt_start') is None and proof.get('halt_dates') == [])
    halt = parse_date(event.get('halt_start'))
    return (event.get('type') == 'cash_reduction' and 0 < ratio < 1 and cash > 0
            and halt in sessions and halt < effective
            and proof.get('halt_dates') == [str(d) for d in sessions if halt <= d < effective])
