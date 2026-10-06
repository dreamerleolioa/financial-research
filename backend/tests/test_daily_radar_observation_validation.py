from copy import deepcopy
from datetime import date, timedelta

import pytest

from ai_stock_sentinel.daily_radar.forward_validation import (
    build_forward_validation_report,
    build_forward_validation_report_from_outcomes,
    evaluate_forward_window,
    FORWARD_VALIDATION_VERSION,
)


SIGNAL = date(2026, 6, 1)


def candidate(index=1, *, score=90):
    return {
        "candidate_id": index, "symbol": f"{2300 + index}.TW",
        "record_date": SIGNAL.isoformat(), "observation_score": score,
        "selection_status": "selected", "primary_bucket": "support_retest",
        "data_dates": {"ohlcv": SIGNAL.isoformat()},
        "input_snapshot": {
            "ohlcv": {"close": 100},
            "indicators": {"support_level": 95, "resistance_level": 105},
            "versions": {"scoring_version": "v1", "rule_version": "r1", "config_version": "c1"},
            "selection_version": "selection-v1",
        },
        "observation_origin": {"first_seen_date": SIGNAL.isoformat(), "candidate_id": index,
                               "scope": "available_public_history"},
    }


def prices(closes, *, lows=None):
    rows = []
    day = SIGNAL
    for i, close in enumerate([100, *closes]):
        low = lows[i - 1] if lows is not None and i else close - 1
        rows.append({"date": day.isoformat(),
                     "open": close, "high": close + 1, "low": low, "close": close})
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return rows


def evaluate(c, rows, *, window=5, benchmark=None, as_of=None):
    return evaluate_forward_window(
        c, price_series=rows, benchmark_prices=benchmark if benchmark is not None else prices([100] * 20),
        window_days=window, as_of_date=as_of or SIGNAL + timedelta(days=20),
        benchmark_symbol="TAIEX", validation_version=FORWARD_VALIDATION_VERSION,
        hit_threshold_pct=0,
    )


def diagnostic(c, rows, **kwargs):
    return evaluate(c, rows, **kwargs)["outcome"]["observation_diagnostic"]


def test_two_consecutive_closes_confirm_and_waiting_risk_stops_at_confirmation():
    rows = prices([103, 106, 104, 107, 108, 80], lows=[98, 99, 97, 100, 101, 79])
    result = diagnostic(candidate(), rows)
    assert result["status"] == "confirmed"
    assert result["confirmation_date"] == "2026-06-08"
    assert result["lead_trading_days"] == 5
    assert result["waiting_max_adverse_excursion_pct"] == -3
    assert result["resistance_reference"] == 105
    assert result["support_reference"] == 95


def test_invalidation_before_confirmation_is_terminal_even_if_later_breaks_out():
    result = diagnostic(candidate(), prices([106, 94, 107, 108, 109]))
    assert result["status"] == "invalidated"
    assert result["invalidation_date"] == "2026-06-03"
    assert result["confirmation_date"] is None
    assert result["lead_trading_days"] is None
    assert result["waiting_max_adverse_excursion_pct"] == -7


def test_intraday_crossings_and_equal_closes_do_not_confirm_or_invalidate():
    rows = prices([105, 105, 95, 104, 106], lows=[94, 94, 94, 94, 94])
    assert diagnostic(candidate(), rows)["status"] == "unconfirmed"
    # A second close outside the five-day window cannot confirm that window.
    rows = prices([105, 105, 95, 104, 106, 107])
    assert diagnostic(candidate(), rows)["status"] == "unconfirmed"
    assert diagnostic(candidate(), rows, window=6)["status"] == "confirmed"


@pytest.mark.parametrize("change,status,reason", [
    ({"resistance_level": None}, "insufficient_data", "reference_missing_or_invalid"),
    ({"support_level": 110}, "insufficient_data", "reference_missing_or_invalid"),
    ({"resistance_level": float("inf")}, "insufficient_data", "reference_missing_or_invalid"),
    ({"resistance_level": 99}, "already_broken_out", None),
])
def test_reference_availability_and_already_broken_out_are_not_failures(change, status, reason):
    c = candidate()
    c["input_snapshot"]["indicators"].update(change)
    result = diagnostic(c, prices([101] * 5))
    assert result["status"] == status
    assert result["missing_reason"] == reason


def test_repeat_and_unknown_origin_do_not_create_new_opportunities():
    c = candidate()
    c["observation_origin"]["first_seen_date"] = "2026-05-29"
    assert diagnostic(c, prices([106] * 5))["status"] == "not_first_observation"
    del c["observation_origin"]
    assert diagnostic(c, prices([106] * 5))["missing_reason"] == "origin_unknown"


def test_additional_diagnostic_preserves_every_existing_return_field():
    c = candidate()
    rows = prices([101, 102, 103, 106, 107])
    from ai_stock_sentinel.calibration.forward_validation import evaluate_forward_window as shared_evaluate
    from ai_stock_sentinel.daily_radar.forward_validation import DAILY_RADAR_FORWARD_ADAPTER
    baseline = shared_evaluate(c, price_series=rows, benchmark_prices=prices([100] * 20),
                              adapter=DAILY_RADAR_FORWARD_ADAPTER, window_days=5,
                              as_of_date=SIGNAL + timedelta(days=20), benchmark_symbol="TAIEX",
                              validation_version=FORWARD_VALIDATION_VERSION, hit_threshold_pct=0)
    actual = evaluate(c, rows)
    assert {k: v for k, v in actual["outcome"].items() if k != "observation_diagnostic"} == baseline["outcome"]
    assert actual["validation_version"] == baseline["validation_version"]


def test_report_picks_top_three_and_five_before_outcomes_and_discloses_missing():
    candidates = [candidate(i, score=100 - i) for i in range(1, 7)]
    series = {c["symbol"]: prices([106] * 5) for c in candidates}
    evaluation = build_forward_validation_report(
        candidates, price_series_by_symbol=series, benchmark_prices=prices([100] * 20),
        market="TW", sample_source="fixture", as_of_date=SIGNAL + timedelta(days=20), windows=[5],
    )
    report = build_forward_validation_report_from_outcomes(
        candidates, [o for o in evaluation.outcomes if o["candidate_id"] != 1],
        market="TW", sample_source="fixture", as_of_date=SIGNAL + timedelta(days=20), windows=[5],
    )
    cohort = report["observation_diagnostics"]["cohorts"][0]["windows"]["5"]
    assert cohort["top_3"]["candidate_ids"] == [1, 2, 3]
    assert cohort["top_3"]["missing_outcome_count"] == 1
    assert cohort["top_3"]["evaluated_observation_count"] == 2
    assert cohort["top_5"]["candidate_ids"] == [1, 2, 3, 4, 5]
    assert cohort["remaining_after_5"]["candidate_ids"] == [6]
    assert cohort["top_3"]["confirmation_rate"] is None


def test_report_separates_selection_versions_and_legacy_missing_diagnostics():
    candidates = [candidate(1), candidate(2)]
    candidates[1]["input_snapshot"]["selection_version"] = "selection-v2"
    legacy = evaluate(candidates[0], prices([106] * 5))
    legacy["outcome"].pop("observation_diagnostic")
    report = build_forward_validation_report_from_outcomes(
        candidates, [legacy], market="TW", sample_source="fixture",
        as_of_date=SIGNAL + timedelta(days=20), windows=[5],
    )["observation_diagnostics"]
    assert len(report["cohorts"]) == 2
    old = next(c for c in report["cohorts"] if c["strategy"]["selection_version"] == "selection-v1")
    assert old["windows"]["5"]["all_selected"]["missing_diagnostic_count"] == 1
    assert old["windows"]["5"]["all_selected"]["confirmation_rate"] is None


def test_report_discloses_immature_and_repeated_signals_without_blocking_mature_first_observations():
    first, repeat, recent = candidate(1), candidate(2), candidate(3)
    repeat["record_date"] = "2026-06-02"
    repeat["observation_origin"] = deepcopy(first["observation_origin"])
    recent["record_date"] = "2026-06-08"
    recent["observation_origin"]["first_seen_date"] = "2026-06-08"
    report = build_forward_validation_report_from_outcomes(
        [first, repeat, recent], [evaluate(first, prices([106] * 5))], market="TW", sample_source="test",
        as_of_date=date(2026, 6, 8), windows=[5], benchmark_prices=prices([100] * 5),
    )["observation_diagnostics"]["cohorts"][0]["windows"]["5"]["all_selected"]
    assert report["confirmation_rate"] == 1
    assert report["immature_observation_count"] == 1
    assert report["excluded_repeat_count"] == 1
    assert report["missing_outcome_count"] == 0


def test_incomplete_ranking_pool_cannot_produce_top_three_rates():
    candidates = [candidate(i, score=100 - i) for i in range(1, 4)]
    for c in candidates:
        c["daily_selected_pool_count"] = 5
    report = build_forward_validation_report_from_outcomes(
        candidates, [evaluate(c, prices([106] * 5)) for c in candidates],
        market="TW", sample_source="test", as_of_date=SIGNAL + timedelta(days=20), windows=[5],
    )["observation_diagnostics"]["cohorts"][0]["windows"]["5"]["top_3"]
    assert report["ranking_pool_complete"] is False
    assert report["confirmation_rate"] is None


def test_diagnostic_requires_exact_candidate_calendar_and_rejects_conflicting_ohlc():
    from ai_stock_sentinel.daily_radar.observation_validation import evaluate_observation
    full = prices([106] * 5)
    for rows in [full[:2] + full[3:], full + [dict(full[1]) | {"close": 107}],
                 full[:1] + [dict(full[1]) | {"low": 108}] + full[2:]]:
        result = evaluate_observation(candidate(), price_series=rows, benchmark_prices=prices([100] * 5),
                                      window_days=5, as_of_date=SIGNAL + timedelta(days=20))
        assert result["status"] == "insufficient_data"
        assert result["missing_reason"] == "candidate_history_gap_or_invalid_ohlc"


@pytest.mark.parametrize("field,value", [("open", 106), ("high", 109), ("low", 105), ("close", 106)])
def test_direct_duplicate_ohlc_conflict_cannot_publish_an_observation(field, value):
    rows = prices([106, 107, 108, 109, 110])
    duplicate = dict(rows[2]) | {field: value}
    result = diagnostic(candidate(), rows + [duplicate])
    assert result["status"] == "insufficient_data"
    assert result["missing_reason"] == "candidate_history_gap_or_invalid_ohlc"


def test_direct_open_conflict_survives_an_intermediate_close_high_low_only_row():
    rows = prices([106, 107, 108, 109, 110])
    partial = {key: value for key, value in rows[2].items() if key != "open"}
    result = diagnostic(candidate(), rows + [partial, dict(rows[2]) | {"open": 106}])
    assert result["status"] == "insufficient_data"


def test_optional_open_absence_on_an_identical_duplicate_does_not_create_conflict():
    rows = prices([106, 107, 108, 109, 110])
    partial = {key: value for key, value in rows[2].items() if key != "open"}
    assert diagnostic(candidate(), rows + [partial])["status"] == "confirmed"


def test_conflict_marker_blocks_only_windows_containing_its_trading_date():
    rows = prices([106] * 10)
    rows[6]["ohlc_conflict_fields"] = ["high"]
    assert diagnostic(candidate(), rows, window=5)["status"] == "confirmed"
    result = diagnostic(candidate(), rows, window=10)
    assert result["status"] == "insufficient_data"
    assert result["missing_reason"] == "candidate_history_gap_or_invalid_ohlc"


def test_reference_date_mismatch_and_future_rows_are_not_used():
    from ai_stock_sentinel.daily_radar.observation_validation import evaluate_observation
    c = candidate()
    c["data_dates"]["ohlcv"] = "2026-05-29"
    assert evaluate(c, prices([106] * 5))["skip_reason"] == "stale_candidate_price"
    assert evaluate_observation(c, price_series=prices([106] * 5), benchmark_prices=prices([100] * 5),
                                window_days=5, as_of_date=date(2026, 6, 8))["missing_reason"] == "reference_date_unknown_or_stale"
    result = evaluate_observation(candidate(), price_series=prices([104] * 5 + [106, 107]),
                                  benchmark_prices=prices([100] * 7), window_days=5,
                                  as_of_date=date(2026, 6, 8))
    assert result["status"] == "unconfirmed"


def test_waiting_drawdown_is_zero_when_all_future_lows_are_above_signal_close():
    assert diagnostic(candidate(), prices([106] * 5))["waiting_max_adverse_excursion_pct"] == 0


def test_stale_technical_reference_does_not_count_as_an_observation_failure():
    c = candidate()
    c["data_dates"]["technical_indicators"] = "2026-05-29"
    result = diagnostic(c, prices([106] * 5))
    assert result["status"] == "insufficient_data"
    assert result["missing_reason"] == "reference_date_unknown_or_stale"


def test_strategy_groups_keep_original_daily_ranks_across_versions():
    candidates = [candidate(i, score=100 - i) for i in range(1, 5)]
    candidates[3]["input_snapshot"]["selection_version"] = "selection-v2"
    report = build_forward_validation_report_from_outcomes(
        candidates, [evaluate(c, prices([106] * 5)) for c in candidates],
        market="TW", sample_source="test", as_of_date=SIGNAL + timedelta(days=20), windows=[5],
    )["observation_diagnostics"]
    cohort = next(c for c in report["cohorts"] if c["strategy"]["selection_version"] == "selection-v2")
    assert cohort["windows"]["5"]["top_3"]["candidate_ids"] == []
    assert cohort["windows"]["5"]["remaining_after_3"]["candidate_ids"] == [4]
