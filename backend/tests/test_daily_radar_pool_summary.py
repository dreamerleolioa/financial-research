from types import SimpleNamespace

from ai_stock_sentinel.daily_radar.pool import pool_summary


def candidate(symbol, *, selected=False, reasons=(), cohort="comparable", limited=False):
    return SimpleNamespace(
        symbol=symbol, selection_status="selected" if selected else "shadow",
        shadow_cohort=None if selected else cohort,
        prefilter_reasons=[{"code": code} for code in reasons],
        input_snapshot={"selection_reason": "candidate_limit"} if limited else {},
    )


def test_summary_separates_missing_data_eligibility_signals_and_limit():
    run = SimpleNamespace(universe_count=7, candidates=[
        candidate("2330.TW", selected=True), candidate("2317.TW", limited=True),
        candidate("1234.TW", reasons=["overextended"]),
        candidate("2345.TW", reasons=["low_liquidity"], cohort="eligibility_audit"),
    ], errors=[
        {"code": "prefilter_rejected", "symbol": "2345.TW", "reasons": ["low_liquidity"]},
        {"code": "prefilter_stale_data", "symbol": "3456.TW", "reasons": ["stale_core_data", "low_liquidity"]},
        {"code": "prefilter_rejected", "symbol": "4567.TW", "reasons": ["data_gap"]},
        {"code": "candidate_processing_error", "symbol": "5678.TW"},
    ])
    summary = pool_summary(run)
    assert summary["state_counts"] == {
        "selected": 1, "data_pending": 2, "eligibility_excluded": 1,
        "signal_filtered": 1, "limit_deferred": 1, "processing_error": 1,
    }
    assert summary["comparable_shadow_count"] == 2
    assert summary["eligibility_audit_shadow_count"] == 1
    assert summary["unclassified_record_count"] == 0
    assert summary["reason_counts"]["low_liquidity"] == 2
    assert summary["population_scope"] == "scored_raw_records"


def test_historical_unknowns_and_duplicates_do_not_become_qualified_candidates():
    run = SimpleNamespace(universe_count=4, candidates=[candidate("2330.TW", selected=True)], errors=[
        {"code": "duplicate_universe_symbol", "symbol": "2330.TW"},
        {"code": "prefilter_rejected", "symbol": "2317.TW", "reasons": ["future_unknown_reason"]},
    ])
    summary = pool_summary(run)
    assert summary["duplicate_record_count"] == 1
    assert summary["unclassified_record_count"] == 2
    assert sum(summary["state_counts"].values()) == 1


def test_selected_status_wins_over_historical_prefilter_errors():
    run = SimpleNamespace(universe_count=1, candidates=[candidate("2330.TW", selected=True)], errors=[
        {"code": "prefilter_rejected", "symbol": "2330.TW", "reasons": ["overextended"]},
    ])
    assert pool_summary(run)["state_counts"]["selected"] == 1


def test_public_limit_does_not_shrink_pool_summary_or_expose_shadow_symbols():
    from datetime import date
    from ai_stock_sentinel.daily_radar.presenter import public_run_response
    from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarCandidate

    run = DailyRadarRun(run_date=date(2026, 6, 1), market="TW", status="completed",
                        universe_count=3, errors=[])
    run.candidates = [DailyRadarCandidate(
        symbol=symbol, name=symbol, primary_bucket="support_retest", secondary_buckets=[],
        observation_score=90-index, repeat_status="new", explanation="觀察",
        selection_status="selected" if index < 2 else "shadow",
        shadow_cohort=None if index < 2 else "comparable", prefilter_reasons=[],
        input_snapshot={"selection_reason": "candidate_limit"} if index == 2 else {},
        risk_labels=[], bucket_scores={}, score_breakdown={}, data_dates={}, matched_rules=[],
    ) for index, symbol in enumerate(["2330.TW", "2317.TW", "PRIVATE.TW"])]
    response = public_run_response(run, bucket=None, limit=1)
    assert len(response.candidates) == 1
    assert response.pool_summary.state_counts["selected"] == 2
    assert response.pool_summary.state_counts["limit_deferred"] == 1
    assert "PRIVATE.TW" not in response.model_dump_json()
