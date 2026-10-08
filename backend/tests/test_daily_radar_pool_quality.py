from datetime import date, timedelta

from ai_stock_sentinel.daily_radar.pool_quality import pool_comparisons


def candidate(index, *, selected=True, cohort="comparable", score=90, day="2026-06-01"):
    return {"candidate_id": index, "symbol": f"{2300 + index}.TW", "record_date": day,
            "observation_score": score, "selection_status": "selected" if selected else "shadow",
            "shadow_cohort": None if selected else cohort, "input_snapshot": {}}


def outcome(c, excess, *, benchmark="TAIEX"):
    return {"candidate_id": c["candidate_id"], "symbol": c["symbol"], "signal_date": c["record_date"],
            "window_days": 5, "status": "validated", "benchmark_symbol": benchmark,
            "outcome": {"excess_return_vs_benchmark_pct": excess}}


def comparison(candidates, outcomes, calendar=None):
    return next(iter(pool_comparisons(candidates, outcomes, [5], calendar=calendar).values()))["5"]


def test_quality_compares_observed_comparable_pool_and_keeps_fixed_daily_ranks():
    selected = [candidate(i, score=100-i) for i in range(1, 5)]
    shadow = candidate(5, selected=False)
    audit = candidate(6, selected=False, cohort="eligibility_audit")
    rows = [outcome(c, excess) for c, excess in zip(selected, [4, -2, 1, 9])]
    result = comparison(selected + [shadow, audit], rows + [outcome(shadow, 3), outcome(audit, 100)])
    assert result["selected"]["median_excess_return_pct"] == 2.5
    assert result["top_3"]["median_excess_return_pct"] == 1
    assert result["comparable_shadow"]["sample_count"] == 1
    assert result["observed_positive_capture_share"] == .75
    assert result["population_scope"] == "observed_daily_comparable_pool"


def test_missing_top_rank_never_gets_replaced_or_publishes_a_rate():
    rows = [candidate(i, score=100-i) for i in range(1, 5)]
    result = comparison(rows, [outcome(c, 5) for c in rows[1:]])
    assert result["top_3"]["sample_count"] == 3
    assert result["top_3"]["evaluated_count"] == 2
    assert result["top_3"]["missing_outcome_count"] == 1
    assert result["top_3"]["positive_excess_rate"] is None
    assert result["top_3"]["median_excess_return_pct"] is None
    assert result["observed_positive_capture_share"] is None


def test_immature_missing_and_invalid_metrics_are_distinct():
    old = candidate(1)
    pending = candidate(2, day="2026-06-05")
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(8)]
    result = comparison([old, pending], [outcome(old, float("nan"))], calendar=days)
    assert result["selected"]["immature_count"] == 1
    assert result["selected"]["missing_metric_count"] == 1
    assert result["selected"]["missing_outcome_count"] == 0
    assert not result["selected"]["coverage_complete"]


def test_daily_repeats_are_disclosed_and_strategy_versions_remain_separate():
    c = candidate(1)
    repeat = candidate(2, day="2026-06-02") | {"symbol": c["symbol"]}
    other = candidate(3)
    other["input_snapshot"] = {"selection_version": "new"}
    results = pool_comparisons([c, repeat, other], [outcome(c, 2), outcome(repeat, 3), outcome(other, 4)], [5])
    assert len(results) == 2
    legacy = results[("unknown", "unknown", "unknown", "legacy")]["5"]["selected"]
    assert legacy["evaluated_count"] == 2
    assert legacy["distinct_symbol_count"] == 1
    assert legacy["signal_date_count"] == 2


def test_incomplete_rank_pool_and_mixed_benchmarks_block_comparisons():
    c = candidate(1) | {"daily_selected_pool_count": 2}
    shadow = candidate(2, selected=False)
    result = comparison([c, shadow], [outcome(c, 2), outcome(shadow, 4, benchmark="SPY")])
    assert not result["top_3"]["coverage_complete"]
    assert result["observed_positive_capture_share"] is None
