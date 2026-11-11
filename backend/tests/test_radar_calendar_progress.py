from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import history_repair as repair
from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
from ai_stock_sentinel.daily_radar.readiness import validation_inventory
from ai_stock_sentinel.daily_radar.research_validation import evaluate_research_window, run_research_validation
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from test_next_open_history_repair import db, seed, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from radar_calendar_support import calendar
from test_daily_radar_validation_read import add_run, add_candidate


def completed(db, *, owned=False):
    cal, _, _, _ = seed(db)
    candidate = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    prices = Prices(cal).fetch([candidate['symbol'], 'TAIEX'], start_date=SIGNAL, end_date=DAY)
    for window in (5, 10, 20, 40, 60):
        result = evaluate_research_window(candidate, price_series=prices[candidate['symbol']], benchmark_prices=prices['TAIEX'],
            window_days=window, as_of_date=DAY, benchmark_symbol='TAIEX', trading_calendar=cal.sessions)
        assert result['status'] == 'validated'
        row = db.scalar(select(Result).where(Result.window_days == window))
        for field in repair.MUTABLE_FIELDS:
            value = result[field]
            if field in ('target_date', 'evaluation_as_of_date') and value: value = date.fromisoformat(value)
            setattr(row, field, value)
        if owned: row.outcome = row.outcome | {'historical_repair': {'repair_id': 'legacy-reviewed'}}
    db.commit()
    return cal


def request():
    return DailyRadarForwardValidationRunRequest(mode='due', return_basis='next_open', as_of_date=DAY, windows=[20])


def recent_candidate(db, cal):
    signal = date(2026, 8, 3)
    row = add_candidate(db, add_run(db, signal), symbol='2317.TW')
    row.data_dates = {'ohlcv': str(signal)}
    row.input_snapshot = dict(row.input_snapshot) | {'ohlcv': {'close': 100 + Prices(cal).days.index(signal)}}
    db.commit()
    return signal, row.id


def test_cached_calendar_does_not_expand_active_benchmark_repair(db):
    cal = completed(db)
    signal, _ = recent_candidate(db, cal)
    required = []
    class RecordingPrices(Prices):
        def repair(self, prices, **kwargs):
            required.extend(kwargs['required_dates_by_symbol'].get('TAIEX', []))
            return super().repair(prices, **kwargs)
    run_research_validation(db, request(), as_of_date=DAY, price_provider=RecordingPrices(cal))
    assert required and min(required) == signal and max(required) == DAY


@pytest.mark.parametrize('change', ['delete', 'conflict'])
def test_cached_calendar_is_revalidated_before_acceptance(db, change):
    cal = completed(db)
    signal, candidate_id = recent_candidate(db, cal)
    before = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    changed = False
    def check():
        nonlocal changed
        if changed: return
        changed = True
        with Session(db.get_bind()) as other:
            prepared = other.scalar(select(DailyRadarPreparedRun))
            if change == 'delete': other.delete(prepared)
            else: prepared.step_statuses = {'validation-calendar': calendar(cal.start, DAY, {signal+timedelta(days=1)}).payload()}
            other.commit()
    error = 'calendar_checkpoint_unavailable' if change == 'delete' else 'calendar_evidence_conflict'
    with pytest.raises(ValueError, match=error):
        run_research_validation(db, request(), as_of_date=DAY, price_provider=Prices(cal, check=check))
    db.commit(); db.expire_all()
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == before
    assert db.scalar(select(Result).where(Result.candidate_id == candidate_id)) is None


@pytest.mark.parametrize('owned', [False, True])
@pytest.mark.parametrize('coverage', ['current', 'stale', 'absent'])
def test_calendar_progress_is_independent_of_completed_result_writes(db, owned, coverage):
    cal = completed(db, owned=owned)
    prepared = db.scalar(select(DailyRadarPreparedRun))
    prepared.step_statuses = ({'validation-calendar': (cal if coverage == 'current' else
        calendar(cal.start, DAY-timedelta(days=1))).payload()} if coverage != 'absent' else {})
    db.commit()
    before = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    calls = []
    def fetch(**kwargs): calls.append(kwargs); return cal
    prices = Prices(cal)
    result = run_research_validation(db, request(), as_of_date=DAY, price_provider=prices,
        calendar_provider=SimpleNamespace(fetch=fetch))
    db.commit()
    assert len(calls) == (0 if coverage == 'current' else 1)
    assert result['records_written'] == 0 and prices.calls == 0
    assert validation_inventory(db, as_of_date=DAY)['next_open']['ready'] is True
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == before


def test_missing_calendar_checkpoint_rejects_and_rolls_back_own_batch(db):
    cal = completed(db)
    db.delete(db.scalar(select(DailyRadarPreparedRun)))
    row = db.scalar(select(Result).where(Result.window_days == 20))
    row.outcome = {k:v for k,v in row.outcome.items() if k != 'validation_window'} | {'forward_return_pct': 777}
    db.commit()
    before = repair.result_snapshot(row)
    with pytest.raises(ValueError, match='calendar_checkpoint_unavailable'):
        run_research_validation(db, request(), as_of_date=DAY, price_provider=Prices(cal),
            calendar_provider=SimpleNamespace(fetch=lambda **kwargs: cal))
    db.commit(); db.refresh(row)  # Even a caller committing after the error cannot persist partial results.
    assert repair.result_snapshot(row) == before


def test_calendar_provider_failure_does_not_claim_completed(db):
    from ai_stock_sentinel.daily_radar.trading_calendar import CalendarUnavailableError
    cal = completed(db)
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {}; db.commit()
    before = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    def fetch(**kwargs): raise TimeoutError('fixture')
    with pytest.raises(CalendarUnavailableError):
        run_research_validation(db, request(), as_of_date=DAY, price_provider=Prices(cal),
            calendar_provider=SimpleNamespace(fetch=fetch))
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == before


def test_prepare_only_returns_calendar_progress_without_a_checkpoint_write(db):
    cal = completed(db)
    db.delete(db.scalar(select(DailyRadarPreparedRun))); db.commit()
    before = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    bundle = run_research_validation(db, request(), as_of_date=DAY, price_provider=Prices(cal),
        calendar_provider=SimpleNamespace(fetch=lambda **kwargs: cal), prepare_only=True)
    assert bundle.calendar.payload() == cal.payload() and bundle.outcomes == []
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == before


def test_explicit_range_still_checks_the_supplied_provider_with_a_cached_calendar(db):
    from ai_stock_sentinel.daily_radar.trading_calendar import CalendarUnavailableError
    cal = completed(db)
    def fetch(**kwargs): raise TimeoutError('explicit source check')
    with pytest.raises(CalendarUnavailableError):
        run_research_validation(db, DailyRadarForwardValidationRunRequest(mode='range', return_basis='next_open',
            start_date=SIGNAL, end_date=DAY, as_of_date=DAY, windows=[20]), as_of_date=DAY,
            price_provider=Prices(cal), calendar_provider=SimpleNamespace(fetch=fetch))
