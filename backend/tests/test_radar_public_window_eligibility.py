from datetime import timedelta

import pytest
from sqlalchemy import event, select

from ai_stock_sentinel.daily_radar import history_repair as repair
from ai_stock_sentinel.daily_radar.forward_validation import (
    build_forward_validation_report, forward_validation_candidates_from_runs,
    upsert_forward_validation_results,
)
from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar
from ai_stock_sentinel.daily_radar.validation_read import read_observation_validation
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from test_radar_calendar_progress import completed
from test_next_open_history_repair import db, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from test_daily_radar_validation_read import add_run, add_candidate
from radar_calendar_support import calendar


def seed_basis(db, basis):
    cal = completed(db)
    if basis == 'signal_close':
        candidate = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
        prices = Prices(cal).fetch([candidate['symbol'], 'TAIEX'], start_date=SIGNAL, end_date=DAY)
        report = build_forward_validation_report([candidate], price_series_by_symbol=prices,
            benchmark_prices=prices['TAIEX'], market='TW', sample_source='test', as_of_date=DAY,
            windows=[5, 10, 20, 40, 60], trading_calendar=cal.sessions)
        assert len(report.outcomes) == 5 and all(r['status'] == 'validated' for r in report.outcomes)
        upsert_forward_validation_results(db, report.outcomes)
        db.commit()
    return cal


def selected(body, basis, window):
    cohort = body['cohorts'][0]
    if basis == 'next_open':
        return cohort['research_pool_comparison'][str(window)]['cost_scenarios']['0']['selected']
    return cohort['pool_comparison'][str(window)]['selected']


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('proof', ['current', 'legacy_verified', 'legacy_unverified'])
@pytest.mark.parametrize('coverage', ['current', 'older_complete', 'absent', 'conflicting'])
def test_public_returns_require_independent_calendar_and_full_window_proof(db, basis, proof, coverage):
    cal = seed_basis(db, basis)
    for row in db.scalars(select(Result)):
        if proof != 'current':
            payload = {k: v for k, v in row.outcome.items() if k != 'validation_window'}
            if proof == 'legacy_verified':
                payload['reviewed_official_window'] = {'session_dates': [str(d) for d in cal.sessions if SIGNAL < d <= row.target_date]}
            row.outcome = payload
    prepared = db.scalar(select(DailyRadarPreparedRun))
    if coverage == 'absent': prepared.step_statuses = {}
    elif coverage == 'older_complete':
        prepared.step_statuses = {'validation-calendar': calendar(cal.start, DAY-timedelta(days=1)).payload()}
    elif coverage == 'conflicting':
        db.add(DailyRadarPreparedRun(run_date=DAY-timedelta(days=1), market='TW', symbol_count=0,
            selected_symbols=[], universe=[], step_statuses={'validation-calendar':
                calendar(cal.start, DAY-timedelta(days=1), {SIGNAL+timedelta(days=1)}).payload()}))
    db.commit()
    if coverage in ('absent', 'conflicting'):
        assert load_calendar(db, start_date=SIGNAL, as_of_date=DAY) is None
    before = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    steps = {r.id: repair.fingerprint(r.step_statuses) for r in db.scalars(select(DailyRadarPreparedRun))}
    statements = []
    def observe(conn, cursor, sql, params, context, many): statements.append(sql)
    event.listen(db.get_bind(), 'before_cursor_execute', observe)
    try:
        body = read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365).model_dump(mode='json')
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', observe)
    eligible = coverage in ('current', 'older_complete') and proof != 'legacy_unverified'
    if coverage in ('absent', 'conflicting'):
        assert body['calendar_invalid_windows']
        assert {r['reason'] for r in body['calendar_invalid_windows']} == {'official_calendar_unavailable'}
    for window in (5, 10, 20, 40, 60):
        stats = selected(body, basis, window)
        assert stats['coverage_complete'] is eligible
        assert stats['evaluated_count'] == int(eligible)
        assert (stats['median_excess_return_pct'] is not None) is eligible
        if basis == 'next_open': assert (stats['median_return_pct'] is not None) is eligible
    assert not any(sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in statements)
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == before
    assert {r.id: repair.fingerprint(r.step_statuses) for r in db.scalars(select(DailyRadarPreparedRun))} == steps


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_new_strategy_does_not_invalidate_covered_historical_performance(db, basis):
    cal = seed_basis(db, basis)
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {
        'validation-calendar': calendar(cal.start, DAY-timedelta(days=1)).payload()}
    add_candidate(db, add_run(db, DAY), selection='new-strategy')
    db.commit()
    body = read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365).model_dump(mode='json')
    historical = next(c for c in body['cohorts'] if c['signal_start_date'] == str(SIGNAL))
    for window in (5, 10, 20, 40, 60):
        stats = selected(body | {'cohorts': [historical]}, basis, window)
        assert stats['evaluated_count'] == 1 and stats['coverage_complete'] is True
    assert body['calendar_invalid_windows'] == []


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_calendar_coverage_is_checked_per_saved_window(db, basis):
    from datetime import date
    cal = seed_basis(db, basis)
    through = date(2026, 7, 1)
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {'validation-calendar': calendar(cal.start, through).payload()}
    db.commit()
    body = read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365).model_dump(mode='json')
    for window in (5, 10, 20, 40, 60):
        stats = selected(body, basis, window)
        assert stats['evaluated_count'] == int(window <= 20)
        assert stats['coverage_complete'] is (window <= 20)
    assert {r['window_days'] for r in body['calendar_invalid_windows']} == {40, 60}
    assert {r['reason'] for r in body['calendar_invalid_windows']} == {'official_calendar_unavailable'}
