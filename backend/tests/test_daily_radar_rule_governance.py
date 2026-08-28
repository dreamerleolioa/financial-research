from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.daily_radar import rule_governance as rule_governance_module
from ai_stock_sentinel.daily_radar.forward_validation import (
    FORWARD_VALIDATION_VERSION,
    build_forward_validation_report,
    forward_validation_fixture_inputs,
)
from ai_stock_sentinel.calibration.governance import (
    block_bootstrap_delta,
    block_bootstrap_mean_delta,
)
from ai_stock_sentinel.daily_radar.rule_governance import (
    DEFAULT_ABLATION_GROUPS,
    _replay_daily_radar_rows,
    build_ablation_report,
    build_monthly_rule_review_report,
    validation_rows_from_results,
)
from ai_stock_sentinel.daily_radar.rule_registry import (
    SCORING_ACTIVE_TIERS,
    assert_rule_can_affect_score,
    get_rule_registry,
)
from ai_stock_sentinel.daily_radar.scoring import (
    RULE_SCORE_ADJUSTMENTS,
    RULE_SIGNAL_FAMILIES,
    RULE_VERSION,
    SCORING_VERSION,
    ScoringConfig,
    score_daily_radar_record,
)
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarRun,
)
from ai_stock_sentinel.db.session import Base, get_db


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


ROOT = Path(__file__).parents[1]
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"


def test_rule_registry_covers_every_scoring_rule_and_active_score_driver() -> None:
    registry = get_rule_registry()
    scoring_source = (ROOT / "src" / "ai_stock_sentinel" / "daily_radar" / "scoring.py").read_text(
        encoding="utf-8"
    )
    scoring_rule_codes = set(re.findall(r'_rule\("([^"]+)"', scoring_source))

    assert scoring_rule_codes
    assert scoring_rule_codes <= set(registry)
    assert scoring_rule_codes == set(RULE_SCORE_ADJUSTMENTS)
    for code in scoring_rule_codes:
        assert registry[code].tier in SCORING_ACTIVE_TIERS
        assert registry[code].owner_module == "daily_radar.scoring"
        assert registry[code].first_version
        assert registry[code].last_reviewed_version == RULE_VERSION
        assert registry[code].signal_family == RULE_SIGNAL_FAMILIES.get(code)

    assert RULE_SIGNAL_FAMILIES
    assert set(RULE_SIGNAL_FAMILIES) <= scoring_rule_codes

    for label in ("overextended", "flow_conflict", "margin_crowding", "market_weakness", "data_gap"):
        assert f"risk_label_{label}" in registry


def test_context_only_rules_are_governed_out_of_live_scoring() -> None:
    registry = get_rule_registry()
    context_only_codes = {
        code for code, entry in registry.items()
        if entry.tier in {"context_only", "deprecated"}
    }
    scoring_source = (ROOT / "src" / "ai_stock_sentinel" / "daily_radar" / "scoring.py").read_text(
        encoding="utf-8"
    )

    assert {"news_sentiment_context", "fundamental_valuation_context"} <= context_only_codes
    for code in context_only_codes:
        assert f'_rule("{code}"' not in scoring_source
        with pytest.raises(ValueError):
            assert_rule_can_affect_score(code)


def test_ablation_report_fixture_is_deterministic_and_marks_low_samples() -> None:
    candidates, prices_by_symbol, benchmark_prices, benchmark_symbol = forward_validation_fixture_inputs(
        fixture_dir=FIXTURE_DIR,
        run_date=date(2026, 5, 29),
        market="TW",
    )
    evaluation = build_forward_validation_report(
        candidates,
        price_series_by_symbol=prices_by_symbol,
        benchmark_prices=benchmark_prices,
        market="TW",
        sample_source="fixture",
        as_of_date=date(2026, 6, 26),
        windows=[5, 10, 20],
        benchmark_symbol=benchmark_symbol,
    )

    first = build_ablation_report(
        evaluation.outcomes,
        market="TW",
        sample_source="fixture",
        min_sample_count=20,
    )
    second = build_ablation_report(
        json.loads(json.dumps(evaluation.outcomes, ensure_ascii=False)),
        market="TW",
        sample_source="fixture",
        min_sample_count=20,
    )

    assert first == second
    assert {row["group"] for row in first["ablation_groups"]} == set(DEFAULT_ABLATION_GROUPS)
    assert first["metadata"]["method"] == "co_occurrence_not_counterfactual"
    assert first["metadata"]["positioning"] == "rule_quality_co_occurrence_diagnostic_not_live_scoring_change"
    assert first["sample_summary"]["validated_by_window"] == {"5": 4, "10": 4, "20": 4}
    assert first["insufficient_sample_cases"]
    assert first["version_manifest"]["live_scoring_changed"] is False
    assert first["version_manifest"]["live_ranking_changed"] is False
    json.dumps(first, ensure_ascii=False, sort_keys=True)


def test_monthly_rule_review_api_uses_persisted_validation_results_and_returns_artifacts(monkeypatch) -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        _add_validation_result(session, candidate)
        session.commit()

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/rule-review/monthly",
            json={"market": "TW", "year": 2026, "month": 6, "min_sample_count": 1},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["month"] == "2026-06"
    assert payload["report_json"]["metadata"]["sample_source"] == "production_db"
    assert payload["report_json"]["sample_summary"]["validated_sample_count"] == 1
    assert payload["report_json"]["human_approval_boundary"] == {
        "automated_report": True,
        "updates_live_scoring": False,
        "requires_human_approved_versioned_strategy_update": True,
    }
    assert "# Daily Radar Rule Review 2026-06" in payload["report_markdown"]


def test_monthly_rule_review_report_keeps_scoring_versions_unchanged() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        _add_validation_result(session, candidate)
        report = build_monthly_rule_review_report(
            session,
            market="TW",
            year=2026,
            month=6,
            min_sample_count=1,
        )

    manifest = report.json_report["version_manifest"]
    assert (
        report.json_report["metadata"]["report_version"]
        == "daily-radar-rule-review-v6"
    )
    assert manifest["scoring_version"] == SCORING_VERSION
    assert manifest["rule_version"] == RULE_VERSION
    assert manifest["live_scoring_changed"] is False
    assert manifest["automated_recommendations_only"] is True
    assert report.json_report["baseline_config"]["primary_bucket_weight"] == 0.8
    assert report.json_report["auto_change_eligible"] is False
    assert report.json_report["counterfactual_ablation_summary"] == report.json_report["ablation_summary"]
    assert report.json_report["co_occurrence_summary"]
    assert {
        row["method"]
        for row in report.json_report["counterfactual_ablation_summary"]
    } == {"not_applicable_no_cohort", "not_applicable_context_only"}
    context_only_ablation = [
        row
        for row in report.json_report["counterfactual_ablation_summary"]
        if row["group"] in {"news_sentiment", "fundamental_valuation"}
    ]
    assert context_only_ablation
    assert {
        row["recommendation"] for row in context_only_ablation
    } == {"not_in_live_score"}
    assert all(
        row["eligible_for_live_change"] is False
        for row in report.json_report["counterfactual_ablation_summary"]
    )
    counterfactual_scope = report.json_report["counterfactual_ablation_scope"]
    assert {
        key: value
        for key, value in counterfactual_scope.items()
        if key != "replay_workload"
    } == {
        "selected_months": [],
        "ranking_pool": "all_latest_run_candidates_for_each_forward_window",
        "ranking_pool_complete": False,
        "ranking_pool_status": "not_applicable",
        "outcome_join": "validated_results_only_after_selection",
        "bucket_cohort": "baseline_primary_bucket_anchor",
        "live_change_eligible": False,
    }
    assert counterfactual_scope["replay_workload"]["candidate_count"] == 0
    assert (
        counterfactual_scope["replay_workload"]["capacity_exceeded"]
        is False
    )
    assert {
        row["recommendation"]
        for row in report.json_report["counterfactual_ablation_summary"]
        if row["method"] == "not_applicable_no_cohort"
    } == {"not_applicable_no_cohort"}
    candidate_parameters = {
        candidate["parameter"]
        for candidate in report.json_report["candidate_configs"]
    }
    assert {
        "primary_bucket_weight",
        "cross_confirmation_weight",
        "market_context_weight",
        "freshness_weight",
        "relative_strength_weight",
        "secondary_bucket_threshold",
    } == candidate_parameters
    assert not candidate_parameters & {
        "overextended_penalty",
        "data_gap_penalty",
        "rule_scores",
        "prefilter_rules",
    }
    assert all(
        sum(
            value != report.json_report["baseline_config"][key]
            for key, value in candidate["candidate_config"].items()
        ) == 1
        for candidate in report.json_report["candidate_configs"]
    )
    assert all(
        candidate["step"] > 0
        for candidate in report.json_report["candidate_configs"]
        if candidate["parameter"] in {"market_context_weight", "freshness_weight"}
    )
    threshold_candidates = [
        candidate
        for candidate in report.json_report["candidate_configs"]
        if candidate["parameter"] == "secondary_bucket_threshold"
    ]
    assert threshold_candidates
    assert {
        candidate["holdout_primary_metric"]["metric"]
        for candidate in threshold_candidates
    } == {"secondary_bucket_average_excess_return_pct"}
    assert all(
        set(candidate["holdout_horizon_gates"]) == {"5", "10", "20"}
        for candidate in report.json_report["candidate_configs"]
    )


def test_replay_ranks_full_candidate_pool_before_joining_validated_outcomes() -> None:
    rows = []
    for candidate_id in range(1, 22):
        positive_days = 5 if candidate_id == 1 else max(0, 5 - candidate_id // 5)
        rows.append({
            "candidate_id": candidate_id,
            "symbol": f"{candidate_id:04d}.TW",
            "signal_date": "2026-06-01",
            "window_days": 5,
            "status": "skipped" if candidate_id == 1 else "validated",
            "outcome": {} if candidate_id == 1 else {
                "excess_return_vs_benchmark_pct": float(candidate_id),
                "max_adverse_excursion_pct": -1.0,
            },
            "candidate_snapshot": {
                "record_date": "2026-06-01",
                "input_snapshot": {
                    "replay_input": {
                        "schema_version": "daily-radar-replay-input-v2",
                        "record": _replay_record(
                            f"{candidate_id:04d}.TW",
                            positive_days=positive_days,
                        ),
                        "market_context": {},
                        "prefilter_result": {},
                    }
                },
            },
        })

    replayed = _replay_daily_radar_rows(rows, ScoringConfig())
    by_id = {row["candidate_id"]: row for row in replayed}

    assert by_id[1]["selected_for_rank"] is True
    assert sum(row["selected_for_rank"] is True for row in replayed) == 20
    assert by_id[21]["selected_for_rank"] is False


def test_replay_scores_each_candidate_once_across_outcome_windows(monkeypatch) -> None:
    rows = []
    for window in (5, 10, 20):
        row = _governance_replay_row(1, with_replay_input=True)
        row["window_days"] = window
        rows.append(row)
    calls = 0
    original = rule_governance_module.score_daily_radar_record

    def score_spy(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        rule_governance_module,
        "score_daily_radar_record",
        score_spy,
    )

    replayed = _replay_daily_radar_rows(rows, ScoringConfig())

    assert calls == 1
    assert {row["window_days"] for row in replayed} == {5, 10, 20}


def test_incomplete_replay_pool_cannot_govern_ranked_changes_at_90_percent() -> None:
    rows = [
        _governance_replay_row(
            candidate_id,
            with_replay_input=candidate_id > 10,
        )
        for candidate_id in range(1, 101)
    ]
    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": True,
    }
    context = rule_governance_module._prepare_daily_radar_replay_context(
        rows,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.9,
    )
    candidates, coverage = rule_governance_module._scoring_candidate_reports(
        context,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        watermarks=[{
            "month": "2026-06",
            "validated_coverage_by_window": {
                "5": 1.0,
                "10": 1.0,
                "20": 1.0,
            },
        }],
        min_sample_count=1,
        min_validated_coverage=0.9,
    )
    counterfactual = rule_governance_module._counterfactual_ablation_report(
        context,
        min_sample_count=1,
    )

    assert coverage["coverage"] == 0.9
    assert coverage["meets_threshold"] is True
    assert coverage["ranking_pool_complete"] is False
    assert coverage["ranking_pool_status"] == "incomplete"
    assert coverage["incomplete_ranking_groups"] == [{
        "signal_date": "2026-06-01",
        "window_days": 5,
        "candidate_count": 100,
        "replayable_candidate_count": 90,
        "missing_candidate_count": 10,
        "maximum_candidate_count": 250,
        "capacity_exceeded": False,
    }]
    assert {
        candidate["eligibility_reason"] for candidate in candidates
    } == {"replay_ranking_pool_incomplete"}
    assert {
        row["recommendation"]
        for row in counterfactual
        if row["method"] == "same_input_counterfactual_replay"
    } == {"replay_ranking_pool_incomplete"}


def test_bucket_counterfactual_uses_stable_baseline_bucket_cohort() -> None:
    def row(
        candidate_id: int,
        *,
        selected: bool,
        excess: float,
        replayed_bucket: str,
    ) -> dict[str, Any]:
        return {
            "candidate_id": candidate_id,
            "status": "validated",
            "selected_for_rank": selected,
            "baseline_primary_bucket": "bottoming_reversal",
            "replayed_primary_bucket": replayed_bucket,
            "outcome": {
                "forward_return_pct": excess,
                "excess_return_vs_benchmark_pct": excess,
                "max_adverse_excursion_pct": -1.0,
            },
        }

    before = [
        row(1, selected=True, excess=1.0, replayed_bucket="bottoming_reversal"),
        row(2, selected=False, excess=3.0, replayed_bucket="bottoming_reversal"),
    ]
    after = [
        row(1, selected=False, excess=1.0, replayed_bucket="support_retest"),
        row(2, selected=True, excess=3.0, replayed_bucket="bottoming_reversal"),
    ]

    impacts = rule_governance_module._bucket_counterfactual_impacts(
        before,
        {"bottoming_reversal": after},
        excluded_rule_codes_by_bucket={
            "bottoming_reversal": {"bottoming_macd_improving"},
        },
        min_sample_count=1,
    )
    bottoming = impacts["bottoming_reversal"]

    assert bottoming["cohort"] == "baseline_primary_bucket_anchor"
    assert bottoming["intervention_scope"] == "bucket_local_owned_rules_removal"
    assert bottoming["excluded_rule_codes"] == ["bottoming_macd_improving"]
    assert bottoming["selection_membership_changed_count"] == 2
    assert bottoming["primary_bucket_changed_count"] == 1
    assert bottoming["delta_average_excess_return_vs_benchmark_pct"] == 2.0
    assert bottoming["recommendation"] == "review_group_removal_for_bucket"
    assert impacts["support_retest"]["recommendation"] == (
        "not_applicable_no_bucket_owned_rules"
    )


def test_partial_v1_replay_payload_is_not_ranking_eligible() -> None:
    complete = _governance_replay_row(1, with_replay_input=True)
    partial = _governance_replay_row(2, with_replay_input=True)
    partial["candidate_snapshot"]["input_snapshot"]["replay_input"] = {
        "schema_version": "daily-radar-replay-input-v1",
        "record": {
            "symbol": "0002.TW",
            "name": "0002.TW",
            "record_date": "2026-06-01",
            "ohlcv": {},
            "indicators": {},
            "technical_profile": {},
            "price_history": [],
            "institutional_flow": {},
            "margin": {},
            "data_dates": {},
        },
        "market_context": {"market": {}},
        "prefilter_result": {
            "prefilter_status": "accepted",
            "prefilter_reasons": [],
        },
        "baseline_config": ScoringConfig().to_dict(),
    }
    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": True,
    }

    context = rule_governance_module._prepare_daily_radar_replay_context(
        [complete, partial],
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.5,
    )

    assert context.replay_coverage["coverage"] == 0.5
    assert context.ranking_pool_status == "incomplete"
    assert context.exclusion_reasons == {"replay_input_incomplete": 1}
    assert [row["candidate_id"] for row in context.eligible_rows] == [1]


def test_replay_input_requires_validation_signal_date_to_match_candidate_snapshot() -> None:
    row = _governance_replay_row(1, with_replay_input=True)
    row["signal_date"] = "2026-06-02"
    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": True,
    }

    context = rule_governance_module._prepare_daily_radar_replay_context(
        [row],
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.9,
    )

    assert context.eligible_rows == []
    assert context.baseline_rows == []
    assert context.ranking_pool_status == "incomplete"
    assert context.exclusion_reasons == {"replay_input_incomplete": 1}


@pytest.mark.parametrize(
    "future_date_source",
    [
        "core_data_date",
        "price_history",
        "market_context_data_date",
        "market_data_date",
        "benchmark_data_date",
        "benchmark_price_history",
    ],
)
def test_replay_input_rejects_dates_after_the_candidate_record_date(
    future_date_source: str,
) -> None:
    row = _governance_replay_row(1, with_replay_input=True)
    replay_input = row["candidate_snapshot"]["input_snapshot"]["replay_input"]
    record = replay_input["record"]
    market_context = replay_input["market_context"]
    if future_date_source == "core_data_date":
        record["data_dates"]["ohlcv"] = "2026-06-02"
    elif future_date_source == "price_history":
        record["price_history"].append(
            {"date": "2026-06-02", "close": 102.0}
        )
    elif future_date_source == "market_context_data_date":
        market_context["data_dates"]["market_index"] = "2026-06-02"
    elif future_date_source == "market_data_date":
        market_context["market"]["data_date"] = "2026-06-02"
    elif future_date_source == "benchmark_data_date":
        market_context["benchmark"] = {
            "data_dates": {"market_index": "2026-06-02"}
        }
    else:
        market_context["benchmark"] = {
            "price_history": [
                {"date": "2026-06-02", "close": 1000.0}
            ]
        }

    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": True,
    }
    context = rule_governance_module._prepare_daily_radar_replay_context(
        [row],
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.9,
    )

    assert context.eligible_rows == []
    assert context.ranking_pool_status == "incomplete"
    assert context.exclusion_reasons == {"replay_input_incomplete": 1}


@pytest.mark.parametrize(
    ("field", "mismatched_value"),
    [
        ("observation_score", -1),
        ("primary_bucket", "mismatched_bucket"),
        ("secondary_buckets", ["mismatched_bucket"]),
        ("bucket_scores", {"mismatched_bucket": -1}),
        ("risk_labels", ["mismatched_risk"]),
        ("matched_rule_codes", ["mismatched_rule"]),
    ],
)
def test_baseline_replay_mismatch_invalidates_ranking_pool(
    field: str,
    mismatched_value: Any,
) -> None:
    row = _governance_replay_row(1, with_replay_input=True)
    row["candidate_snapshot"][field] = mismatched_value
    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": True,
    }

    context = rule_governance_module._prepare_daily_radar_replay_context(
        [row],
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.9,
    )

    assert context.ranking_pool_status == "incomplete"
    assert context.ranking_pool_complete is False
    assert context.eligible_rows == []
    assert context.baseline_rows == []
    assert context.replay_coverage["coverage"] == 0.0
    assert context.exclusion_reasons == {"baseline_replay_mismatch": 1}


def test_replay_governance_rejects_candidate_groups_over_capacity(
    monkeypatch,
) -> None:
    rows = [
        _governance_replay_row(candidate_id, with_replay_input=True)
        for candidate_id in range(1, 252)
    ]
    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": True,
    }

    def unexpected_scoring(*args, **kwargs):
        raise AssertionError("capacity guard must run before replay scoring")

    monkeypatch.setattr(
        rule_governance_module,
        "score_daily_radar_record",
        unexpected_scoring,
    )
    context = rule_governance_module._prepare_daily_radar_replay_context(
        rows,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.9,
    )
    candidates, coverage = rule_governance_module._scoring_candidate_reports(
        context,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        watermarks=[],
        min_sample_count=1,
        min_validated_coverage=0.9,
    )

    assert context.ranking_pool_status == "capacity_exceeded"
    assert context.baseline_rows == []
    assert coverage["incomplete_ranking_groups"] == [{
        "signal_date": "2026-06-01",
        "window_days": 5,
        "candidate_count": 251,
        "replayable_candidate_count": 251,
        "missing_candidate_count": 0,
        "maximum_candidate_count": 250,
        "capacity_exceeded": True,
    }]
    assert {
        candidate["eligibility_reason"] for candidate in candidates
    } == {"replay_workload_limit_exceeded"}


def test_replay_governance_rejects_aggregate_workload_before_scoring(
    monkeypatch,
) -> None:
    rows = [
        _governance_replay_row(candidate_id, with_replay_input=True)
        for candidate_id in range(1, 3)
    ]
    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": True,
    }

    def unexpected_scoring(*args, **kwargs):
        raise AssertionError("aggregate workload guard must run before scoring")

    monkeypatch.setattr(
        rule_governance_module,
        "MAX_GOVERNANCE_BOOTSTRAP_ROW_ITERATIONS",
        1,
    )
    monkeypatch.setattr(
        rule_governance_module,
        "score_daily_radar_record",
        unexpected_scoring,
    )

    context = rule_governance_module._prepare_daily_radar_replay_context(
        rows,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.9,
    )
    candidates, coverage = rule_governance_module._scoring_candidate_reports(
        context,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        watermarks=[],
        min_sample_count=1,
        min_validated_coverage=0.9,
    )

    workload = coverage["replay_workload"]
    assert context.ranking_pool_status == "capacity_exceeded"
    assert context.baseline_rows == []
    assert workload["candidate_count"] == 2
    assert workload["candidate_window_row_count"] == 2
    assert workload["estimated_bootstrap_row_iterations"] == 10_000
    assert workload["maximum_bootstrap_row_iterations"] == 1
    assert workload["capacity_exceeded"] is True
    assert workload["exceeded_limits"] == ["bootstrap_row_iterations"]
    assert {
        candidate["eligibility_reason"] for candidate in candidates
    } == {"replay_workload_limit_exceeded"}


def test_replay_workload_counts_bucket_local_ablation_passes_at_limit() -> None:
    maximum = rule_governance_module.MAX_GOVERNANCE_REPLAY_SCORING_CALLS
    expected_passes = (
        1
        + rule_governance_module.SCORING_CANDIDATE_CONFIG_COUNT
        + 8
        + 16
    )
    maximum_candidates = maximum // expected_passes

    at_limit = rule_governance_module._daily_radar_replay_workload_from_counts(
        candidate_count=maximum_candidates,
        candidate_window_row_count=0,
    )
    over_limit = rule_governance_module._daily_radar_replay_workload_from_counts(
        candidate_count=maximum_candidates + 1,
        candidate_window_row_count=0,
    )

    assert at_limit["active_ablation_group_count"] == 8
    assert at_limit["active_bucket_ablation_count"] == 16
    assert at_limit["scoring_pass_count"] == expected_passes
    assert at_limit["capacity_exceeded"] is False
    assert over_limit["capacity_exceeded"] is True
    assert over_limit["exceeded_limits"] == ["replay_scoring_calls"]


@pytest.mark.parametrize(
    "parameter",
    ["primary_bucket_weight", "secondary_bucket_threshold"],
)
def test_candidate_bootstrap_uses_selected_rows_and_preserves_date_blocks(
    monkeypatch,
    parameter: str,
) -> None:
    baseline_rows = [
        {
            "candidate_id": 1,
            "record_date": "2026-01-05",
            "window_days": 5,
            "status": "validated",
            "selected_for_rank": True,
            "selected_for_secondary": True,
            "outcome": {
                "excess_return_vs_benchmark_pct": 1.0,
                "max_adverse_excursion_pct": -1.0,
            },
        },
        {
            "candidate_id": 2,
            "record_date": "2026-01-05",
            "window_days": 5,
            "status": "validated",
            "selected_for_rank": False,
            "selected_for_secondary": False,
            "outcome": {
                "excess_return_vs_benchmark_pct": 0.0,
                "max_adverse_excursion_pct": -1.0,
            },
        },
        {
            "candidate_id": 3,
            "record_date": "2026-01-06",
            "window_days": 5,
            "status": "missing",
            "selected_for_rank": True,
            "selected_for_secondary": True,
            "outcome": {},
        },
    ]
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        rule_governance_module,
        "_replay_daily_radar_rows",
        lambda rows, config, **kwargs: list(baseline_rows),
    )

    def bootstrap_spy(
        before_rows,
        after_rows,
        *,
        value,
        block_key,
        block_values=None,
        **kwargs,
    ):
        captured["before_candidate_ids"] = [
            row["candidate_id"] for row in before_rows
        ]
        captured["after_candidate_ids"] = [
            row["candidate_id"] for row in after_rows
        ]
        captured["block_values"] = list(block_values or [])
        return {
            "seed": 20260724,
            "iterations": 500,
            "block_count": len(captured["block_values"]),
            "delta": 0.0,
            "ci_95": [0.0, 0.0],
        }

    monkeypatch.setattr(
        rule_governance_module,
        "block_bootstrap_mean_delta",
        bootstrap_spy,
    )
    rule_governance_module._scoring_candidate_report(
        [],
        baseline_rows=baseline_rows,
        baseline_config=ScoringConfig(),
        parameter=parameter,
        direction=1,
        step=rule_governance_module.TUNABLE_SCORING_PARAMETERS[parameter],
        cohort={
            "selected_months": ["2026-01", "2026-02"],
            "training_months": ["2026-01"],
            "holdout_month": "2026-02",
            "cohort_complete": True,
        },
        watermarks=[{
            "month": "2026-01",
            "validated_coverage_by_window": {
                "5": 1.0,
                "10": 1.0,
                "20": 1.0,
            },
        }],
        min_sample_count=1,
        min_validated_coverage=0.9,
        replay_coverage_ok=True,
        replay_ranking_pool_complete=True,
        replay_ranking_pool_status="complete",
    )

    assert captured["before_candidate_ids"] == [1]
    assert captured["after_candidate_ids"] == [1]
    assert captured["block_values"] == ["2026-01-05", "2026-01-06"]


def test_block_bootstrap_preserves_explicit_blocks_without_selected_rows() -> None:
    rows = [{"record_date": "2026-01-05", "value": 1.0}]

    result = block_bootstrap_delta(
        rows,
        rows,
        metric=lambda sample: (
            sum(float(row["value"]) for row in sample) / len(sample)
            if sample
            else None
        ),
        block_key="record_date",
        block_values=["2026-01-05", "2026-01-06"],
        iterations=20,
    )

    assert result["block_count"] == 2
    assert result["delta"] == 0.0


def test_mean_bootstrap_matches_generic_bootstrap_without_row_rebuilds() -> None:
    before = [
        {"record_date": "2026-01-01", "value": 0.8214394288679769},
        {"record_date": "2026-01-02", "value": -1.972180397600749},
        {"record_date": "2026-01-03", "value": -0.1820005504515918},
    ]
    after = [
        {"record_date": "2026-01-01", "value": 0.8215793061016093},
        {"record_date": "2026-01-02", "value": -1.972220498231419},
        {"record_date": "2026-01-03", "value": -0.18186045374162296},
    ]
    blocks = ["2026-01-01", "2026-01-02", "2026-01-03"]
    metric = lambda rows: (
        round(
            sum(float(row["value"]) for row in rows) / len(rows),
            4,
        )
        if rows
        else None
    )

    generic = block_bootstrap_delta(
        before,
        after,
        metric=metric,
        block_key="record_date",
        block_values=blocks,
        iterations=500,
    )
    optimized = block_bootstrap_mean_delta(
        before,
        after,
        value=lambda row: float(row["value"]),
        block_key="record_date",
        block_values=blocks,
        iterations=500,
    )

    assert generic == {
        "seed": 20260724,
        "iterations": 500,
        "block_count": 3,
        "delta": 0.0,
        "ci_95": [0.0, 0.0002],
    }
    assert optimized == generic


def test_monthly_governance_reuses_one_baseline_replay(monkeypatch) -> None:
    rows = [_governance_replay_row(1, with_replay_input=True)]
    cohort = {
        "selected_months": ["2026-06"],
        "training_months": [],
        "holdout_month": "2026-06",
        "cohort_complete": False,
    }
    calls: list[tuple[ScoringConfig, frozenset[str]]] = []
    original = rule_governance_module._replay_daily_radar_rows

    def replay_spy(rows, config, *, excluded_rule_codes=None):
        calls.append((config, frozenset(excluded_rule_codes or ())))
        return original(
            rows,
            config,
            excluded_rule_codes=excluded_rule_codes,
        )

    monkeypatch.setattr(
        rule_governance_module,
        "_replay_daily_radar_rows",
        replay_spy,
    )
    context = rule_governance_module._prepare_daily_radar_replay_context(
        rows,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=0.9,
    )
    rule_governance_module._scoring_candidate_reports(
        context,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        watermarks=[],
        min_sample_count=1,
        min_validated_coverage=0.9,
    )
    rule_governance_module._counterfactual_ablation_report(
        context,
        min_sample_count=1,
    )

    assert sum(
        config == ScoringConfig() and not excluded_codes
        for config, excluded_codes in calls
    ) == 1


def test_monthly_review_does_not_fall_back_to_validated_older_same_day_run() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        old_run = _add_run(session)
        old_run.created_at = datetime(2026, 6, 1, 10, tzinfo=timezone.utc)
        old_candidate = _add_candidate(session, old_run)
        _add_validation_result(session, old_candidate)
        latest_run = _add_run(session)
        latest_run.created_at = datetime(2026, 6, 2, 1, tzinfo=timezone.utc)
        _add_candidate(session, latest_run)
        session.commit()

        rows = validation_rows_from_results(
            session,
            market="TW",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
        )

    assert len(rows) == 3
    assert {row["window_days"] for row in rows} == {5, 10, 20}
    assert {row["status"] for row in rows} == {"missing"}
    assert {row["skip_reason"] for row in rows} == {
        "validation_result_missing"
    }
    assert {row["candidate_id"] for row in rows} == {2}


def test_month_is_not_mature_when_shorter_window_results_are_missing() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        session.add(
            DailyRadarForwardValidationResult(
                candidate_id=candidate.id,
                window_days=20,
                validation_version=FORWARD_VALIDATION_VERSION,
                status="validated",
                signal_date=run.run_date,
                target_date=date(2026, 6, 29),
                benchmark_symbol="TAIEX",
                outcome={
                    "forward_return_pct": 10.0,
                    "excess_return_vs_benchmark_pct": 8.0,
                    "hit_above_threshold": True,
                },
                skip_reason=None,
            )
        )
        session.commit()

        payload = build_monthly_rule_review_report(
            session,
            market="TW",
            year=2026,
            month=6,
            min_sample_count=1,
        ).json_report

    watermark = payload["completeness_watermarks"][0]
    assert watermark["maturity_complete"] is False
    assert watermark["evaluated_samples_by_window"] == {
        "5": 0,
        "10": 0,
        "20": 1,
    }
    assert watermark["validated_coverage_by_window"] == {
        "5": 0.0,
        "10": 0.0,
        "20": 1.0,
    }
    assert payload["cohort"]["selected_months"] == []
    assert payload["sample_summary"]["missing_by_window"] == {
        "5": 1,
        "10": 1,
        "20": 0,
    }


def test_daily_radar_monthly_report_aggregates_watermarks_and_bounds_detail_to_six_months() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run_months = [
            date(2025 + offset // 12, offset % 12 + 1, 1)
            for offset in range(19)
        ]
        for index, run_date in enumerate(run_months, start=1):
            run = _add_run(session)
            run.run_date = run_date
            run.created_at = datetime(
                run_date.year,
                run_date.month,
                1,
                tzinfo=timezone.utc,
            )
            candidate = _add_candidate(session, run)
            candidate.symbol = f"24{index:02d}.TW"
            candidate.data_dates = {"ohlcv": run.run_date.isoformat()}
            for window in (5, 10, 20):
                session.add(
                    DailyRadarForwardValidationResult(
                        candidate_id=candidate.id,
                        window_days=window,
                        validation_version=FORWARD_VALIDATION_VERSION,
                        status="validated",
                        signal_date=run.run_date,
                        target_date=date(
                            run_date.year,
                            run_date.month,
                            min(25, 1 + window),
                        ),
                        benchmark_symbol="TAIEX",
                        outcome={
                            "forward_return_pct": float(index),
                            "excess_return_vs_benchmark_pct": float(index),
                            "max_adverse_excursion_pct": -1.0,
                            "hit_above_threshold": True,
                        },
                        skip_reason=None,
                    )
                )
        session.commit()

    statements: list[tuple[str, object]] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        started_at = perf_counter()
        with Session(engine) as session:
            report = build_monthly_rule_review_report(
                session,
                market="TW",
                year=2026,
                month=7,
                min_sample_count=1,
            )
        elapsed_seconds = perf_counter() - started_at
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    payload = report.json_report
    assert payload["cohort"]["selected_months"] == [
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]
    assert payload["replay_coverage"]["selected_validation_rows"] == 18
    assert payload["replay_coverage"]["selected_samples"] == 6
    assert payload["replay_coverage"]["coverage"] == 0.0
    assert payload["replay_coverage"]["meets_threshold"] is False
    assert payload["replay_coverage"]["exclusion_reasons"] == {
        "replay_input_incomplete": 6,
    }
    assert all(
        candidate["eligibility_reason"] == "replay_coverage_below_threshold"
        for candidate in payload["candidate_configs"]
    )
    assert len(payload["completeness_watermarks"]) == 19
    assert elapsed_seconds < 5.0
    select_statements = [
        (statement, parameters)
        for statement, parameters in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(select_statements) == 4
    assert any(
        "GROUP BY" in statement
        and "daily_radar_forward_validation_results" in statement
        for statement, _parameters in select_statements
    )
    assert all(
        "2000-01-01" not in str(parameters)
        for _statement, parameters in select_statements
    )
    detail_statements = [
        statement
        for statement, _parameters in select_statements
        if "GROUP BY" not in statement
        and "count(daily_radar_candidates.id)" not in statement.lower()
    ]
    assert detail_statements
    assert all(
        "daily_radar_runs.run_date >= ?" in statement
        for statement in detail_statements
    )


def test_monthly_report_checks_capacity_before_loading_optimizer_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        for month in range(1, 7):
            run_date = date(2026, month, 1)
            run = _add_run(session)
            run.run_date = run_date
            run.created_at = datetime(
                2026,
                month,
                1,
                tzinfo=timezone.utc,
            )
            candidate = _add_candidate(session, run)
            candidate.data_dates = {"ohlcv": run_date.isoformat()}
            for window in (5, 10, 20):
                session.add(
                    DailyRadarForwardValidationResult(
                        candidate_id=candidate.id,
                        window_days=window,
                        validation_version=FORWARD_VALIDATION_VERSION,
                        status="validated",
                        signal_date=run_date,
                        target_date=run_date,
                        benchmark_symbol="TAIEX",
                        outcome={
                            "forward_return_pct": 1.0,
                            "excess_return_vs_benchmark_pct": 1.0,
                            "max_adverse_excursion_pct": -1.0,
                            "hit_above_threshold": True,
                        },
                        skip_reason=None,
                    )
                )
        session.commit()

        original_loader = rule_governance_module.validation_rows_from_results

        def guarded_loader(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            if kwargs.get("selected_months") is not None:
                raise AssertionError("optimizer JSON snapshots were loaded")
            return original_loader(*args, **kwargs)

        monkeypatch.setattr(
            rule_governance_module,
            "MAX_GOVERNANCE_BOOTSTRAP_ROW_ITERATIONS",
            1,
        )
        monkeypatch.setattr(
            rule_governance_module,
            "validation_rows_from_results",
            guarded_loader,
        )

        report = build_monthly_rule_review_report(
            session,
            market="TW",
            year=2026,
            month=6,
            min_sample_count=1,
        ).json_report

    assert report["replay_coverage"]["ranking_pool_status"] == (
        "capacity_exceeded"
    )
    assert report["replay_coverage"]["replay_workload"][
        "capacity_exceeded"
    ] is True


def test_daily_radar_monthly_report_defaults_to_current_validation_version() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        _add_validation_result(session, candidate)
        session.add(
            DailyRadarForwardValidationResult(
                candidate_id=candidate.id,
                window_days=5,
                validation_version="daily-radar-forward-validation-v1",
                status="validated",
                signal_date=run.run_date,
                target_date=date(2026, 6, 8),
                benchmark_symbol="TAIEX",
                outcome={
                    "forward_return_pct": 999.0,
                    "excess_return_vs_benchmark_pct": 999.0,
                    "hit_above_threshold": True,
                },
                skip_reason=None,
            )
        )
        session.commit()

        report = build_monthly_rule_review_report(
            session,
            market="TW",
            year=2026,
            month=6,
            min_sample_count=1,
        ).json_report

    assert report["metadata"]["validation_version"] == FORWARD_VALIDATION_VERSION
    assert report["sample_summary"]["evaluated_sample_count"] == 1
    assert report["sample_summary"]["validated_sample_count"] == 1


def test_monthly_report_excludes_benchmark_identity_mismatch() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        candidate.input_snapshot = {
            **candidate.input_snapshot,
            "replay_input": {
                "market_context": {
                    "benchmark": {"symbol": "TAIEX"},
                }
            },
        }
        session.add(
            DailyRadarForwardValidationResult(
                candidate_id=candidate.id,
                window_days=5,
                validation_version=FORWARD_VALIDATION_VERSION,
                status="validated",
                signal_date=run.run_date,
                target_date=date(2026, 6, 8),
                benchmark_symbol="SPY",
                outcome={
                    "forward_return_pct": 999.0,
                    "excess_return_vs_benchmark_pct": 999.0,
                    "hit_above_threshold": True,
                },
                skip_reason=None,
            )
        )
        session.commit()

        report = build_monthly_rule_review_report(
            session,
            market="TW",
            year=2026,
            month=6,
            benchmark_symbol="SPY",
            min_sample_count=1,
        ).json_report

    assert report["sample_summary"]["validated_sample_count"] == 0
    assert report["sample_summary"]["missing_by_window"]["5"] == 1
    assert report["completeness_watermarks"][0][
        "validation_identity_mismatch_samples_by_window"
    ]["5"] == 1
    assert all(
        item["max_window_sample_count"] == 0
        for item in report["rule_recommendations"]
    )


def test_rule_review_workflow_calls_cloud_api_and_uploads_artifacts() -> None:
    workflow = (ROOT.parent / ".github" / "workflows" / "monthly-analysis-calibration.yml").read_text(
        encoding="utf-8"
    )

    assert "/internal/daily-radar/rule-review/monthly" in workflow
    assert "/internal/analysis-calibration/monthly" in workflow
    assert "${{ secrets.DAILY_RADAR_API_BASE_URL }}" in workflow
    assert "${{ secrets.DAILY_RADAR_INTERNAL_TOKEN }}" in workflow
    assert "Authorization: Bearer ${CALIBRATION_INTERNAL_TOKEN}" in workflow
    assert "reports/calibration/monthly" in workflow
    assert "manifest.json" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "min_replay_coverage: 0.9" in workflow
    assert "retention-days: 30" in workflow


def _sqlite_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _add_run(session: Session) -> DailyRadarRun:
    run = DailyRadarRun(
        run_date=date(2026, 6, 1),
        market="TW",
        status="completed",
        started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        universe_count=1,
        prefilter_count=1,
        candidate_count=1,
        errors=[],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def _add_candidate(session: Session, run: DailyRadarRun) -> DailyRadarCandidate:
    candidate = DailyRadarCandidate(
        run_id=run.id,
        symbol="2330.TW",
        name="TSMC",
        primary_bucket="institutional_accumulation",
        secondary_buckets=["price_volume_strengthening"],
        observation_score=88,
        bucket_scores={"institutional_accumulation": 88},
        risk_labels=["market_weakness"],
        matched_rules=[
            {"rule_id": "institutional_consecutive_flow"},
            {"rule_id": "price_volume_obv_rising"},
        ],
        explanation="Observation summary.",
        repeat_status="new",
        score_breakdown={
            "market_context": {"details": {"regime": "constructive"}},
            "relative_strength": {"freshness": "fresh", "relative_value": 0.03},
        },
        input_snapshot={"market_context": {"regime": "constructive"}},
        data_dates={"ohlcv": "2026-06-01"},
    )
    session.add(candidate)
    session.flush()
    return candidate


def _add_validation_result(session: Session, candidate: DailyRadarCandidate) -> None:
    session.add(
        DailyRadarForwardValidationResult(
            candidate_id=candidate.id,
            window_days=5,
            validation_version=FORWARD_VALIDATION_VERSION,
            status="validated",
            signal_date=date(2026, 6, 1),
            target_date=date(2026, 6, 8),
            benchmark_symbol="TAIEX",
            outcome={
                "forward_return_pct": 10.0,
                "excess_return_vs_benchmark_pct": 8.0,
                "hit_above_threshold": True,
            },
            skip_reason=None,
        )
    )


def _governance_replay_row(
    candidate_id: int,
    *,
    with_replay_input: bool,
) -> dict[str, Any]:
    record = _replay_record(
        f"{candidate_id:04d}.TW",
        positive_days=max(0, 5 - candidate_id // 20),
    )
    market_context = {
        "market": {},
        "data_dates": {},
        "benchmark": {"symbol": "TAIEX", "data_dates": {}},
    }
    prefilter_result = {
        "prefilter_status": "accepted",
        "prefilter_reasons": [],
    }
    baseline = score_daily_radar_record(
        record,
        market_context=market_context,
        prefilter_result=prefilter_result,
    )
    replay_input = (
        {
            "schema_version": "daily-radar-replay-input-v2",
            "record": record,
            "market_context": market_context,
            "prefilter_result": prefilter_result,
            "baseline_config": ScoringConfig().to_dict(),
        }
        if with_replay_input
        else {}
    )
    return {
        "candidate_id": candidate_id,
        "symbol": f"{candidate_id:04d}.TW",
        "signal_date": "2026-06-01",
        "window_days": 5,
        "benchmark_symbol": "TAIEX",
        "status": "validated",
        "outcome": {
            "excess_return_vs_benchmark_pct": float(candidate_id),
            "max_adverse_excursion_pct": -1.0,
        },
        "candidate_snapshot": {
            "record_date": "2026-06-01",
            "observation_score": baseline["observation_score"],
            "primary_bucket": baseline["primary_bucket"],
            "secondary_buckets": baseline["secondary_buckets"],
            "bucket_scores": baseline["bucket_scores"],
            "risk_labels": baseline["risk_labels"],
            "matched_rule_codes": [
                rule["rule_id"] for rule in baseline["matched_rules"]
            ],
            "input_snapshot": {
                "versions": {
                    "scoring_version": rule_governance_module.SCORING_VERSION,
                    "rule_version": rule_governance_module.RULE_VERSION,
                    "config_version": rule_governance_module.SCORING_CONFIG_VERSION,
                },
                "replay_input": replay_input,
            },
        },
    }


def test_governance_replay_accepts_zero_baseline_margin_reason() -> None:
    row = _governance_replay_row(1, with_replay_input=True)
    record = row["candidate_snapshot"]["input_snapshot"]["replay_input"]["record"]
    record["margin"].pop("margin_delta_pct")
    record["margin"]["margin_delta_pct_unavailable_reason"] = "baseline_zero"

    assert rule_governance_module._is_complete_daily_radar_replay_input(row)


def _replay_record(symbol: str, *, positive_days: int) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": symbol,
        "record_date": "2026-06-01",
        "ohlcv": {
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "previous_close": 100.0,
            "volume": 2_000_000,
            "avg_volume_20": 1_500_000,
        },
        "indicators": {
            "ma5": 100.0,
            "ma20": 98.0,
            "ma60": 95.0,
            "rsi14": 60.0,
            "bias20": 3.0,
            "macd_histogram": 1.0,
            "macd_hist_pct": 1.0 / 101.0 * 100,
            "kd_k": 60.0,
            "kd_d": 55.0,
            "atr14": 2.0,
            "volume_ratio": 1.2,
            "missing_trading_days_60": 0,
            "support_level": 99.0,
            "resistance_level": 102.0,
        },
        "technical_profile": {
            "version": "technical-layer-v4",
            "formula_versions": {
                "metrics": "technical-metrics-v4",
                "layering": "technical-layer-v4",
            },
            "data_quality": {
                "ohlcv_aligned": True,
                "price_level_basis": "ohlc_high_low",
                "price_level_completed_bars_only": True,
                "price_level_missing_reason": None,
            },
        },
        "price_history": [
            {"date": "2026-05-29", "close": 100.0},
            {"date": "2026-06-01", "close": 101.0},
        ],
        "institutional_flow": {
            "consecutive_positive_days": positive_days,
            "three_party_net_shares": 1,
            "flow_state": "consistent_accumulation",
            "net_flow_to_avg_volume": 0.1,
        },
        "margin": {
            "margin_delta_pct": 1.0,
            "margin_to_volume": 0.2,
        },
        "data_dates": {
            "ohlcv": "2026-06-01",
            "technical_indicators": "2026-06-01",
            "institutional_flow": "2026-06-01",
            "margin": "2026-06-01",
        },
    }


@pytest.mark.parametrize(
    ("path", "field"),
    [
        (("indicators",), "macd_hist_pct"),
        (("indicators",), "support_level"),
        (("indicators",), "resistance_level"),
        (("technical_profile", "formula_versions"), "metrics"),
        (("technical_profile", "data_quality"), "price_level_completed_bars_only"),
    ],
)
def test_governance_replay_rejects_incomplete_v2_technical_contract(
    path: tuple[str, ...],
    field: str,
) -> None:
    row = _governance_replay_row(1, with_replay_input=True)
    record = row["candidate_snapshot"]["input_snapshot"]["replay_input"]["record"]
    target: dict[str, Any] = record
    for key in path:
        target = target[key]
    target.pop(field)

    assert not rule_governance_module._is_complete_daily_radar_replay_input(row)
