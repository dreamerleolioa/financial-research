"""Regression cases verified while reviewing the stock-only Radar change."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.forward_validation import (
    FORWARD_VALIDATION_VERSION, forward_validation_candidates_from_runs,
    persisted_forward_validation_outcomes,
)
from ai_stock_sentinel.daily_radar import research_validation as research
from ai_stock_sentinel.daily_radar.research_validation import (
    RESEARCH_VALIDATION_VERSION, evaluate_research_window, run_research_validation,
)
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult, DailyRadarPreparedRun
from test_daily_radar_forward_validation import _add_candidate, _add_run
from test_daily_radar_research_validation import price
from test_forward_validation_memory import engine_with_tables


def test_shadow_stocks_validate_when_the_only_selected_instrument_is_an_etf():
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            run = _add_run(session, run_date=date(2026, 6, 5))
            run.candidate_count = 1  # Legacy count included the selected ETF.
            _add_candidate(session, run, symbol="0050.TW")
            shadow = _add_candidate(session, run, symbol="2330.TW")
            shadow.selection_status = "shadow"
            shadow.shadow_cohort = "comparable"
            session.commit()

            candidates = forward_validation_candidates_from_runs(session, market="TW")
            assert [c["symbol"] for c in candidates] == ["2330.TW"]
            assert candidates[0]["daily_selected_pool_count"] == 0

            class Provider:
                def fetch(self, symbols, **kwargs):
                    return {symbol: [price("2026-06-05"), price("2026-06-08")]
                            for symbol in symbols}

            result = run_research_validation(session,
                DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1]),
                as_of_date=date(2026, 6, 8), price_provider=Provider())
            assert result["candidate_count"] == 1
            assert result["validated_count"] == 1
    finally:
        engine.dispose()


def _retryable(session, candidate, *, version=RESEARCH_VALIDATION_VERSION):
    row = DailyRadarForwardValidationResult(candidate_id=candidate.id, window_days=1,
        validation_version=version, status="skipped", signal_date=date(2026, 1, 2),
        benchmark_symbol="TAIEX", evaluation_as_of_date=date(2026, 1, 5),
        skip_reason="missing_future_price", outcome={"missing_dates": ["2026-01-05"]})
    session.add(row)
    return row


class _JanuaryPrices:
    calls = 0

    def fetch(self, symbols, **kwargs):
        self.calls += 1
        return {s: [price("2026-01-02"), price("2026-01-05")] for s in symbols}


def test_v1_retryable_windows_migrate_outside_lookback_and_do_not_reappear():
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            candidate = _add_candidate(session, _add_run(session, run_date=date(2026, 1, 2)))
            _retryable(session, candidate, version="daily-radar-next-open-price-v1")
            session.commit()
            provider = _JanuaryPrices()
            request = DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1])
            result = run_research_validation(session, request, as_of_date=date(2026, 10, 14), price_provider=provider)
            assert result["validated_count"] == 1
            assert result["report"]["remaining_retryable_window_count"] == 0
            session.commit()
            result = run_research_validation(session, request, as_of_date=date(2026, 10, 14), price_provider=provider)
            assert result["records_written"] == 0
            assert provider.calls == 2
    finally:
        engine.dispose()


def test_bounded_resume_reports_and_drains_unprocessed_backlog(monkeypatch):
    monkeypatch.setattr(research, "RESEARCH_RESUME_BATCH_SIZE", 1, raising=False)
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            run = _add_run(session, run_date=date(2026, 1, 2))
            for symbol in ("2330.TW", "2454.TW", "2317.TW"):
                _retryable(session, _add_candidate(session, run, symbol=symbol))
            session.commit()
            request = DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1])
            for remaining in (2, 1, 0):
                result = run_research_validation(session, request, as_of_date=date(2026, 10, 14), price_provider=_JanuaryPrices())
                assert result["validated_count"] == 1
                assert result["report"]["remaining_retryable_window_count"] == remaining
                session.commit()
    finally:
        engine.dispose()


@pytest.mark.parametrize("observed,reason", [
    (None, "candidate_price_date_missing"), ("2026-06-08", "future_candidate_price"),
])
def test_next_open_rejects_unproven_candidate_dates_before_loading_prices(observed, reason):
    candidate = {"candidate_id": 1, "symbol": "2330.TW", "record_date": "2026-06-05",
                 "data_dates": {"ohlcv": observed}}
    result = evaluate_research_window(candidate, price_series=[], benchmark_prices=[],
        window_days=1, as_of_date=date(2026, 6, 8), benchmark_symbol="TAIEX")
    assert result["skip_reason"] == reason
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            row = _add_candidate(session, _add_run(session, run_date=date(2026, 6, 5)))
            row.data_dates = candidate["data_dates"]
            session.commit()

            class Provider:
                def fetch(self, *args, **kwargs):
                    pytest.fail("invalid point-in-time evidence must be rejected before fetching prices")

            result = run_research_validation(session,
                DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1]),
                as_of_date=date(2026, 6, 8), price_provider=Provider())
            assert result["terminal_skipped_count"] == 1
            assert result["retryable_skipped_count"] == 0
            assert result["report"]["current_run_skip_reasons"] == {reason: 1}
    finally:
        engine.dispose()


def test_full_saved_calendar_prevents_a_missing_session_from_shifting_the_target():
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            _add_candidate(session, _add_run(session, run_date=date(2026, 6, 5)))
            for day, history in ((date(2026, 6, 8), [price("2026-06-05"), price("2026-06-08")]),
                                 (date(2026, 6, 10), [price("2026-06-05"), price("2026-06-09"), price("2026-06-10")])):
                session.add(DailyRadarPreparedRun(run_date=day, market="TW", selected_symbols=[],
                    universe=[], symbol_count=0,
                    market_context={"benchmark": {"symbol": "TAIEX", "price_history": history}}))
            session.commit()

            class Provider:
                required = None

                def fetch(self, symbols, **kwargs):
                    return {s: [price("2026-06-05"), price("2026-06-09"), price("2026-06-10")]
                            for s in symbols}

                def repair(self, prices, *, required_dates_by_symbol, **kwargs):
                    if "TAIEX" in required_dates_by_symbol:
                        self.required = required_dates_by_symbol["TAIEX"]
                    return prices  # A provider failure must remain missing evidence.

            provider = Provider()
            result = run_research_validation(session,
                DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[2]),
                as_of_date=date(2026, 6, 10), price_provider=provider)
            assert date(2026, 6, 8) in provider.required
            assert result["validated_count"] == 0
            assert result["report"]["current_run_skip_reasons"] == {"missing_benchmark": 1}
    finally:
        engine.dispose()


def test_monthly_governance_excludes_etfs_from_rows_watermarks_and_replay_capacity():
    from ai_stock_sentinel.daily_radar import rule_governance as governance
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            run = _add_run(session, run_date=date(2026, 6, 5))
            _add_candidate(session, run, symbol="0050.TW")
            _add_candidate(session, run, symbol="2330.TW")
            session.commit()
            rows = governance.validation_rows_from_results(session, market="TW",
                start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
            assert {r["symbol"] for r in rows} == {"2330.TW"}
            marks = governance._daily_radar_completeness_watermarks(session, market="TW",
                through_date=date(2026, 6, 30), benchmark_symbol="TAIEX", validation_version=FORWARD_VALIDATION_VERSION)
            assert marks[0]["expected_20d_samples"] == 1
            workload = governance._daily_radar_replay_workload_preflight(session, market="TW", selected_months=["2026-06"])
            assert workload["candidate_count"] == 1
    finally:
        engine.dispose()


def test_persisted_skips_retain_missing_date_diagnostics():
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            candidate = _add_candidate(session, _add_run(session, run_date=date(2026, 1, 2)))
            _retryable(session, candidate)
            session.commit()
            candidates = forward_validation_candidates_from_runs(session, market="TW")
            rows = persisted_forward_validation_outcomes(session, candidates, windows=[1],
                as_of_date=date(2026, 1, 5), validation_version=RESEARCH_VALIDATION_VERSION)
            assert rows[0]["outcome"]["missing_dates"] == ["2026-01-05"]
    finally:
        engine.dispose()


def test_unchanged_failed_attempts_rotate_the_bounded_retry_queue(monkeypatch):
    monkeypatch.setattr(research, "RESEARCH_RESUME_BATCH_SIZE", 1)
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            run = _add_run(session, run_date=date(2026, 1, 2))
            for symbol in ("2330.TW", "2454.TW"):
                row = _retryable(session, _add_candidate(session, run, symbol=symbol))
                row.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
                row.evaluation_as_of_date = date(2026, 10, 14)
                row.outcome = {"missing_dates": ["2026-01-05"], "expected_target_date": "2026-01-05",
                               "skip_reason": "missing_future_price"}
            session.commit()

            class Provider(_JanuaryPrices):
                seen = []

                def fetch(self, symbols, **kwargs):
                    if symbols == ["TAIEX"]:
                        return super().fetch(symbols, **kwargs)
                    self.seen.extend(symbols)
                    return {}

            provider = Provider()
            request = DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1])
            for _ in range(2):
                result = run_research_validation(session, request, as_of_date=date(2026, 10, 14), price_provider=provider)
                assert result["report"]["remaining_retryable_window_count"] == 2
                session.commit()
            assert provider.seen == ["2330.TW", "2454.TW"]
    finally:
        engine.dispose()


def test_future_retry_record_is_not_a_gap_for_a_historical_as_of():
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            candidate = _add_candidate(session, _add_run(session, run_date=date(2026, 1, 2)))
            _retryable(session, candidate).evaluation_as_of_date = date(2026, 10, 15)
            session.commit()
            provider = _JanuaryPrices()
            result = run_research_validation(session,
                DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1]),
                as_of_date=date(2026, 10, 14), price_provider=provider)
            assert result["records_written"] == 0
            assert result["report"]["remaining_retryable_window_count"] == 0
            assert provider.calls == 0
    finally:
        engine.dispose()


def test_future_v2_retry_does_not_hide_a_legacy_gap_visible_on_the_requested_date():
    engine = engine_with_tables()
    try:
        with Session(engine) as session:
            candidate = _add_candidate(session, _add_run(session, run_date=date(2026, 1, 2)))
            _retryable(session, candidate, version="daily-radar-next-open-price-v1")
            _retryable(session, candidate).evaluation_as_of_date = date(2026, 10, 15)
            session.commit()
            result = run_research_validation(session,
                DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1]),
                as_of_date=date(2026, 10, 14), price_provider=_JanuaryPrices())
            assert result["validated_count"] == 1
            assert result["report"]["remaining_retryable_window_count"] == 0
    finally:
        engine.dispose()
