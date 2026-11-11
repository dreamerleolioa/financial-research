"""Calendar-backed maturity must govern both bounded retries and backlog counts."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from ai_stock_sentinel.daily_radar import research_validation as research
from ai_stock_sentinel.daily_radar.forward_validation import (
    forward_validation_candidates_from_runs, upsert_forward_validation_results, default_due_start_date,
)
from ai_stock_sentinel.daily_radar.history_repair import result_snapshot
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.daily_radar.readiness import validation_inventory
from ai_stock_sentinel.daily_radar.validation_read import read_observation_validation
from ai_stock_sentinel.daily_radar.trading_calendar import (
    CalendarEvidenceIndex, TradingCalendar, stored_calendars, pending_window_is_immature,
)
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from test_daily_radar_validation_read import add_run, add_candidate
from test_next_open_history_repair import db, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from radar_calendar_support import calendar, FixtureCalendarProvider

WINDOWS = [5, 10, 20, 40, 60]
PENDING_DAY = date(2026, 6, 3)


def seed(db, version=research.RESEARCH_VALIDATION_VERSION):
    c = add_candidate(db, add_run(db, SIGNAL))
    c.data_dates = {'ohlcv': str(SIGNAL)}
    c.input_snapshot = c.input_snapshot | {'ohlcv': {'close': 100}}
    db.commit()
    candidate = forward_validation_candidates_from_runs(db, market='TW', end_date=PENDING_DAY)[0]
    cal = calendar(SIGNAL, PENDING_DAY)
    bars = Prices(cal).fetch([c.symbol, 'TAIEX'], start_date=SIGNAL, end_date=PENDING_DAY)
    outcomes = [research.evaluate_research_window(candidate, price_series=bars[c.symbol], benchmark_prices=bars['TAIEX'],
        window_days=w, as_of_date=PENDING_DAY, benchmark_symbol='TAIEX', trading_calendar=cal.sessions) for w in WINDOWS]
    assert all(r['status'] == 'pending' and r['skip_reason'] == 'window_not_mature' for r in outcomes)
    # Historical imports may retain the explicit skip instead of dropping pending rows.
    upsert_forward_validation_results(db, [r | {'status': 'skipped', 'validation_version': version} for r in outcomes])
    prepared = DailyRadarPreparedRun(run_date=PENDING_DAY, market='TW', symbol_count=0, selected_symbols=[], universe=[],
        step_statuses={'validation-calendar': cal.payload()})
    db.add(prepared)
    db.commit()
    return c.id, prepared


@pytest.mark.parametrize('version', research.RESEARCH_RESUME_VERSIONS)
@pytest.mark.parametrize('stored_proof', [False, True])
def test_pending_history_does_not_block_runner_readiness_before_maturity(db, tmp_path, version, stored_proof):
    _, prepared = seed(db, version)
    if not stored_proof: prepared.step_statuses = {}; db.commit()
    before = {r.id: result_snapshot(r) for r in db.scalars(select(Result))}
    prices = Prices(calendar(SIGNAL, PENDING_DAY))
    def unexpected_fetch(*args, **kwargs):
        pytest.fail('proven immature windows do not need remote price evidence')
    prices.fetch = unexpected_fetch
    request = DailyRadarForwardValidationRunRequest(mode='due', return_basis='next_open', windows=WINDOWS)
    result = research.run_research_validation(db, request, as_of_date=PENDING_DAY, price_provider=prices,
        calendar_provider=FixtureCalendarProvider())
    db.commit()
    assert result['report']['remaining_retryable_window_count'] == 0
    assert result['records_written'] == result['retryable_skipped_count'] == 0
    assert {r.id: result_snapshot(r) for r in db.scalars(select(Result))} == before
    inventory = validation_inventory(db, as_of_date=PENDING_DAY)
    assert inventory['next_open']['ready'] is True
    from test_radar_validation_workflow_execution import runner
    posted = []

    def post(endpoint, token, payload):
        posted.append(dict(payload))
        return result | {'as_of_date': str(PENDING_DAY)}

    readiness = {'version': 'radar-readiness-v1', 'market': 'TW', 'requested_run_date': str(PENDING_DAY),
        'effective_run_date': str(PENDING_DAY), 'publication': {}, 'scoring': {}, 'validation': inventory}
    assert runner().run(endpoint='https://test.invalid', token='fixture', run_date=str(PENDING_DAY),
        output_dir=tmp_path, request=post, readiness_request=lambda *a: readiness,
        return_bases=('next_open',), sleep=lambda _: None) == 0
    assert len(posted) == 1 and posted[0]['windows'] == WINDOWS
    body = read_observation_validation(db, market='TW', as_of_date=PENDING_DAY, lookback_days=365).model_dump(mode='json')
    for w in WINDOWS:
        stats = body['cohorts'][0]['research_pool_comparison'][str(w)]['cost_scenarios']['0']['selected']
        assert stats['immature_count'] == 1 and stats['coverage_complete'] is True


@pytest.mark.parametrize('proof', ['current', 'absent', 'stale', 'conflicting', 'signal_uncovered'])
@pytest.mark.parametrize('reason', ['window_not_mature', 'missing_future_price'])
def test_retry_query_excludes_only_proven_immature_pending_rows(db, proof, reason):
    _, prepared = seed(db)
    if proof == 'absent': prepared.step_statuses = {}
    elif proof == 'stale':
        prepared.step_statuses = {'validation-calendar': calendar(SIGNAL, PENDING_DAY-timedelta(days=1)).payload()}
    elif proof == 'signal_uncovered':
        prepared.step_statuses = {'validation-calendar': calendar(SIGNAL+timedelta(days=1), PENDING_DAY).payload()}
    elif proof == 'conflicting':
        through = PENDING_DAY-timedelta(days=1)
        db.add(DailyRadarPreparedRun(run_date=through, market='TW', symbol_count=0, selected_symbols=[], universe=[],
            step_statuses={'validation-calendar': calendar(SIGNAL, through, {SIGNAL}).payload()}))
    for row in db.scalars(select(Result)):
        row.skip_reason = reason
        row.outcome = row.outcome | {'skip_reason': reason}
    db.commit()
    proof = CalendarEvidenceIndex(stored_calendars(db, as_of_date=PENDING_DAY)).latest()
    request = DailyRadarForwardValidationRunRequest(mode='due', return_basis='next_open', windows=WINDOWS)
    expected = 0 if reason == 'window_not_mature' and proof and proof.start <= SIGNAL and proof.through >= PENDING_DAY else 5
    rows = db.execute(research._retryable_windows_query(request, PENDING_DAY, calendar=proof)).all()
    assert len(rows) == expected


@pytest.mark.parametrize('maturing_window', WINDOWS)
@pytest.mark.parametrize('version', research.RESEARCH_RESUME_VERSIONS)
def test_each_exact_target_restores_pending_windows_to_real_evaluation(db, maturing_window, version):
    cid, prepared = seed(db, version)
    full = calendar(SIGNAL, DAY)
    target = [d for d in full.sessions if d > SIGNAL][maturing_window-1]
    proof = calendar(SIGNAL, target)
    prepared.run_date = target
    prepared.step_statuses = {'validation-calendar': proof.payload()}
    db.commit()
    request = DailyRadarForwardValidationRunRequest(mode='due', return_basis='next_open', windows=WINDOWS,
        start_date=SIGNAL, end_date=SIGNAL)
    result = research.run_research_validation(db, request, as_of_date=target, price_provider=Prices(proof))
    db.commit()
    due = {w for w in WINDOWS if w <= maturing_window}
    assert result['validated_count'] == len(due)
    assert result['report']['remaining_retryable_window_count'] == 0
    saved = list(db.scalars(select(Result).where(Result.candidate_id == cid,
        Result.validation_version == research.RESEARCH_VALIDATION_VERSION, Result.status == 'validated')))
    assert {r.window_days for r in saved} == due
    assert all(r.target_date == [d for d in full.sessions if d > SIGNAL][r.window_days-1] for r in saved)


def test_immature_history_cannot_consume_the_bounded_old_backlog_slot(db, monkeypatch):
    cal = calendar(SIGNAL, DAY)
    old = add_candidate(db, add_run(db, SIGNAL), symbol='2330.TW')
    recent = add_candidate(db, add_run(db, DAY), symbol='2317.TW')
    for c, signal in ((old, SIGNAL), (recent, DAY)):
        c.data_dates = {'ohlcv': str(signal)}
        c.input_snapshot = c.input_snapshot | {'ohlcv': {'close': 100}}
        db.add(Result(candidate_id=c.id, window_days=5, validation_version=research.RESEARCH_VALIDATION_VERSION,
            status='skipped', signal_date=signal, target_date=None, benchmark_symbol='TAIEX',
            evaluation_as_of_date=DAY, skip_reason='window_not_mature' if c is recent else 'missing_future_price',
            outcome={}, updated_at=datetime(2000 if c is recent else 2001, 1, 1, tzinfo=timezone.utc)))
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', symbol_count=0, selected_symbols=[], universe=[],
        step_statuses={'validation-calendar': cal.payload()}))
    db.commit()
    assert SIGNAL < default_due_start_date(DAY, 5)
    monkeypatch.setattr(research, 'RESEARCH_RESUME_BATCH_SIZE', 1)
    result = research.run_research_validation(db,
        DailyRadarForwardValidationRunRequest(return_basis='next_open', windows=[5]),
        as_of_date=DAY, price_provider=Prices(cal))
    db.commit()
    assert result['validated_count'] == 1
    assert result['report']['remaining_retryable_window_count'] == 0
    assert db.scalar(select(Result).where(Result.candidate_id == old.id)).status == 'validated'
    assert db.scalar(select(Result).where(Result.candidate_id == recent.id)).skip_reason == 'window_not_mature'


def test_official_maturity_outweighs_calendar_age_during_extended_market_closure(db):
    _, prepared = seed(db)
    cutoff = date(2026, 6, 30)
    closed = {PENDING_DAY+timedelta(days=i) for i in range(1, (cutoff-PENDING_DAY).days+1)}
    proof = calendar(SIGNAL, cutoff, closed)
    assert len([d for d in proof.sessions if d > SIGNAL]) == 2
    prepared.run_date = cutoff
    prepared.step_statuses = {'validation-calendar': proof.payload()}
    db.commit()
    before = {r.id: result_snapshot(r) for r in db.scalars(select(Result))}
    result = research.run_research_validation(db,
        DailyRadarForwardValidationRunRequest(return_basis='next_open', windows=WINDOWS, start_date=SIGNAL),
        as_of_date=cutoff, price_provider=Prices(proof))
    db.commit()
    assert result['records_written'] == 0 and result['report']['remaining_retryable_window_count'] == 0
    assert {r.id: result_snapshot(r) for r in db.scalars(select(Result))} == before


def test_sql_retry_selection_matches_session_count_oracle_with_special_opening(db):
    cutoff = date(2026, 6, 15)
    proof = calendar(SIGNAL, cutoff, {date(2026, 6, 2)})
    payload = proof.payload()
    saturday = '2026-06-06'
    payload['sessions'] = sorted(payload['sessions']+[saturday])
    payload['reports'][0]['session_dates'] = sorted(payload['reports'][0]['session_dates']+[saturday])
    proof = TradingCalendar.parse(payload)
    expected = set()
    pending = {'status': 'skipped', 'skip_reason': 'window_not_mature'}
    for offset in range(-2, 15):
        signal = SIGNAL+timedelta(days=offset)
        c = add_candidate(db, add_run(db, signal))
        for window in WINDOWS:
            db.add(Result(candidate_id=c.id, window_days=window, validation_version=research.RESEARCH_VALIDATION_VERSION,
                status='skipped', skip_reason='window_not_mature', signal_date=signal, target_date=None,
                benchmark_symbol='TAIEX', evaluation_as_of_date=cutoff, outcome={}))
            if not pending_window_is_immature(pending, signal=signal, window=window, sessions=proof.sessions):
                expected.add((c.id, window))
    db.commit()
    request = DailyRadarForwardValidationRunRequest(return_basis='next_open', windows=WINDOWS)
    assert set(db.execute(research._retryable_windows_query(request, cutoff, calendar=proof))) == expected


def test_non_trading_signal_is_diagnosed_before_maturity_and_without_price_io(db):
    signal, cutoff = date(2026, 5, 30), date(2026, 6, 30)
    closed = {PENDING_DAY+timedelta(days=i) for i in range(1, (cutoff-PENDING_DAY).days+1)}
    proof = calendar(signal, cutoff, closed)
    c = add_candidate(db, add_run(db, signal))
    c.data_dates = {'ohlcv': str(signal)}
    c.input_snapshot = c.input_snapshot | {'ohlcv': {'close': 100}}
    for window in WINDOWS:
        db.add(Result(candidate_id=c.id, window_days=window, validation_version=research.RESEARCH_VALIDATION_VERSION,
            status='skipped', skip_reason='missing_future_price', signal_date=signal, target_date=None,
            benchmark_symbol='TAIEX', evaluation_as_of_date=cutoff, outcome={}))
    db.add(DailyRadarPreparedRun(run_date=cutoff, market='TW', symbol_count=0, selected_symbols=[], universe=[],
        step_statuses={'validation-calendar': proof.payload()}))
    db.commit()
    prices = Prices(proof)
    result = research.run_research_validation(db,
        DailyRadarForwardValidationRunRequest(return_basis='next_open', windows=WINDOWS,
            start_date=signal, end_date=signal), as_of_date=cutoff, price_provider=prices)
    db.commit()
    assert result['report']['current_run_skip_reasons'] == {'non_trading_signal_date': 5}
    assert result['records_written'] == 5 and prices.calls == 0
    rows = list(db.scalars(select(Result)))
    assert len(rows) == 5 and {r.skip_reason for r in rows} == {'non_trading_signal_date'}
    assert all(r.target_date is None and 'forward_return_pct' not in r.outcome for r in rows)
