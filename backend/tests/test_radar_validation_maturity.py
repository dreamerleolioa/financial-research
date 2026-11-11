"""Saved pending windows and absent rows have the same maturity semantics."""
from datetime import date, timedelta

import pytest
from sqlalchemy import event, select

from ai_stock_sentinel.daily_radar import history_repair as repair
from ai_stock_sentinel.daily_radar.forward_validation import (
    build_forward_validation_report, forward_validation_candidates_from_runs,
    upsert_forward_validation_results,
)
from ai_stock_sentinel.daily_radar.readiness import validation_inventory
from ai_stock_sentinel.daily_radar.research_validation import evaluate_research_window
from ai_stock_sentinel.daily_radar.validation_read import read_observation_validation
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from test_daily_radar_validation_read import add_run, add_candidate
from test_next_open_history_repair import db, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from test_radar_public_window_eligibility import selected
from radar_calendar_support import calendar

WINDOWS = [5, 10, 20, 40, 60]
PENDING_DAY = date(2026, 6, 3)


def seed_pending(db):
    row = add_candidate(db, add_run(db, SIGNAL))
    row.data_dates = {"ohlcv": str(SIGNAL)}
    row.input_snapshot = row.input_snapshot | {"ohlcv": {"close": 100}}
    db.commit()
    candidate = forward_validation_candidates_from_runs(db, market="TW", end_date=PENDING_DAY)[0]
    cal = calendar(SIGNAL, PENDING_DAY)
    bars = Prices(cal).fetch([candidate["symbol"], "TAIEX"], start_date=SIGNAL, end_date=PENDING_DAY)
    outcomes = build_forward_validation_report([candidate], price_series_by_symbol=bars,
        benchmark_prices=bars["TAIEX"], market="TW", sample_source="test", as_of_date=PENDING_DAY,
        windows=WINDOWS, trading_calendar=cal.sessions).outcomes
    outcomes += [evaluate_research_window(candidate, price_series=bars[candidate["symbol"]],
        benchmark_prices=bars["TAIEX"], window_days=w, as_of_date=PENDING_DAY,
        benchmark_symbol="TAIEX", trading_calendar=cal.sessions) for w in WINDOWS]
    assert len(outcomes) == 10
    assert {r["skip_reason"] for r in outcomes} == {"window_not_mature"}
    # Next-open normally filters evaluator pending rows before acceptance. Retain
    # explicit historical skipped rows here to exercise the shared read contract.
    outcomes = [r | {"status": "skipped"} if r["status"] == "pending" else r for r in outcomes]
    upsert_forward_validation_results(db, outcomes)
    prepared = DailyRadarPreparedRun(run_date=PENDING_DAY, market="TW", symbol_count=0,
        selected_symbols=[], universe=[], step_statuses={"validation-calendar": cal.payload()})
    db.add(prepared)
    db.commit()
    return prepared


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("proof", ["current", "stale", "absent", "conflicting", "signal_uncovered", "mature"])
def test_public_pending_rows_require_current_full_calendar_and_expire_at_maturity(db, saved, proof):
    prepared = seed_pending(db)
    cutoff = DAY if proof == "mature" else PENDING_DAY
    if proof == "stale":
        prepared.step_statuses = {"validation-calendar": calendar(SIGNAL, PENDING_DAY-timedelta(days=1)).payload()}
    elif proof == "absent": prepared.step_statuses = {}
    elif proof == "conflicting":
        db.add(DailyRadarPreparedRun(run_date=PENDING_DAY-timedelta(days=1), market="TW", symbol_count=0,
            selected_symbols=[], universe=[], step_statuses={"validation-calendar":
                calendar(SIGNAL, PENDING_DAY-timedelta(days=1), {SIGNAL}).payload()}))
    elif proof == "signal_uncovered":
        prepared.step_statuses = {"validation-calendar": calendar(SIGNAL+timedelta(days=1), PENDING_DAY).payload()}
    elif proof == "mature":
        prepared.run_date = DAY
        prepared.step_statuses = {"validation-calendar": calendar(SIGNAL, DAY).payload()}
    if not saved:
        for row in db.scalars(select(Result)): db.delete(row)
    db.commit()
    before = {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))}
    statements = []
    def observe(conn, cursor, sql, params, context, many): statements.append(sql)
    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        body = read_observation_validation(db, market="TW", as_of_date=cutoff, lookback_days=365).model_dump(mode="json")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    immature = proof == "current"
    for window in WINDOWS:
        diagnostic = body["cohorts"][0]["windows"][str(window)]["all_selected"]
        assert diagnostic["immature_observation_count"] == int(immature)
        assert diagnostic["skipped_validation_count"] == int(saved and not immature)
        assert diagnostic["missing_outcome_count"] == int(not saved and not immature)
        assert diagnostic["coverage_complete"] is immature
        for basis in ("signal_close", "next_open"):
            stats = selected(body, basis, window)
            assert stats["immature_count"] == int(immature)
            assert stats["skipped_count"] == int(saved and not immature)
            assert stats["missing_outcome_count"] == int(not saved and not immature)
            assert stats["coverage_complete"] is immature
    if proof in ("current", "mature"):
        inventory = validation_inventory(db, as_of_date=cutoff, lookback_days=365)
        for report in inventory.values():
            assert report["immature_count"] == (5 if immature else 0)
            assert report["retryable_count"] == (5 if saved and not immature else 0)
            assert report["missing_outcome_count"] == (5 if not saved and not immature else 0)
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
    assert {r.id: repair.result_snapshot(r) for r in db.scalars(select(Result))} == before


def test_genuine_saved_failure_remains_a_gap_before_window_maturity(db):
    seed_pending(db)
    for row in db.scalars(select(Result)): row.skip_reason = "missing_future_price"
    db.commit()
    body = read_observation_validation(db, market="TW", as_of_date=PENDING_DAY, lookback_days=365).model_dump(mode="json")
    for window in WINDOWS:
        diagnostic = body["cohorts"][0]["windows"][str(window)]["all_selected"]
        assert diagnostic["immature_observation_count"] == 0
        assert diagnostic["skipped_validation_count"] == 1
        assert diagnostic["missing_reasons"] == {"missing_future_price": 1}
        for basis in ("signal_close", "next_open"):
            stats = selected(body, basis, window)
            assert stats["immature_count"] == 0 and stats["skipped_count"] == 1


@pytest.mark.parametrize("maturing_window", WINDOWS)
def test_pending_expires_on_each_exact_official_target(db, maturing_window):
    prepared = seed_pending(db)
    full = calendar(SIGNAL, DAY)
    cutoff = [d for d in full.sessions if d > SIGNAL][maturing_window-1]
    prepared.run_date = cutoff
    prepared.step_statuses = {"validation-calendar": calendar(SIGNAL, cutoff).payload()}
    db.commit()
    body = read_observation_validation(db, market="TW", as_of_date=cutoff, lookback_days=365).model_dump(mode="json")
    for window in WINDOWS:
        immature = window > maturing_window
        diagnostic = body["cohorts"][0]["windows"][str(window)]["all_selected"]
        assert diagnostic["immature_observation_count"] == int(immature)
        assert diagnostic["skipped_validation_count"] == int(not immature)
        for basis in ("signal_close", "next_open"):
            stats = selected(body, basis, window)
            assert stats["immature_count"] == int(immature)
            assert stats["skipped_count"] == int(not immature)
