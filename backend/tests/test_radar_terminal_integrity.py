from datetime import date

import pytest

from ai_stock_sentinel.daily_radar.readiness import classify_window
from ai_stock_sentinel.calibration import official_corporate_actions as actions
from radar_calendar_support import calendar


def inputs():
    candidate = {'candidate_id': 1, 'symbol': '2330.TW', 'record_date': '2026-06-01',
                 'data_dates': {'ohlcv': '2026-06-01'}}
    cal = calendar(date(2026, 6, 1), date(2026, 6, 8))
    row = {'status': 'skipped', 'skip_reason': 'stock_distribution_in_window',
           'target_date': None, 'evaluation_as_of_date': '2026-06-08', 'benchmark_symbol': 'TAIEX',
           'outcome': {'return_basis': 'next_open', 'price_basis': 'unadjusted_price',
               'expected_target_date': '2026-06-08', actions.PROVENANCE_KEY: {
                   'evidence_version': actions.EVIDENCE_VERSION,
                   'event': {'symbol': '2330.TW', 'effective_date': '2026-06-04',
                             'type': 'stock_distribution', 'share_ratio': 1.1,
                             'previous_close': 100, 'cash_per_share': 0, 'halt_start': None},
                   'session_dates': ['2026-06-02', '2026-06-03', '2026-06-04', '2026-06-05', '2026-06-08'],
                   'sources': [{'source_url': 'https://www.twse.com.tw/rwd/zh/exRight/TWT48U?date=20260604',
                                'snapshot_sha256': 'a' * 64, 'captured_at': '2026-06-08T16:00:00+00:00'}],
                   'halt_dates': [], 'yahoo_adjusted_dates': ['2026-06-02', '2026-06-03']}}}
    return candidate, row, cal


def classify(candidate, row, cal):
    return classify_window(candidate, row, basis='next_open', window=5,
                           as_of_date=date(2026, 6, 8), calendar=cal)


@pytest.mark.parametrize('change', ['wrong_target', 'missing_basis', 'missing_proof', 'wrong_symbol',
    'outside_event', 'wrong_sessions', 'bad_source', 'unproven_halt', 'future_evaluation'])
def test_corporate_terminal_requires_reason_specific_evidence(change):
    c, row, cal = inputs()
    payload = row['outcome']; proof = payload[actions.PROVENANCE_KEY]
    if change == 'wrong_target': row['target_date'] = '2026-06-02'
    if change == 'missing_basis': payload.pop('price_basis')
    if change == 'missing_proof': payload.pop(actions.PROVENANCE_KEY)
    if change == 'wrong_symbol': proof['event']['symbol'] = '2317.TW'
    if change == 'outside_event': proof['event']['effective_date'] = '2026-06-09'
    if change == 'wrong_sessions': proof['session_dates'][0] = '2026-06-01'
    if change == 'bad_source': proof['sources'][0].pop('snapshot_sha256')
    if change == 'unproven_halt':
        row['skip_reason'] = 'cash_reduction_halt_in_window'
        proof['event']['type'] = 'cash_reduction'
    if change == 'future_evaluation': row['evaluation_as_of_date'] = '2026-06-09'
    state, reason, _ = classify(c, row, cal)
    assert state != 'terminal'
    assert reason in {'calendar_window_mismatch', 'research_basis_mismatch', 'terminal_evidence_unverified'}


def test_proven_distribution_and_cash_reduction_remain_terminal():
    c, row, cal = inputs()
    assert classify(c, row, cal)[0] == 'terminal'
    proof = row['outcome'][actions.PROVENANCE_KEY]
    row['skip_reason'] = 'cash_reduction_halt_in_window'
    proof['event'].update(type='cash_reduction', share_ratio=0.8, cash_per_share=2,
                          halt_start='2026-06-02')
    proof['halt_dates'] = ['2026-06-02', '2026-06-03']
    assert classify(c, row, cal)[0] == 'terminal'


@pytest.mark.parametrize('reason,observed', [('stale_candidate_price', '2026-05-29'),
    ('candidate_price_date_missing', None), ('future_candidate_price', '2026-06-02')])
def test_signal_level_terminal_is_checked_against_current_candidate(reason, observed):
    c, row, cal = inputs()
    c['data_dates']['ohlcv'] = observed
    row.update(skip_reason=reason, target_date=None, outcome={})
    assert classify(c, row, cal)[0] == 'terminal'
    c['data_dates']['ohlcv'] = '2026-06-01'
    assert classify(c, row, cal)[0] != 'terminal'


def test_split_terminal_requires_split_event_evidence():
    c, row, cal = inputs()
    row.update(skip_reason='stock_split_in_window', outcome={})
    assert classify(c, row, cal)[0] != 'terminal'


def test_split_producer_checks_full_window_before_accepting_terminal():
    from types import SimpleNamespace
    from test_daily_radar_research_validation import evaluate, price
    rows = [price('2026-06-05'), price('2026-06-08', stock_splits=2), price('2026-06-09')]
    row = evaluate(rows)
    c = {'symbol': '2330.TW', 'record_date': '2026-06-05', 'data_dates': {'ohlcv': '2026-06-05'}}
    cal = SimpleNamespace(through=date(2026, 6, 9), sessions=tuple(date(2026, 6, d) for d in (5, 8, 9)))
    assert classify_window(c, row, basis='next_open', window=2, as_of_date=cal.through, calendar=cal)[0] == 'terminal'
    rows[-1]['low'] = None
    assert evaluate(rows)['skip_reason'] == 'invalid_research_ohlc'


@pytest.mark.parametrize('bad', [[], 'invalid', 1, {'event': 'invalid'}, {'sources': 'invalid'}])
def test_malformed_terminal_proof_is_unresolved(bad):
    c, row, cal = inputs()
    row['outcome'][actions.PROVENANCE_KEY] = bad
    assert classify(c, row, cal)[0] != 'terminal'


@pytest.mark.parametrize('bad', [123, None, [], {}, True])
def test_non_string_source_url_is_unresolved(bad):
    c, row, cal = inputs()
    row['outcome'][actions.PROVENANCE_KEY]['sources'][0]['source_url'] = bad
    assert classify(c, row, cal)[0] != 'terminal'


def test_unhashable_split_symbol_is_unresolved():
    c, row, cal = inputs()
    row['skip_reason'] = 'stock_split_in_window'
    row['outcome'].update(session_dates=[str(d) for d in cal.sessions[1:]],
        split_event={'symbol': [], 'date': '2026-06-04', 'ratio': 2,
                     'price_source': 'yfinance', 'corporate_actions_checked': True})
    assert classify(c, row, cal)[0] != 'terminal'


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_signal_level_terminal_ignores_incidental_price_calendar_target(basis):
    c, row, cal = inputs()
    c['data_dates']['ohlcv'] = '2026-05-29'
    row.update(skip_reason='stale_candidate_price', target_date=None,
               outcome={'expected_target_date': '2026-06-09'})
    assert classify_window(c, row, basis=basis, window=5, as_of_date=cal.through, calendar=cal)[0] == 'terminal'
