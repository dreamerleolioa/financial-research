from datetime import date, timedelta

from ai_stock_sentinel.daily_radar.research_quality import research_pool_comparisons
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from test_daily_radar_pool_quality import candidate as base_candidate, outcome


def candidate(*args, **kwargs):
    return base_candidate(*args, **kwargs) | {"input_snapshot": {
        "versions": {"scoring_version": "s1", "rule_version": "r1", "config_version": "c1"},
        "selection_version": "policy1"}}


def saved(c, excess, gross=None, adverse=-5, window=5):
    row = outcome(c, excess)
    return row | {"window_days": window, "validation_version": RESEARCH_VALIDATION_VERSION,
        "outcome": row["outcome"] | {"return_basis": "next_open", "price_basis": "unadjusted_price",
            "forward_return_pct": excess if gross is None else gross,
            "max_adverse_excursion_pct": adverse}}


def comparison(candidates, outcomes, calendar=None, window=5):
    return next(iter(research_pool_comparisons(candidates, outcomes, [window], calendar=calendar).values()))[str(window)]


def test_risk_tail_and_costs_use_fixed_ranks_and_saved_new_basis():
    rows = [candidate(i, score=100-i) for i in range(1, 11)]
    result = comparison(rows, [saved(c, i - 5, adverse=-i) for i, c in enumerate(rows, 1)])
    gross = result["cost_scenarios"]["0"]["selected"]
    cost = result["cost_scenarios"]["0.5"]["selected"]
    assert gross["median_return_pct"] == .5
    assert gross["worst_decile_mean_return_pct"] == -4
    assert gross["worst_adverse_excursion_pct"] == -10
    assert gross["median_adverse_excursion_pct"] == -5.5
    assert cost["median_return_pct"] == 0
    assert cost["worst_decile_mean_return_pct"] == -4.5
    assert cost["positive_excess_rate"] == .5
    assert result["cost_scenarios"]["0"]["top_3"]["median_return_pct"] == -3
    assert result["cost_scenarios"]["0"]["confidence"]["status"] == "calendar_missing"


def test_partial_risk_and_wrong_validation_versions_do_not_become_zero_or_complete():
    first, second = candidate(1), candidate(2)
    row = saved(first, 2, adverse=None)
    wrong = saved(second, 100) | {"validation_version": "daily-radar-forward-validation-v2"}
    result = comparison([first, second], [row, wrong])["cost_scenarios"]["0"]
    stats = result["selected"]
    assert stats["missing_outcome_count"] == 1
    assert stats["missing_risk_count"] == 1
    assert stats["worst_adverse_excursion_pct"] is None
    assert stats["median_return_pct"] is None
    assert result["confidence"]["status"] == "incomplete_coverage"


def test_missing_and_skip_reasons_remain_visible():
    c = candidate(1)
    row = saved(c, 2) | {"status": "skipped", "skip_reason": "missing_entry_open"}
    stats = comparison([c], [row])["cost_scenarios"]["0"]["selected"]
    assert stats["missing_reasons"] == {"missing_entry_open": 1}
    assert stats["median_return_pct"] is None


def test_confidence_clusters_same_day_rows_and_requires_ten_full_horizon_blocks():
    day = date(2026, 1, 1)
    calendar = [day + timedelta(days=i) for i in range(70)]
    candidates, outcomes = [], []
    for i in range(49):
        for selected in (True, False):
            for j in range(3):
                c = candidate(len(candidates) + 1, selected=selected, day=calendar[i].isoformat())
                c["symbol"] = f"{2330+j}.TW"  # deliberately repeated, not independent trades
                candidates.append(c)
                outcomes.append(saved(c, 2 if selected else 1))
    result = comparison(candidates, outcomes, calendar=calendar)["cost_scenarios"]["0"]["confidence"]
    assert result["paired_date_count"] == 49
    assert result["effective_block_count"] == 9
    assert result["status"] == "insufficient_blocks"
    assert result["lower_pct"] is None
    for selected in (True, False):
        c = candidate(len(candidates) + 1, selected=selected, day=calendar[49].isoformat())
        candidates.append(c)
        outcomes.append(saved(c, 2 if selected else 1))
    report = comparison(candidates, outcomes, calendar=calendar)
    ci = report["cost_scenarios"]["0"]["confidence"]
    assert ci["status"] == "estimated"
    assert ci["paired_date_count"] == 50
    assert ci["lower_pct"] == ci["upper_pct"] == 1
    assert ci == comparison(candidates, outcomes, calendar=calendar)["cost_scenarios"]["0"]["confidence"]
    assert ci == report["cost_scenarios"]["1"]["confidence"]  # equal assumed costs cancel in group difference


def test_confidence_uses_only_dates_with_both_groups_and_never_mixes_benchmarks():
    c, shadow = candidate(1), candidate(2, selected=False, day="2026-06-02")
    result = comparison([c, shadow], [saved(c, 3), saved(shadow, 2)],
                        calendar=[date(2026, 6, 1), date(2026, 6, 2)])
    assert result["cost_scenarios"]["0"]["confidence"]["paired_date_count"] == 0
    shadow["record_date"] = c["record_date"]
    wrong = saved(shadow, 2) | {"benchmark_symbol": "SPX"}
    ci = comparison([c, shadow], [saved(c, 3), wrong], calendar=[date(2026, 6, 1)])["cost_scenarios"]["0"]["confidence"]
    assert ci["status"] == "incomplete_coverage"


def test_overlapping_horizons_produce_block_uncertainty_and_duplicate_rows_do_not_inflate_confidence():
    calendar = [date(2025, 1, 1) + timedelta(days=i) for i in range(240)]
    candidates, outcomes = [], []
    for i in range(200):
        for selected in (True, False):
            c = candidate(len(candidates) + 1, selected=selected, day=calendar[i].isoformat())
            c["symbol"] = "2330.TW" if selected else "2317.TW"
            candidates.append(c)
            outcomes.append(saved(c, (10 if i < 100 else -10) if selected else 0, window=20))
    ci = comparison(candidates, outcomes, calendar, window=20)["cost_scenarios"]["0"]["confidence"]
    assert ci["status"] == "estimated"
    assert ci["mean_difference_pct"] == 0
    assert ci["lower_pct"] < 0 < ci["upper_pct"]
    assert ci["upper_pct"] - ci["lower_pct"] > 8
    duplicate = candidate(999, day=calendar[0].isoformat()) | {"symbol": "2330.TW"}
    repeated = comparison(candidates + [duplicate], outcomes + [saved(duplicate, 10, window=20)], calendar, window=20)
    assert repeated["cost_scenarios"]["0"]["confidence"] == ci


def test_confidence_counts_contiguous_date_blocks_instead_of_dividing_sparse_dates():
    calendar = [date(2025, 1, 1) + timedelta(days=i) for i in range(150)]
    candidates, outcomes = [], []
    for day in calendar[:120:2]:
        for selected in (True, False):
            c = candidate(len(candidates) + 1, selected=selected, day=day.isoformat())
            candidates.append(c)
            outcomes.append(saved(c, 2 if selected else 1))
    ci = comparison(candidates, outcomes, calendar)["cost_scenarios"]["0"]["confidence"]
    assert ci["paired_date_count"] == 60
    assert ci["effective_block_count"] == 0
    assert ci["status"] == "insufficient_blocks"


def test_missing_calendar_keeps_known_paired_dates_but_block_count_unknown():
    c, shadow = candidate(1), candidate(2, selected=False)
    ci = comparison([c, shadow], [saved(c, 2), saved(shadow, 1)])["cost_scenarios"]["0"]["confidence"]
    assert ci["status"] == "calendar_missing"
    assert ci["paired_date_count"] == 1
    assert ci["effective_block_count"] is None


def test_unknown_strategy_versions_never_publish_confidence_for_mixed_history():
    c, shadow = candidate(1), candidate(2, selected=False)
    c["input_snapshot"] = shadow["input_snapshot"] = {}
    ci = comparison([c, shadow], [saved(c, 2), saved(shadow, 1)], [date(2026, 6, 1)])["cost_scenarios"]["0"]["confidence"]
    assert ci["status"] == "strategy_unknown"
    assert ci["lower_pct"] is None
    assert ci["effective_block_count"] is None
