"""Per-window calendar eligibility and independent calendar selection oracle."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlalchemy import event, select

from ai_stock_sentinel.daily_radar import trading_calendar as tc, history_repair as repair
from ai_stock_sentinel.daily_radar.forward_validation import (
    build_forward_validation_report, forward_validation_candidates_from_runs, upsert_forward_validation_results,
)
from ai_stock_sentinel.daily_radar.research_validation import evaluate_research_window
from ai_stock_sentinel.daily_radar.validation_read import read_observation_validation
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from test_daily_radar_validation_read import add_run, add_candidate
from test_radar_public_window_eligibility import seed_basis, selected
from test_next_open_history_repair import db, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from radar_calendar_support import calendar

WINDOWS = [5, 10, 20, 40, 60]
OLD = date(2026, 5, 1)


def read_body(db):
    statements = []
    def observe(conn, cursor, sql, params, context, many): statements.append(sql)
    event.listen(db.get_bind(), 'before_cursor_execute', observe)
    try:
        body = read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365).model_dump(mode='json')
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', observe)
    assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for s in statements)
    assert sum(s.lstrip().upper().startswith('SELECT') and 'daily_radar_prepared_runs.step_statuses' in s.lower() for s in statements) == 1
    return body


def cohort_body(body, version):
    return body | {'cohorts': [next(c for c in body['cohorts'] if c['strategy']['selection_version'] == version)]}


def partial_start(db, basis):
    seed_basis(db, basis)
    cal = calendar(SIGNAL, DAY)
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {tc.CALENDAR_STEP: cal.payload()}
    db.commit()
    return cal


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('old_status', ['selected', 'shadow'])
def test_uncovered_older_candidate_cannot_change_covered_cohort_performance(db, basis, old_status):
    partial_start(db, basis)
    before = read_body(db)
    old = add_candidate(db, add_run(db, OLD), symbol='2317.TW', selection='uncovered', status=old_status)
    if old_status == 'shadow': old.shadow_cohort = 'comparable'
    db.commit()
    original = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    after = read_body(db)
    for window in WINDOWS:
        assert selected(cohort_body(after, 'v1'), basis, window) == selected(cohort_body(before, 'v1'), basis, window)
        assert selected(cohort_body(after, 'v1'), basis, window)['evaluated_count'] == 1
    assert after['calendar_through_date'] == str(DAY)
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == original


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_multiple_proofs_keep_both_covered_cohorts_eligible(db, basis):
    partial_start(db, basis)
    old = add_candidate(db, add_run(db, OLD), symbol='2317.TW', selection='older')
    old.data_dates = {'ohlcv': str(OLD)}
    old.input_snapshot = old.input_snapshot | {'ohlcv': {'close': 100}}
    db.commit()
    candidate = next(c for c in forward_validation_candidates_from_runs(db, market='TW', end_date=DAY) if c['candidate_id'] == old.id)
    cal = calendar(OLD, DAY)
    prices = Prices(cal); prices.days = list(cal.sessions)
    bars = prices.fetch([old.symbol, 'TAIEX'], start_date=OLD, end_date=DAY)
    if basis == 'next_open':
        outcomes = [evaluate_research_window(candidate, price_series=bars[old.symbol], benchmark_prices=bars['TAIEX'],
            window_days=w, as_of_date=DAY, benchmark_symbol='TAIEX', trading_calendar=cal.sessions) for w in WINDOWS]
    else:
        outcomes = build_forward_validation_report([candidate], price_series_by_symbol=bars, benchmark_prices=bars['TAIEX'],
            market='TW', sample_source='test', as_of_date=DAY, windows=WINDOWS, trading_calendar=cal.sessions).outcomes
    assert all(r['status'] == 'validated' for r in outcomes)
    upsert_forward_validation_results(db, outcomes)
    cutoff = date(2026, 8, 1)
    db.add(DailyRadarPreparedRun(run_date=cutoff, market='TW', symbol_count=0, selected_symbols=[], universe=[],
        step_statuses={tc.CALENDAR_STEP: calendar(OLD, cutoff).payload()}))
    db.commit()
    body = read_body(db)
    for version in ('v1', 'older'):
        for window in WINDOWS:
            stats = selected(cohort_body(body, version), basis, window)
            assert stats['evaluated_count'] == 1 and stats['coverage_complete'] is True
    assert body['calendar_invalid_windows'] == []


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_disjoint_proofs_cannot_be_spliced_into_a_complete_window(db, basis):
    seed_basis(db, basis)
    split = date(2026, 6, 15)
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {tc.CALENDAR_STEP: calendar(split+timedelta(days=1), DAY).payload()}
    db.add(DailyRadarPreparedRun(run_date=split, market='TW', symbol_count=0, selected_symbols=[], universe=[],
        step_statuses={tc.CALENDAR_STEP: calendar(SIGNAL, split).payload()}))
    db.commit()
    body = read_body(db)
    for window in WINDOWS:
        stats = selected(body, basis, window)
        assert stats['evaluated_count'] == int(window <= 10)
        assert stats['coverage_complete'] is (window <= 10)


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('current', [False, True])
def test_maturity_requires_current_proof_and_is_independent_of_uncovered_old_cohorts(db, basis, current):
    partial_start(db, basis)
    if not current:
        db.scalar(select(DailyRadarPreparedRun)).step_statuses = {tc.CALENDAR_STEP: calendar(SIGNAL, DAY-timedelta(days=1)).payload()}
    add_candidate(db, add_run(db, OLD), symbol='2317.TW', selection='uncovered')
    add_candidate(db, add_run(db, DAY), symbol='2303.TW', selection='latest')
    db.commit()
    body = cohort_body(read_body(db), 'latest')
    for window in WINDOWS:
        stats = selected(body, basis, window)
        assert stats['evaluated_count'] == 0
        assert stats['immature_count'] == int(current)
        assert stats['missing_outcome_count'] == int(not current)


@pytest.mark.parametrize('saved_state', ['validated', 'missing_diagnostic', 'skipped', 'absent'])
def test_narrow_current_proof_cannot_hide_saved_diagnostics_or_missing_mature_results(db, saved_state):
    cal = seed_basis(db, 'signal_close')
    candidate_id = db.scalar(select(Result.candidate_id))
    row = db.get(DailyRadarCandidate, candidate_id)
    row.input_snapshot = row.input_snapshot | {'indicators': {'support_level': 95, 'resistance_level': 101}}
    db.commit()
    candidate = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    bars = Prices(cal).fetch([candidate['symbol'], 'TAIEX'], start_date=SIGNAL, end_date=DAY)
    close = build_forward_validation_report([candidate], price_series_by_symbol=bars, benchmark_prices=bars['TAIEX'],
        market='TW', sample_source='test', as_of_date=DAY, windows=WINDOWS, trading_calendar=cal.sessions).outcomes
    assert all(r['outcome']['observation_diagnostic']['status'] == 'confirmed' for r in close)
    research = [evaluate_research_window(candidate, price_series=bars[candidate['symbol']], benchmark_prices=bars['TAIEX'],
        window_days=w, as_of_date=DAY, benchmark_symbol='TAIEX', trading_calendar=cal.sessions) for w in WINDOWS]
    upsert_forward_validation_results(db, close+research)
    from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
    for result in db.scalars(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION)):
        if saved_state == 'missing_diagnostic':
            result.outcome = {k: v for k, v in result.outcome.items() if k != 'observation_diagnostic'}
        elif saved_state == 'skipped':
            result.status, result.skip_reason = 'skipped', 'missing_future_price'
        elif saved_state == 'absent': db.delete(result)
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {tc.CALENDAR_STEP: calendar(DAY-timedelta(days=3), DAY).payload()}
    through = DAY-timedelta(days=1)
    db.add(DailyRadarPreparedRun(run_date=through, market='TW', symbol_count=0, selected_symbols=[], universe=[],
        step_statuses={tc.CALENDAR_STEP: calendar(SIGNAL, through).payload()}))
    db.commit()
    original = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    body = read_body(db)
    for window in WINDOWS:
        summary = body['cohorts'][0]['windows'][str(window)]['all_selected']
        assert summary['immature_observation_count'] == 0
        assert summary['evaluated_observation_count'] == int(saved_state == 'validated')
        assert summary['missing_diagnostic_count'] == int(saved_state == 'missing_diagnostic')
        assert summary['skipped_validation_count'] == int(saved_state == 'skipped')
        assert summary['missing_outcome_count'] == int(saved_state == 'absent')
        assert summary['coverage_complete'] is (saved_state == 'validated')
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == original


@pytest.fixture(scope='module')
def proof_cases():
    return [calendar(date(2026,5,25),date(2026,6,5)), calendar(date(2026,6,1),date(2026,6,12)),
        calendar(date(2026,5,28),date(2026,6,10),{date(2026,6,2)}), calendar(date(2026,5,1),date(2026,5,8)),
        calendar(date(2026,6,8),date(2026,6,12)), calendar(date(2026,5,1),date(2026,5,8),{date(2026,5,4)})]


@pytest.mark.parametrize('mask', range(64))
def test_calendar_index_matches_pairwise_conflict_oracle_for_all_evidence_subsets(proof_cases, mask):
    evidence = [p for i,p in enumerate(proof_cases) if mask & (1 << i)]
    index = tc.CalendarEvidenceIndex(evidence)
    for offset in range(44):
        signal = date(2026,5,1)+timedelta(days=offset)
        covering = [c for c in evidence if c.start <= signal <= c.through]
        expected = max(covering, key=lambda c:c.through) if covering else None
        if expected and any(tc.calendars_conflict(expected, other) for other in evidence): expected = None
        assert index.for_signal(signal) is expected, (mask, signal)


def test_latest_calendar_prefers_complete_broad_proof_on_equal_cutoff():
    broad, narrow = calendar(SIGNAL, DAY), calendar(SIGNAL+timedelta(days=30), DAY)
    assert tc.CalendarEvidenceIndex([narrow, broad]).latest() is broad


def test_calendar_index_uses_official_sessions_including_weekend_openings():
    normal = calendar(date(2026,6,1),date(2026,6,12))
    payload = deepcopy(normal.payload())
    saturday = '2026-06-06'
    payload['sessions'] = sorted(payload['sessions']+[saturday])
    payload['reports'][0]['session_dates'] = sorted(payload['reports'][0]['session_dates']+[saturday])
    special = tc.TradingCalendar.parse(payload)
    assert tc.CalendarEvidenceIndex([special]).for_signal(date.fromisoformat(saturday)) is special
    index = tc.CalendarEvidenceIndex([normal,special])
    assert index.for_signal(date(2026,6,1)) is None


@pytest.mark.parametrize('size', [1, 1000])
def test_same_proof_normalization_does_not_grow_with_outcome_count(monkeypatch, size):
    from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
    from ai_stock_sentinel.daily_radar.window_identity import identity
    cal = calendar(SIGNAL, DAY)
    days = [d for d in cal.sessions if d > SIGNAL][:5]
    template = {'signal_date': str(SIGNAL), 'window_days': 5, 'target_date': str(days[-1]),
        'status': 'validated', 'validation_version': FORWARD_VALIDATION_VERSION,
        'outcome': {'validation_window': identity(SIGNAL, days, 'signal_close')}}
    rows = [template | {'candidate_id': i} for i in range(size)]
    original, calls = tc.check_outcome_calendar, []
    def count(rows, sessions, **kwargs):
        calls.append(len(rows))
        return original(rows, sessions, **kwargs)
    monkeypatch.setattr(tc, 'check_outcome_calendar', count)
    checked, gaps = tc.check_outcome_calendars(rows, tc.CalendarEvidenceIndex([cal]))
    assert checked == rows and not gaps
    assert calls == [size]


def test_batched_projection_preserves_rows_gaps_duplicates_and_order(monkeypatch):
    from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
    from ai_stock_sentinel.daily_radar.window_identity import identity
    early, late = calendar(OLD, date(2026,5,15)), calendar(SIGNAL, DAY)
    full = calendar(OLD, DAY)
    def row(signal, cid):
        days = [d for d in full.sessions if d > signal][:5]
        return {'candidate_id': cid, 'signal_date': str(signal), 'window_days': 5, 'target_date': str(days[-1]),
            'status': 'validated', 'validation_version': FORWARD_VALIDATION_VERSION,
            'outcome': {'validation_window': identity(signal, days, 'signal_close')}}
    unknown = row(date(2026,5,20), 2)
    bad = row(date(2026,5,4), 5); bad['outcome']['validation_window']['sessions_sha256'] = 'f'*64
    rows = [row(SIGNAL,1), unknown, row(SIGNAL,3) | {'status':'skipped','skip_reason':'provider_failed'},
        row(OLD,4), bad, row(SIGNAL,6), deepcopy(unknown)]
    before = deepcopy(rows); index = tc.CalendarEvidenceIndex([late,early])
    expected_rows, expected_gaps = [], []
    for item in rows:
        cal = index.for_signal(date.fromisoformat(item['signal_date']))
        checked, gaps = tc.check_outcome_calendar([item], cal.sessions if cal else None,
            covered_through=cal.through if cal else None)
        expected_rows.extend(checked); expected_gaps.extend(gaps)
    original, calls = tc.check_outcome_calendar, []
    def count(items, sessions, **kwargs):
        calls.append(len(items))
        return original(items, sessions, **kwargs)
    monkeypatch.setattr(tc, 'check_outcome_calendar', count)
    checked, gaps = tc.check_outcome_calendars(rows, index)
    assert checked == expected_rows and gaps == expected_gaps and rows == before
    assert len(calls) == 3
