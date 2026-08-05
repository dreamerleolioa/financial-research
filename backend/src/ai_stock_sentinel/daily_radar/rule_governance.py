from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Integer, and_, case, cast, extract, func, literal, or_, select, true, union_all
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.governance import (
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_MIN_REPLAY_COVERAGE,
    DEFAULT_MIN_VALIDATED_COVERAGE,
    block_bootstrap_mean_delta,
    independent_block_count,
    independent_sample_counts_by_window,
    metrics_by_window,
    month_bounds,
    outcome_metrics,
    replay_coverage_summary,
    required_block_counts,
    select_training_and_holdout_months,
    validated_coverage,
)
from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_FORWARD_WINDOWS,
    FORWARD_VALIDATION_VERSION,
)
from ai_stock_sentinel.daily_radar.data_quality import missing_scoring_fields
from ai_stock_sentinel.daily_radar.repository import PUBLIC_RUN_STATUSES
from ai_stock_sentinel.daily_radar.rule_registry import (
    SCORING_ACTIVE_TIERS,
    RuleRegistryEntry,
    get_rule_registry,
    registry_payload,
)
from ai_stock_sentinel.daily_radar.scoring import (
    RULE_VERSION,
    SCORING_CONFIG_VERSION,
    SCORING_VERSION,
    ScoringConfig,
    score_daily_radar_record,
)
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarRun,
)


RULE_REVIEW_REPORT_VERSION = "daily-radar-rule-review-v5"
DEFAULT_MIN_SAMPLE_COUNT = 20
DEFAULT_RANK_CUTOFF = 20
MAX_GOVERNANCE_CANDIDATES_PER_GROUP = 250
MAX_GOVERNANCE_REPLAY_SCORING_CALLS = 300_000
MAX_GOVERNANCE_BOOTSTRAP_ROW_ITERATIONS = 220_000_000
TUNABLE_SCORING_PARAMETERS = {
    "primary_bucket_weight": 0.05,
    "cross_confirmation_weight": 0.10,
    "market_context_weight": 0.10,
    "freshness_weight": 0.10,
    "relative_strength_weight": 0.10,
    "secondary_bucket_threshold": 5,
}
INCREASE_ONLY_SCORING_PARAMETERS = {
    "market_context_weight",
    "freshness_weight",
}
SCORING_CANDIDATE_CONFIG_COUNT = sum(
    1 if parameter in INCREASE_ONLY_SCORING_PARAMETERS else 2
    for parameter in TUNABLE_SCORING_PARAMETERS
)
DEFAULT_ABLATION_GROUPS = (
    "news_sentiment",
    "fundamental_valuation",
    "mfi",
    "obv",
    "kd",
    "donchian",
    "institutional_flow",
    "margin_related_risk_labels",
    "relative_strength",
    "market_regime_penalty",
)


@dataclass(frozen=True)
class MonthlyRuleReviewReport:
    json_report: dict[str, Any]
    markdown_report: str


@dataclass(frozen=True)
class _DailyRadarReplayContext:
    eligible_rows: list[dict[str, Any]]
    baseline_rows: list[dict[str, Any]]
    replay_coverage: dict[str, Any]
    ranking_pool_complete: bool
    ranking_pool_status: str
    incomplete_ranking_groups: list[dict[str, Any]]
    exclusion_reasons: dict[str, int]
    replay_workload: dict[str, Any]


def build_ablation_report(
    outcomes: Iterable[Mapping[str, Any]],
    *,
    market: str,
    sample_source: str,
    validation_version: str = FORWARD_VALIDATION_VERSION,
    min_sample_count: int = DEFAULT_MIN_SAMPLE_COUNT,
    ablation_groups: Sequence[str] = DEFAULT_ABLATION_GROUPS,
) -> dict[str, Any]:
    rows = [dict(row) for row in outcomes]
    validated_rows = [row for row in rows if row.get("status") == "validated"]
    windows = list(DEFAULT_FORWARD_WINDOWS)
    group_rows = [
        _ablation_group_row(
            group,
            window,
            validated_rows,
            min_sample_count=min_sample_count,
        )
        for group in ablation_groups
        for window in windows
    ]
    return {
        "metadata": {
            "report_version": f"{RULE_REVIEW_REPORT_VERSION}-ablation",
            "market": market,
            "sample_source": sample_source,
            "validation_version": validation_version,
            "min_sample_count": min_sample_count,
            "method": "co_occurrence_not_counterfactual",
            "positioning": "rule_quality_co_occurrence_diagnostic_not_live_scoring_change",
        },
        "sample_summary": _sample_summary(rows, windows),
        "ablation_groups": group_rows,
        "insufficient_sample_cases": [
            row for row in group_rows if row["recommendation"] == "insufficient_sample"
        ],
        "version_manifest": _version_manifest(),
    }


def build_monthly_rule_review_report(
    session: Session,
    *,
    market: str,
    year: int,
    month: int,
    validation_version: str | None = None,
    min_sample_count: int = DEFAULT_MIN_SAMPLE_COUNT,
    min_validated_coverage: float = DEFAULT_MIN_VALIDATED_COVERAGE,
    min_replay_coverage: float = DEFAULT_MIN_REPLAY_COVERAGE,
) -> MonthlyRuleReviewReport:
    month_start, month_end = _month_bounds(year, month)
    version = validation_version or FORWARD_VALIDATION_VERSION
    rows = validation_rows_from_results(
        session,
        market=market,
        start_date=month_start,
        end_date=month_end,
        validation_version=version,
    )
    ablation = build_ablation_report(
        rows,
        market=market,
        sample_source="production_db",
        validation_version=version,
        min_sample_count=min_sample_count,
    )
    validated_rows = [row for row in rows if row.get("status") == "validated"]
    rule_recommendations = _rule_recommendations(validated_rows, min_sample_count=min_sample_count)
    watermarks = _daily_radar_completeness_watermarks(
        session,
        market=market,
        through_date=month_end,
        validation_version=version,
    )
    cohort = select_training_and_holdout_months(watermarks)
    selected_months = [
        str(value)
        for value in _as_list(cohort.get("selected_months"))
    ]
    selected_month_set = set(selected_months)
    optimizer_detail_rows = validation_rows_from_results(
        session,
        market=market,
        start_date=None,
        end_date=None,
        validation_version=version,
        selected_months=selected_months,
    )
    optimizer_rows = [
        row
        for row in optimizer_detail_rows
        if str(row.get("signal_date") or "")[:7] in selected_month_set
    ]
    replay_context = _prepare_daily_radar_replay_context(
        optimizer_rows,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        min_replay_coverage=min_replay_coverage,
    )
    counterfactual_ablation = _counterfactual_ablation_report(
        replay_context,
        min_sample_count=min_sample_count,
    )
    candidate_configs, replay_coverage = _scoring_candidate_reports(
        replay_context,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        watermarks=watermarks,
        min_sample_count=min_sample_count,
        min_validated_coverage=min_validated_coverage,
    )
    min_training_blocks, min_holdout_blocks = required_block_counts()
    report = {
        "metadata": {
            "report_version": RULE_REVIEW_REPORT_VERSION,
            "market": market,
            "month": f"{year:04d}-{month:02d}",
            "sample_source": "production_db",
            "validation_version": version,
            "min_sample_count": min_sample_count,
            "min_validated_coverage": min_validated_coverage,
            "min_replay_coverage": min_replay_coverage,
            "min_training_block_count": min_training_blocks,
            "min_holdout_block_count": min_holdout_blocks,
            "positioning": "automated_rule_quality_recommendations_not_human_approved_strategy_update",
        },
        "sample_summary": ablation["sample_summary"],
        "registry_summary": _registry_summary(),
        "ablation_summary": counterfactual_ablation,
        "counterfactual_ablation_summary": counterfactual_ablation,
        "counterfactual_ablation_scope": {
            "selected_months": selected_months,
            "ranking_pool": "all_latest_run_candidates_for_each_forward_window",
            "ranking_pool_complete": replay_context.ranking_pool_complete,
            "ranking_pool_status": replay_context.ranking_pool_status,
            "outcome_join": "validated_results_only_after_selection",
            "live_change_eligible": False,
            "replay_workload": replay_context.replay_workload,
        },
        "co_occurrence_summary": ablation["ablation_groups"],
        "rule_recommendations": rule_recommendations,
        "skip_reasons": _skip_reasons(rows),
        "cohort": cohort,
        "completeness_watermarks": watermarks,
        "baseline_config": ScoringConfig().to_dict(),
        "candidate_configs": candidate_configs,
        "replay_coverage": replay_coverage,
        "locked_parameters": [
            "overextended_penalty",
            "flow_conflict_penalty",
            "margin_crowding_penalty",
            "market_weakness_penalty",
            "data_gap_penalty",
            "weak_market_component",
            "supportive_market_component",
            "data_gap_freshness_component",
            "fresh_freshness_component",
            "relative_strength_lookback_days",
            "rule_scores",
            "prefilter_rules",
        ],
        "auto_change_eligible": any(
            candidate.get("auto_change_eligible") is True
            for candidate in candidate_configs
        ),
        "version_manifest": _version_manifest(),
        "human_approval_boundary": {
            "automated_report": True,
            "updates_live_scoring": False,
            "requires_human_approved_versioned_strategy_update": True,
        },
    }
    return MonthlyRuleReviewReport(json_report=report, markdown_report=render_rule_review_markdown(report))


def validation_rows_from_results(
    session: Session,
    *,
    market: str,
    start_date: date | None,
    end_date: date | None,
    validation_version: str | None = None,
    selected_months: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    version = validation_version or FORWARD_VALIDATION_VERSION
    date_predicate = _date_filter(
        DailyRadarRun.run_date,
        start_date=start_date,
        end_date=end_date,
        selected_months=selected_months,
    )
    ranked_runs = (
        select(
            DailyRadarRun.id.label("run_id"),
            func.row_number().over(
                partition_by=DailyRadarRun.run_date,
                order_by=(
                    DailyRadarRun.created_at.desc(),
                    DailyRadarRun.id.desc(),
                ),
            ).label("row_number"),
        )
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.status.in_(PUBLIC_RUN_STATUSES),
            date_predicate,
        )
        .subquery()
    )
    latest_run_ids = select(ranked_runs.c.run_id).where(
        ranked_runs.c.row_number == 1
    )
    windows = _forward_windows_subquery()

    query = (
        select(
            DailyRadarForwardValidationResult,
            DailyRadarCandidate,
            DailyRadarRun,
            windows.c.window_days,
        )
        .select_from(DailyRadarCandidate)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .join(windows, true())
        .outerjoin(
            DailyRadarForwardValidationResult,
            and_(
                DailyRadarForwardValidationResult.candidate_id
                == DailyRadarCandidate.id,
                DailyRadarForwardValidationResult.window_days
                == windows.c.window_days,
                DailyRadarForwardValidationResult.validation_version == version,
            ),
        )
        .where(
            DailyRadarRun.market == market,
            date_predicate,
            DailyRadarRun.id.in_(latest_run_ids),
        )
        .order_by(
            DailyRadarRun.run_date.asc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
            DailyRadarCandidate.symbol.asc(),
            windows.c.window_days.asc(),
        )
    )
    return [
        _row_from_result(
            result,
            candidate,
            run,
            window_days=int(window_days),
            validation_version=version,
        )
        for result, candidate, run, window_days in session.execute(query).all()
    ]


def render_rule_review_markdown(report: Mapping[str, Any]) -> str:
    metadata = _mapping(report.get("metadata"))
    summary = _mapping(report.get("sample_summary"))
    replay_coverage = _mapping(report.get("replay_coverage"))
    replay_workload = _mapping(replay_coverage.get("replay_workload"))
    counterfactual_scope = _mapping(
        report.get("counterfactual_ablation_scope")
    )
    lines = [
        f"# Daily Radar Rule Review {metadata.get('month')}",
        "",
        "This automated report is a rule-quality governance diagnostic. It is not a performance advertisement, trading recommendation, or human-approved strategy update.",
        "",
        "## Metadata",
        "",
        f"- Market: {metadata.get('market')}",
        f"- Validation version: {metadata.get('validation_version')}",
        f"- Minimum sample count: {metadata.get('min_sample_count')}",
        f"- Minimum replay coverage: {metadata.get('min_replay_coverage')}",
        f"- Replay coverage: {replay_coverage.get('coverage')}",
        f"- Replay coverage meets threshold: {replay_coverage.get('meets_threshold')}",
        f"- Replay ranking pool complete: {replay_coverage.get('ranking_pool_complete')}",
        f"- Replay ranking pool status: {replay_coverage.get('ranking_pool_status')}",
        f"- Estimated replay scoring calls: {replay_workload.get('estimated_scoring_calls')}",
        f"- Estimated bootstrap row-iterations: {replay_workload.get('estimated_bootstrap_row_iterations')}",
        f"- Replay workload within budget: {not bool(replay_workload.get('capacity_exceeded'))}",
        f"- Minimum training date blocks: {metadata.get('min_training_block_count')}",
        f"- Minimum holdout date blocks: {metadata.get('min_holdout_block_count')}",
        f"- Validated samples: {summary.get('validated_sample_count', 0)}",
        f"- Skipped samples: {summary.get('skipped_sample_count', 0)}",
        f"- Missing validation results: {summary.get('missing_sample_count', 0)}",
        f"- Counterfactual cohort: {', '.join(str(value) for value in _as_list(counterfactual_scope.get('selected_months'))) or 'insufficient'}",
        f"- Counterfactual ranking pool: {counterfactual_scope.get('ranking_pool')}",
        f"- Counterfactual outcome join: {counterfactual_scope.get('outcome_join')}",
        "",
        "## Counterfactual Ablation Recommendations",
        "",
        "| Group | Window | Validated sample | Delta excess pct | Recommendation |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in _as_list(report.get("ablation_summary")):
        lines.append(
            "| {group} | {window} | {sample} | {delta} | {recommendation} |".format(
                group=row.get("group"),
                window=row.get("window_days"),
                sample=row.get("validated_selected_sample_count"),
                delta=_markdown_value(row.get("delta_average_excess_return_vs_benchmark_pct")),
                recommendation=row.get("recommendation"),
            )
        )
    lines.extend([
        "",
        "## Rule Recommendations",
        "",
        "| Rule | Tier | Status | Window sample max | Automated recommendation |",
        "| --- | --- | --- | ---: | --- |",
    ])
    for row in _as_list(report.get("rule_recommendations")):
        lines.append(
            "| {rule} | {tier} | {status} | {sample} | {recommendation} |".format(
                rule=row.get("rule_code"),
                tier=row.get("tier"),
                status=row.get("validation_status"),
                sample=row.get("max_window_sample_count"),
                recommendation=row.get("automated_recommendation"),
            )
        )
    lines.extend([
        "",
        "## Weight Candidate Actions",
        "",
        "| Parameter | Before | After | Training delta | Holdout delta | Eligible | Reason |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- |",
    ])
    for row in _as_list(report.get("candidate_configs")):
        bootstrap = _mapping(row.get("training_block_bootstrap"))
        holdout = _mapping(row.get("holdout_primary_metric"))
        lines.append(
            "| {parameter} | {before} | {after} | {training} | {holdout} | {eligible} | {reason} |".format(
                parameter=row.get("parameter"),
                before=row.get("before"),
                after=row.get("after"),
                training=_markdown_value(bootstrap.get("delta")),
                holdout=_markdown_value(holdout.get("delta")),
                eligible=row.get("auto_change_eligible"),
                reason=row.get("eligibility_reason"),
            )
        )
    lines.extend([
        "",
        "## Human Approval Boundary",
        "",
        "Automated recommendations may inform a later versioned strategy update plan, but this report does not modify live scoring, ranking, rule tier, or rule version.",
        "",
    ])
    return "\n".join(lines)


def write_rule_review_artifacts(report: Mapping[str, Any], markdown: str, *, json_path: str, markdown_path: str) -> None:
    from pathlib import Path

    Path(json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(markdown_path).write_text(markdown, encoding="utf-8")


def _ablation_group_row(
    group: str,
    window_days: int,
    rows: Sequence[Mapping[str, Any]],
    *,
    min_sample_count: int,
) -> dict[str, Any]:
    window_rows = [row for row in rows if int(row["window_days"]) == window_days]
    with_group = [row for row in window_rows if group in set(_rule_groups(row))]
    without_group = [row for row in window_rows if group not in set(_rule_groups(row))]
    metrics_with = _metrics(with_group)
    metrics_without = _metrics(without_group)
    delta_excess = _delta(
        metrics_with["average_excess_return_vs_benchmark_pct"],
        metrics_without["average_excess_return_vs_benchmark_pct"],
    )
    recommendation = _recommendation(
        sample_count=int(metrics_with["sample_count"]),
        min_sample_count=min_sample_count,
        delta_excess=delta_excess,
        profit_factor=metrics_with["profit_factor_like_ratio"],
    )
    return {
        "group": group,
        "window_days": window_days,
        "sample_count_with_group": metrics_with["sample_count"],
        "sample_count_without_group": metrics_without["sample_count"],
        "average_excess_return_vs_benchmark_pct_with_group": metrics_with["average_excess_return_vs_benchmark_pct"],
        "average_excess_return_vs_benchmark_pct_without_group": metrics_without["average_excess_return_vs_benchmark_pct"],
        "delta_average_excess_return_vs_benchmark_pct": delta_excess,
        "hit_rate_above_threshold_with_group": metrics_with["hit_rate_above_threshold"],
        "profit_factor_like_ratio_with_group": metrics_with["profit_factor_like_ratio"],
        "recommendation": recommendation,
    }


def _counterfactual_ablation_report(
    replay_context: _DailyRadarReplayContext,
    *,
    min_sample_count: int,
    ablation_groups: Sequence[str] = DEFAULT_ABLATION_GROUPS,
) -> list[dict[str, Any]]:
    replayable = replay_context.eligible_rows
    baseline = replay_context.baseline_rows
    registry = get_rule_registry()
    results: list[dict[str, Any]] = []
    for group in ablation_groups:
        group_entries = {
            code
            for code, entry in registry.items()
            if entry.ablation_group == group
        }
        excluded_codes = {
            code
            for code in group_entries
            if registry[code].tier in SCORING_ACTIVE_TIERS
        }
        if not excluded_codes:
            for window in DEFAULT_FORWARD_WINDOWS:
                before = [
                    row
                    for row in baseline
                    if int(row["window_days"]) == window
                ]
                before_selected = {
                    int(row["candidate_id"])
                    for row in before
                    if row.get("selected_for_rank") is True
                }
                before_metrics = outcome_metrics(
                    before,
                    selection=lambda row: (
                        row.get("status") == "validated"
                        and row.get("selected_for_rank") is True
                    ),
                )
                results.append({
                    "group": group,
                    "window_days": window,
                    "method": "not_applicable_context_only",
                    "excluded_rule_codes": [],
                    "context_only_rule_codes": sorted(group_entries),
                    "baseline_selected_count": len(before_selected),
                    "ablated_selected_count": len(before_selected),
                    "selection_membership_changed_count": 0,
                    "validated_selected_sample_count": int(
                        before_metrics["selected_sample_count"]
                    ),
                    "average_excess_return_vs_benchmark_pct_baseline": (
                        _float_or_none(
                            before_metrics.get(
                                "average_excess_return_vs_benchmark_pct"
                            )
                        )
                    ),
                    "average_excess_return_vs_benchmark_pct_ablated": None,
                    "delta_average_excess_return_vs_benchmark_pct": None,
                    "recommendation": "not_in_live_score",
                    "eligible_for_live_change": False,
                })
            continue
        if replay_context.ranking_pool_status == "not_applicable":
            for window in DEFAULT_FORWARD_WINDOWS:
                results.append({
                    "group": group,
                    "window_days": window,
                    "method": "not_applicable_no_cohort",
                    "excluded_rule_codes": sorted(excluded_codes),
                    "baseline_selected_count": 0,
                    "ablated_selected_count": 0,
                    "selection_membership_changed_count": 0,
                    "validated_selected_sample_count": 0,
                    "average_excess_return_vs_benchmark_pct_baseline": None,
                    "average_excess_return_vs_benchmark_pct_ablated": None,
                    "delta_average_excess_return_vs_benchmark_pct": None,
                    "recommendation": "not_applicable_no_cohort",
                    "eligible_for_live_change": False,
                })
            continue
        if replay_context.ranking_pool_status in {
            "incomplete",
            "capacity_exceeded",
        }:
            blocked_recommendation = (
                "replay_workload_limit_exceeded"
                if replay_context.ranking_pool_status == "capacity_exceeded"
                else "replay_ranking_pool_incomplete"
            )
            for window in DEFAULT_FORWARD_WINDOWS:
                before = [
                    row
                    for row in baseline
                    if int(row["window_days"]) == window
                ]
                before_selected = {
                    int(row["candidate_id"])
                    for row in before
                    if row.get("selected_for_rank") is True
                }
                results.append({
                    "group": group,
                    "window_days": window,
                    "method": "same_input_counterfactual_replay",
                    "excluded_rule_codes": sorted(excluded_codes),
                    "baseline_selected_count": len(before_selected),
                    "ablated_selected_count": 0,
                    "selection_membership_changed_count": 0,
                    "validated_selected_sample_count": 0,
                    "average_excess_return_vs_benchmark_pct_baseline": None,
                    "average_excess_return_vs_benchmark_pct_ablated": None,
                    "delta_average_excess_return_vs_benchmark_pct": None,
                    "recommendation": blocked_recommendation,
                    "eligible_for_live_change": False,
                })
            continue
        ablated = _replay_daily_radar_rows(
            replayable,
            ScoringConfig(),
            excluded_rule_codes=excluded_codes,
        )
        for window in DEFAULT_FORWARD_WINDOWS:
            before = [row for row in baseline if int(row["window_days"]) == window]
            after = [row for row in ablated if int(row["window_days"]) == window]
            selection = lambda row: (
                row.get("status") == "validated"
                and row.get("selected_for_rank") is True
            )
            before_metrics = outcome_metrics(before, selection=selection)
            after_metrics = outcome_metrics(after, selection=selection)
            before_excess = _float_or_none(
                before_metrics.get("average_excess_return_vs_benchmark_pct")
            )
            after_excess = _float_or_none(
                after_metrics.get("average_excess_return_vs_benchmark_pct")
            )
            delta_excess = _delta(after_excess, before_excess)
            validated_count = min(
                int(before_metrics["selected_sample_count"]),
                int(after_metrics["selected_sample_count"]),
            )
            if validated_count < min_sample_count or delta_excess is None:
                recommendation = "insufficient_sample"
            elif delta_excess > 0:
                recommendation = "review_group_removal"
            else:
                recommendation = "keep_group"
            before_selected = {
                int(row["candidate_id"])
                for row in before
                if row.get("selected_for_rank") is True
            }
            after_selected = {
                int(row["candidate_id"])
                for row in after
                if row.get("selected_for_rank") is True
            }
            results.append({
                "group": group,
                "window_days": window,
                "method": "same_input_counterfactual_replay",
                "excluded_rule_codes": sorted(excluded_codes),
                "baseline_selected_count": len(before_selected),
                "ablated_selected_count": len(after_selected),
                "selection_membership_changed_count": len(
                    before_selected.symmetric_difference(after_selected)
                ),
                "validated_selected_sample_count": validated_count,
                "average_excess_return_vs_benchmark_pct_baseline": before_excess,
                "average_excess_return_vs_benchmark_pct_ablated": after_excess,
                "delta_average_excess_return_vs_benchmark_pct": delta_excess,
                "recommendation": recommendation,
                "eligible_for_live_change": False,
            })
    return results


def _rule_recommendations(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_sample_count: int,
) -> list[dict[str, Any]]:
    registry = get_rule_registry()
    recommendations: list[dict[str, Any]] = []
    for code, entry in sorted(registry.items()):
        rule_rows = [row for row in rows if code in set(_rule_codes(row))]
        metrics_by_window = {
            str(window): _metrics([row for row in rule_rows if int(row["window_days"]) == window])
            for window in sorted({int(row["window_days"]) for row in rows})
        }
        max_sample = max((int(metrics["sample_count"]) for metrics in metrics_by_window.values()), default=0)
        recommendation = _rule_recommendation(entry, metrics_by_window, max_sample, min_sample_count)
        recommendations.append({
            "rule_code": code,
            "description": entry.description,
            "tier": entry.tier,
            "validation_status": entry.validation_status,
            "ablation_group": entry.ablation_group,
            "max_window_sample_count": max_sample,
            "metrics_by_window": metrics_by_window,
            "automated_recommendation": recommendation,
        })
    return recommendations


def _rule_recommendation(
    entry: RuleRegistryEntry,
    metrics_by_window: Mapping[str, Mapping[str, Any]],
    max_sample: int,
    min_sample_count: int,
) -> str:
    if entry.tier in {"deprecated", "context_only"}:
        return "keep_out_of_live_score"
    if max_sample < min_sample_count:
        return "insufficient_sample_keep_current_tier"
    negative_windows = [
        metrics
        for metrics in metrics_by_window.values()
        if metrics.get("sample_count") and _float_or_none(metrics.get("average_excess_return_vs_benchmark_pct")) is not None
        and float(metrics["average_excess_return_vs_benchmark_pct"]) < 0
    ]
    if len(negative_windows) >= 2:
        return "review_for_demotion_or_context_only"
    return "retain_pending_human_review"


def _recommendation(
    *,
    sample_count: int,
    min_sample_count: int,
    delta_excess: float | None,
    profit_factor: float | None,
) -> str:
    if sample_count < min_sample_count:
        return "insufficient_sample"
    if delta_excess is not None and delta_excess < 0 and (profit_factor is None or profit_factor < 1):
        return "review_for_demotion"
    if delta_excess is not None and delta_excess > 0:
        return "retain_pending_human_review"
    return "monitor"


def _row_from_result(
    result: DailyRadarForwardValidationResult | None,
    candidate: DailyRadarCandidate,
    run: DailyRadarRun,
    *,
    window_days: int,
    validation_version: str,
) -> dict[str, Any]:
    snapshot = _candidate_snapshot(candidate, run)
    if result is None:
        return {
            "candidate_id": candidate.id,
            "symbol": candidate.symbol,
            "signal_date": run.run_date.isoformat(),
            "window_days": window_days,
            "validation_version": validation_version,
            "benchmark_symbol": None,
            "status": "missing",
            "skip_reason": "validation_result_missing",
            "outcome": {},
            "candidate_snapshot": snapshot,
        }
    return {
        "candidate_id": result.candidate_id,
        "symbol": candidate.symbol,
        "signal_date": result.signal_date.isoformat(),
        "window_days": result.window_days,
        "validation_version": result.validation_version,
        "benchmark_symbol": result.benchmark_symbol,
        "status": result.status,
        "skip_reason": result.skip_reason,
        "outcome": dict(result.outcome or {}),
        "candidate_snapshot": snapshot,
    }


def _candidate_snapshot(candidate: DailyRadarCandidate, run: DailyRadarRun) -> dict[str, Any]:
    score_breakdown = _mapping(candidate.score_breakdown)
    return {
        "candidate_id": candidate.id,
        "run_id": run.id,
        "symbol": candidate.symbol,
        "record_date": run.run_date.isoformat(),
        "primary_bucket": candidate.primary_bucket,
        "secondary_buckets": list(candidate.secondary_buckets or []),
        "observation_score": candidate.observation_score,
        "bucket_scores": dict(candidate.bucket_scores or {}),
        "matched_rule_codes": _matched_rule_codes(candidate.matched_rules),
        "matched_rules": list(candidate.matched_rules or []),
        "risk_labels": list(candidate.risk_labels or []),
        "market_regime": _market_regime(candidate),
        "relative_strength_bucket": _relative_strength_bucket(score_breakdown),
        "repeat_status": candidate.repeat_status,
        "score_decile": _score_decile(candidate.observation_score),
        "data_freshness_status": "data_gap" if "data_gap" in set(candidate.risk_labels or []) else "fresh",
        "score_breakdown": dict(candidate.score_breakdown or {}),
        "input_snapshot": dict(candidate.input_snapshot or {}),
        "data_dates": dict(candidate.data_dates or {}),
    }


def _daily_radar_completeness_watermarks(
    session: Session,
    *,
    market: str,
    through_date: date,
    validation_version: str,
) -> list[dict[str, Any]]:
    ranked_runs = (
        select(
            DailyRadarRun.id.label("run_id"),
            func.row_number().over(
                partition_by=DailyRadarRun.run_date,
                order_by=(
                    DailyRadarRun.created_at.desc(),
                    DailyRadarRun.id.desc(),
                ),
            ).label("row_number"),
        )
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.status.in_(PUBLIC_RUN_STATUSES),
            DailyRadarRun.run_date <= through_date,
        )
        .subquery()
    )
    latest_run_ids = select(ranked_runs.c.run_id).where(
        ranked_runs.c.row_number == 1
    )
    windows = _forward_windows_subquery()
    year_value = cast(extract("year", DailyRadarRun.run_date), Integer)
    month_value = cast(extract("month", DailyRadarRun.run_date), Integer)
    expected_count = func.count(func.distinct(DailyRadarCandidate.id))
    evaluated_count = func.count(
        func.distinct(
            case(
                (
                    DailyRadarForwardValidationResult.id.is_not(None),
                    DailyRadarCandidate.id,
                )
            )
        )
    )
    validated_count = func.count(
        func.distinct(
            case(
                (
                    DailyRadarForwardValidationResult.status == "validated",
                    DailyRadarCandidate.id,
                )
            )
        )
    )
    query = (
        select(
            year_value,
            month_value,
            windows.c.window_days,
            expected_count,
            evaluated_count,
            validated_count,
        )
        .select_from(DailyRadarCandidate)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .join(windows, true())
        .outerjoin(
            DailyRadarForwardValidationResult,
            and_(
                DailyRadarForwardValidationResult.candidate_id
                == DailyRadarCandidate.id,
                DailyRadarForwardValidationResult.window_days
                == windows.c.window_days,
                DailyRadarForwardValidationResult.validation_version
                == validation_version,
            ),
        )
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.status.in_(PUBLIC_RUN_STATUSES),
            DailyRadarRun.run_date <= through_date,
            DailyRadarRun.id.in_(latest_run_ids),
        )
        .group_by(year_value, month_value, windows.c.window_days)
        .order_by(
            year_value.asc(),
            month_value.asc(),
            windows.c.window_days.asc(),
        )
    )
    by_month: dict[str, dict[int, tuple[int, int, int]]] = defaultdict(dict)
    for year, month, window, expected, evaluated, validated in session.execute(query):
        by_month[f"{int(year):04d}-{int(month):02d}"][int(window)] = (
            int(expected or 0),
            int(evaluated or 0),
            int(validated or 0),
        )
    watermarks: list[dict[str, Any]] = []
    for month, counts_by_window in sorted(by_month.items()):
        expected_by_window = {
            str(window): counts_by_window.get(window, (0, 0, 0))[0]
            for window in DEFAULT_FORWARD_WINDOWS
        }
        evaluated_by_window = {
            str(window): counts_by_window.get(window, (0, 0, 0))[1]
            for window in DEFAULT_FORWARD_WINDOWS
        }
        validated_by_window = {
            str(window): counts_by_window.get(window, (0, 0, 0))[2]
            for window in DEFAULT_FORWARD_WINDOWS
        }
        evaluated_coverage_by_window = {
            str(window): validated_coverage(
                evaluated_by_window[str(window)],
                expected_by_window[str(window)],
            )
            for window in DEFAULT_FORWARD_WINDOWS
        }
        validated_coverage_by_window = {
            str(window): validated_coverage(
                validated_by_window[str(window)],
                expected_by_window[str(window)],
            )
            for window in DEFAULT_FORWARD_WINDOWS
        }
        expected_value = expected_by_window["20"]
        evaluated_value = evaluated_by_window["20"]
        validated_value = validated_by_window["20"]
        coverage_values = [
            value
            for value in validated_coverage_by_window.values()
            if value is not None
        ]
        watermarks.append({
            "month": month,
            "expected_20d_samples": expected_value,
            "evaluated_20d_samples": evaluated_value,
            "validated_20d_samples": validated_value,
            "expected_samples_by_window": expected_by_window,
            "evaluated_samples_by_window": evaluated_by_window,
            "validated_samples_by_window": validated_by_window,
            "evaluated_coverage_by_window": evaluated_coverage_by_window,
            "validated_coverage_by_window": validated_coverage_by_window,
            "maturity_complete": (
                expected_value > 0
                and all(
                    evaluated_by_window[str(window)]
                    >= expected_by_window[str(window)]
                    for window in DEFAULT_FORWARD_WINDOWS
                )
            ),
            "validated_coverage": min(coverage_values)
            if len(coverage_values) == len(DEFAULT_FORWARD_WINDOWS)
            else None,
        })
    return watermarks


def _forward_windows_subquery() -> Any:
    return union_all(
        *[
            select(literal(window).label("window_days"))
            for window in DEFAULT_FORWARD_WINDOWS
        ]
    ).subquery("forward_windows")


def _date_filter(
    column: Any,
    *,
    start_date: date | None,
    end_date: date | None,
    selected_months: Sequence[str] | None,
) -> Any:
    if selected_months is not None:
        predicates = []
        for value in selected_months:
            year_text, month_text = value.split("-", 1)
            start, end = month_bounds(int(year_text), int(month_text))
            predicates.append(and_(column >= start, column <= end))
        return or_(*predicates) if predicates else column.in_([])
    predicates = []
    if start_date is not None:
        predicates.append(column >= start_date)
    if end_date is not None:
        predicates.append(column <= end_date)
    return and_(*predicates) if predicates else column.is_not(None)


def _scoring_candidate_reports(
    replay_context: _DailyRadarReplayContext,
    *,
    baseline_config: ScoringConfig,
    cohort: Mapping[str, Any],
    watermarks: Sequence[Mapping[str, Any]],
    min_sample_count: int,
    min_validated_coverage: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reports = [
        _scoring_candidate_report(
            (
                []
                if replay_context.ranking_pool_status == "capacity_exceeded"
                else replay_context.eligible_rows
            ),
            baseline_rows=replay_context.baseline_rows,
            baseline_config=baseline_config,
            parameter=parameter,
            direction=direction,
            step=step,
            cohort=cohort,
            watermarks=watermarks,
            min_sample_count=min_sample_count,
            min_validated_coverage=min_validated_coverage,
            replay_coverage_ok=bool(
                replay_context.replay_coverage["meets_threshold"]
            ),
            replay_ranking_pool_complete=(
                replay_context.ranking_pool_complete
            ),
            replay_ranking_pool_status=replay_context.ranking_pool_status,
        )
        for parameter, step in TUNABLE_SCORING_PARAMETERS.items()
        for direction in ((1,) if parameter in INCREASE_ONLY_SCORING_PARAMETERS else (-1, 1))
    ]
    return reports, {
        **replay_context.replay_coverage,
        "ranking_pool_complete": replay_context.ranking_pool_complete,
        "ranking_pool_status": replay_context.ranking_pool_status,
        "incomplete_ranking_groups": replay_context.incomplete_ranking_groups,
        "exclusion_reasons": replay_context.exclusion_reasons,
        "replay_workload": replay_context.replay_workload,
    }


def _prepare_daily_radar_replay_context(
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_config: ScoringConfig,
    cohort: Mapping[str, Any],
    min_replay_coverage: float,
) -> _DailyRadarReplayContext:
    replay_workload = _daily_radar_replay_workload(rows)
    eligible_rows: list[dict[str, Any]] = []
    excluded_candidate_ids: dict[str, set[int]] = {}
    for row in rows:
        if not _is_complete_daily_radar_replay_input(row):
            candidate_id = row.get("candidate_id")
            if candidate_id is not None:
                excluded_candidate_ids.setdefault(
                    "replay_input_incomplete",
                    set(),
                ).add(int(candidate_id))
            continue
        eligible_rows.append(dict(row))
    ranking_pool_status, incomplete_ranking_groups = (
        _daily_radar_ranking_pool_completeness(
            rows,
            eligible_rows,
            workload_capacity_exceeded=bool(
                replay_workload["capacity_exceeded"]
            ),
        )
    )
    baseline_rows: list[dict[str, Any]] = []
    if ranking_pool_status not in {"not_applicable", "capacity_exceeded"}:
        baseline_rows = _replay_daily_radar_rows(
            eligible_rows,
            baseline_config,
        )
        mismatch_candidate_ids = _baseline_replay_mismatch_candidate_ids(
            baseline_rows
        )
        if mismatch_candidate_ids:
            excluded_candidate_ids.setdefault(
                "baseline_replay_mismatch",
                set(),
            ).update(mismatch_candidate_ids)
            eligible_rows = [
                row
                for row in eligible_rows
                if row.get("candidate_id") not in mismatch_candidate_ids
            ]
            baseline_rows = [
                row
                for row in baseline_rows
                if row.get("candidate_id") not in mismatch_candidate_ids
            ]
            ranking_pool_status, incomplete_ranking_groups = (
                _daily_radar_ranking_pool_completeness(rows, eligible_rows)
            )
    replay_coverage = replay_coverage_summary(
        rows,
        eligible_rows,
        sample_key="candidate_id",
        date_key="signal_date",
        selected_months=[
            str(value) for value in _as_list(cohort.get("selected_months"))
        ],
        minimum_coverage=min_replay_coverage,
    )
    return _DailyRadarReplayContext(
        eligible_rows=eligible_rows,
        baseline_rows=baseline_rows,
        replay_coverage=replay_coverage,
        ranking_pool_complete=ranking_pool_status == "complete",
        ranking_pool_status=ranking_pool_status,
        incomplete_ranking_groups=incomplete_ranking_groups,
        exclusion_reasons={
            reason: len(candidate_ids)
            for reason, candidate_ids in sorted(excluded_candidate_ids.items())
        },
        replay_workload=replay_workload,
    )


def _daily_radar_ranking_pool_completeness(
    rows: Sequence[Mapping[str, Any]],
    eligible_rows: Sequence[Mapping[str, Any]],
    *,
    workload_capacity_exceeded: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    selected_by_group: dict[tuple[str, int], set[int]] = defaultdict(set)
    eligible_by_group: dict[tuple[str, int], set[int]] = defaultdict(set)
    for row in rows:
        candidate_id = row.get("candidate_id")
        if candidate_id is None:
            continue
        key = (
            str(row.get("signal_date") or ""),
            int(row.get("window_days") or 0),
        )
        selected_by_group[key].add(int(candidate_id))
    for row in eligible_rows:
        candidate_id = row.get("candidate_id")
        if candidate_id is None:
            continue
        key = (
            str(row.get("signal_date") or ""),
            int(row.get("window_days") or 0),
        )
        eligible_by_group[key].add(int(candidate_id))
    incomplete = []
    capacity_exceeded = workload_capacity_exceeded
    for (signal_date, window_days), candidate_ids in sorted(
        selected_by_group.items()
    ):
        replayable_ids = eligible_by_group.get((signal_date, window_days), set())
        missing_ids = sorted(candidate_ids - replayable_ids)
        exceeds_capacity = (
            len(candidate_ids) > MAX_GOVERNANCE_CANDIDATES_PER_GROUP
        )
        capacity_exceeded = capacity_exceeded or exceeds_capacity
        if not missing_ids and not exceeds_capacity:
            continue
        incomplete.append({
            "signal_date": signal_date,
            "window_days": window_days,
            "candidate_count": len(candidate_ids),
            "replayable_candidate_count": len(replayable_ids),
            "missing_candidate_count": len(missing_ids),
            "maximum_candidate_count": MAX_GOVERNANCE_CANDIDATES_PER_GROUP,
            "capacity_exceeded": exceeds_capacity,
        })
    if not selected_by_group:
        return "not_applicable", []
    if capacity_exceeded:
        return "capacity_exceeded", incomplete
    if incomplete:
        return "incomplete", incomplete
    return "complete", []


def _daily_radar_replay_workload(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_ids = {
        int(row["candidate_id"])
        for row in rows
        if row.get("candidate_id") is not None
    }
    registry = get_rule_registry()
    active_ablation_groups = {
        entry.ablation_group
        for entry in registry.values()
        if entry.tier in SCORING_ACTIVE_TIERS
        and entry.ablation_group in DEFAULT_ABLATION_GROUPS
    }
    scoring_pass_count = (
        1
        + SCORING_CANDIDATE_CONFIG_COUNT
        + len(active_ablation_groups)
    )
    estimated_scoring_calls = len(candidate_ids) * scoring_pass_count
    estimated_bootstrap_row_iterations = (
        len(rows)
        * SCORING_CANDIDATE_CONFIG_COUNT
        * DEFAULT_BOOTSTRAP_ITERATIONS
    )
    exceeded_limits = []
    if estimated_scoring_calls > MAX_GOVERNANCE_REPLAY_SCORING_CALLS:
        exceeded_limits.append("replay_scoring_calls")
    if (
        estimated_bootstrap_row_iterations
        > MAX_GOVERNANCE_BOOTSTRAP_ROW_ITERATIONS
    ):
        exceeded_limits.append("bootstrap_row_iterations")
    return {
        "candidate_count": len(candidate_ids),
        "candidate_window_row_count": len(rows),
        "candidate_config_count": SCORING_CANDIDATE_CONFIG_COUNT,
        "active_ablation_group_count": len(active_ablation_groups),
        "scoring_pass_count": scoring_pass_count,
        "estimated_scoring_calls": estimated_scoring_calls,
        "maximum_scoring_calls": MAX_GOVERNANCE_REPLAY_SCORING_CALLS,
        "bootstrap_iterations": DEFAULT_BOOTSTRAP_ITERATIONS,
        "estimated_bootstrap_row_iterations": (
            estimated_bootstrap_row_iterations
        ),
        "maximum_bootstrap_row_iterations": (
            MAX_GOVERNANCE_BOOTSTRAP_ROW_ITERATIONS
        ),
        "capacity_exceeded": bool(exceeded_limits),
        "exceeded_limits": exceeded_limits,
    }


def _is_complete_daily_radar_replay_input(
    row: Mapping[str, Any],
) -> bool:
    snapshot = row.get("candidate_snapshot")
    if not isinstance(snapshot, Mapping):
        return False
    input_snapshot = snapshot.get("input_snapshot")
    if not isinstance(input_snapshot, Mapping):
        return False
    versions = input_snapshot.get("versions")
    if not isinstance(versions, Mapping) or dict(versions) != {
        "scoring_version": SCORING_VERSION,
        "rule_version": RULE_VERSION,
        "config_version": SCORING_CONFIG_VERSION,
    }:
        return False
    replay_input = input_snapshot.get("replay_input")
    if not isinstance(replay_input, Mapping):
        return False
    if replay_input.get("schema_version") != "daily-radar-replay-input-v1":
        return False
    if not isinstance(replay_input.get("market_context"), Mapping):
        return False
    if not isinstance(replay_input.get("prefilter_result"), Mapping):
        return False
    baseline_config = replay_input.get("baseline_config")
    if not isinstance(baseline_config, Mapping):
        return False
    if dict(baseline_config) != ScoringConfig().to_dict():
        return False
    record = replay_input.get("record")
    if not isinstance(record, Mapping):
        return False
    for key in (
        "ohlcv",
        "indicators",
        "technical_profile",
        "institutional_flow",
        "margin",
        "data_dates",
    ):
        if not isinstance(record.get(key), Mapping):
            return False
    price_history = record.get("price_history")
    if not isinstance(price_history, list) or not price_history:
        return False
    symbol = str(record.get("symbol") or "")
    if not symbol or symbol != str(row.get("symbol") or ""):
        return False
    record_date = str(record.get("record_date") or "")
    snapshot_date = str(snapshot.get("record_date") or "")
    validation_date = str(row.get("signal_date") or "")
    if not snapshot_date or not validation_date:
        return False
    if record_date != snapshot_date or record_date != validation_date:
        return False
    try:
        record_date_value = date.fromisoformat(record_date)
    except ValueError:
        return False
    for item in price_history:
        if not isinstance(item, Mapping) or not _is_finite_replay_number(
            item.get("close")
        ):
            return False
        try:
            price_date = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            return False
        if price_date > record_date_value:
            return False
    ohlcv = _mapping(record.get("ohlcv"))
    indicators = _mapping(record.get("indicators"))
    institutional_flow = _mapping(record.get("institutional_flow"))
    margin = _mapping(record.get("margin"))
    if missing_scoring_fields(
        ohlcv=ohlcv,
        indicators=indicators,
        institutional_flow=institutional_flow,
        margin=margin,
    ):
        return False
    numeric_fields = (
        *((ohlcv, key) for key in (
            "open", "high", "low", "close", "previous_close", "volume",
            "avg_volume_20",
        )),
        *((indicators, key) for key in (
            "ma5", "ma20", "ma60", "rsi14", "bias20", "macd_histogram",
            "kd_k", "kd_d", "atr14", "volume_ratio",
            "missing_trading_days_60",
        )),
        *((institutional_flow, key) for key in (
            "three_party_net_shares", "consecutive_positive_days",
            "net_flow_to_avg_volume",
        )),
        *((margin, key) for key in ("margin_delta_pct", "margin_to_volume")),
    )
    if any(
        not _is_finite_replay_number(payload.get(key))
        for payload, key in numeric_fields
    ):
        return False
    if not str(institutional_flow.get("flow_state") or ""):
        return False
    technical_profile = _mapping(record.get("technical_profile"))
    if not str(technical_profile.get("version") or ""):
        return False
    data_dates = _mapping(record.get("data_dates"))
    for key in (
        "ohlcv",
        "technical_indicators",
        "institutional_flow",
        "margin",
    ):
        try:
            data_date = date.fromisoformat(str(data_dates.get(key) or ""))
        except ValueError:
            return False
        if data_date > record_date_value:
            return False
    market_context = _mapping(replay_input.get("market_context"))
    market = market_context.get("market")
    if not isinstance(market, Mapping):
        return False
    market_dates = _mapping(market_context.get("data_dates"))
    benchmark = _mapping(market_context.get("benchmark"))
    benchmark_dates = _mapping(benchmark.get("data_dates"))
    for value in (*market_dates.values(), *benchmark_dates.values()):
        try:
            context_date = date.fromisoformat(str(value or ""))
        except ValueError:
            return False
        if context_date > record_date_value:
            return False
    market_data_date = market.get("data_date")
    if market_data_date is not None:
        try:
            parsed_market_date = date.fromisoformat(str(market_data_date))
        except ValueError:
            return False
        if parsed_market_date > record_date_value:
            return False
    benchmark_history = benchmark.get("price_history")
    if isinstance(benchmark_history, list):
        for item in benchmark_history:
            if not isinstance(item, Mapping):
                return False
            try:
                benchmark_date = date.fromisoformat(str(item.get("date") or ""))
            except ValueError:
                return False
            if benchmark_date > record_date_value:
                return False
    prefilter_result = _mapping(replay_input.get("prefilter_result"))
    if prefilter_result.get("prefilter_status") != "accepted":
        return False
    if not isinstance(prefilter_result.get("prefilter_reasons"), list):
        return False
    return True


def _baseline_replay_mismatch_candidate_ids(
    baseline_rows: Sequence[Mapping[str, Any]],
) -> set[int]:
    mismatches: set[int] = set()
    checked: set[int] = set()
    for row in baseline_rows:
        candidate_id = row.get("candidate_id")
        if candidate_id is None:
            continue
        candidate_id_value = int(candidate_id)
        if candidate_id_value in checked:
            continue
        checked.add(candidate_id_value)
        snapshot = _mapping(row.get("candidate_snapshot"))
        if _baseline_replay_signature(snapshot) != row.get(
            "_baseline_replay_signature"
        ):
            mismatches.add(candidate_id_value)
    return mismatches


def _baseline_replay_signature(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "observation_score": payload.get("observation_score"),
        "primary_bucket": payload.get("primary_bucket"),
        "secondary_buckets": list(_as_list(payload.get("secondary_buckets"))),
        "bucket_scores": dict(_mapping(payload.get("bucket_scores"))),
        "risk_labels": list(_as_list(payload.get("risk_labels"))),
        "matched_rule_codes": [
            str(value)
            for value in _as_list(payload.get("matched_rule_codes"))
        ],
    }


def _is_finite_replay_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _scoring_candidate_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_rows: Sequence[Mapping[str, Any]],
    baseline_config: ScoringConfig,
    parameter: str,
    direction: int,
    step: float | int,
    cohort: Mapping[str, Any],
    watermarks: Sequence[Mapping[str, Any]],
    min_sample_count: int,
    min_validated_coverage: float,
    replay_coverage_ok: bool,
    replay_ranking_pool_complete: bool,
    replay_ranking_pool_status: str,
) -> dict[str, Any]:
    before_value = getattr(baseline_config, parameter)
    raw_after = before_value + (direction * step)
    after_value: float | int
    if parameter == "secondary_bucket_threshold":
        after_value = max(0, min(100, int(raw_after)))
    else:
        after_value = max(0.0, round(float(raw_after), 4))
    candidate_config = replace(baseline_config, **{parameter: after_value})
    candidate_rows = _replay_daily_radar_rows(rows, candidate_config)
    training_months = set(str(value) for value in _as_list(cohort.get("training_months")))
    holdout_month = str(cohort.get("holdout_month") or "")
    before_training = [row for row in baseline_rows if str(row["record_date"])[:7] in training_months]
    after_training = [row for row in candidate_rows if str(row["record_date"])[:7] in training_months]
    before_holdout = [row for row in baseline_rows if str(row["record_date"])[:7] == holdout_month]
    after_holdout = [row for row in candidate_rows if str(row["record_date"])[:7] == holdout_month]
    is_secondary_threshold = parameter == "secondary_bucket_threshold"
    selection_key = "selected_for_secondary" if is_secondary_threshold else "selected_for_rank"
    selection = lambda row: (
        row.get("status") == "validated"
        and row.get(selection_key) is True
    )
    primary_metric = _secondary_average_excess if is_secondary_threshold else _selected_average_excess
    downside_metric = _secondary_average_downside if is_secondary_threshold else _selected_average_downside
    objective_name = (
        "secondary_bucket_average_excess_return_pct"
        if is_secondary_threshold
        else f"top_{DEFAULT_RANK_CUTOFF}_average_excess_return_pct"
    )
    before_training_metrics = metrics_by_window(
        before_training,
        selection=selection,
    )
    after_training_metrics = metrics_by_window(
        after_training,
        selection=selection,
    )
    before_holdout_metrics = metrics_by_window(
        before_holdout,
        selection=selection,
    )
    after_holdout_metrics = metrics_by_window(
        after_holdout,
        selection=selection,
    )
    bootstrap = block_bootstrap_mean_delta(
        [row for row in before_training if selection(row)],
        [row for row in after_training if selection(row)],
        value=lambda row: _float_or_none(
            _mapping(row.get("outcome")).get(
                "excess_return_vs_benchmark_pct"
            )
        ),
        block_key="record_date",
        block_values=sorted(
            {
                row.get("record_date")
                for row in before_training
                if row.get("record_date") is not None
            }
            & {
                row.get("record_date")
                for row in after_training
                if row.get("record_date") is not None
            }
        ),
    )
    holdout_before = primary_metric(before_holdout)
    holdout_after = primary_metric(after_holdout)
    holdout_delta = _delta(holdout_after, holdout_before)
    downside_before = downside_metric(before_holdout)
    downside_after = downside_metric(after_holdout)
    selected_watermarks = [
        row
        for row in watermarks
        if str(row.get("month")) in set(_as_list(cohort.get("selected_months")))
    ]
    coverage_ok = bool(selected_watermarks) and all(
        all(
            (
                _mapping(row.get("validated_coverage_by_window")).get(
                    str(window)
                )
                or 0
            )
            >= min_validated_coverage
            for window in DEFAULT_FORWARD_WINDOWS
        )
        for row in selected_watermarks
    )
    ci_values = _as_list(bootstrap.get("ci_95"))
    lower_ci = _float_or_none(ci_values[0]) if ci_values else None
    training_delta = _float_or_none(bootstrap.get("delta"))
    training_sample_counts = independent_sample_counts_by_window(
        before_training,
        sample_key="candidate_id",
        selection=selection,
    )
    holdout_sample_counts = independent_sample_counts_by_window(
        before_holdout,
        sample_key="candidate_id",
        selection=selection,
    )
    after_training_sample_counts = independent_sample_counts_by_window(
        after_training,
        sample_key="candidate_id",
        selection=selection,
    )
    after_holdout_sample_counts = independent_sample_counts_by_window(
        after_holdout,
        sample_key="candidate_id",
        selection=selection,
    )
    enough_samples = (
        all(count >= min_sample_count for count in training_sample_counts.values())
        and all(count >= min_sample_count for count in holdout_sample_counts.values())
        and all(
            count >= min_sample_count
            for count in after_training_sample_counts.values()
        )
        and all(
            count >= min_sample_count
            for count in after_holdout_sample_counts.values()
        )
    )
    training_block_count = independent_block_count(
        before_training,
        block_key="record_date",
        selection=selection,
    )
    holdout_block_count = independent_block_count(
        before_holdout,
        block_key="record_date",
        selection=selection,
    )
    after_training_block_count = independent_block_count(
        after_training,
        block_key="record_date",
        selection=selection,
    )
    after_holdout_block_count = independent_block_count(
        after_holdout,
        block_key="record_date",
        selection=selection,
    )
    min_training_blocks, min_holdout_blocks = required_block_counts()
    enough_blocks = (
        training_block_count >= min_training_blocks
        and holdout_block_count >= min_holdout_blocks
        and after_training_block_count >= min_training_blocks
        and after_holdout_block_count >= min_holdout_blocks
    )
    downside_preserved = (
        downside_before is not None
        and downside_after is not None
        and downside_after >= downside_before - 0.25
    )
    holdout_horizon_gates: dict[str, dict[str, Any]] = {}
    for window in ("5", "10", "20"):
        before_window = _mapping(before_holdout_metrics.get(window))
        after_window = _mapping(after_holdout_metrics.get(window))
        before_excess = _float_or_none(
            before_window.get("average_excess_return_vs_benchmark_pct")
        )
        after_excess = _float_or_none(
            after_window.get("average_excess_return_vs_benchmark_pct")
        )
        before_downside = _float_or_none(
            before_window.get("average_downside_pct")
        )
        after_downside = _float_or_none(
            after_window.get("average_downside_pct")
        )
        excess_delta = _delta(after_excess, before_excess)
        downside_delta = _delta(after_downside, before_downside)
        holdout_horizon_gates[window] = {
            "before_excess_return_pct": before_excess,
            "after_excess_return_pct": after_excess,
            "excess_return_delta_pct": excess_delta,
            "minimum_excess_return_delta_pct": -0.05,
            "before_downside_pct": before_downside,
            "after_downside_pct": after_downside,
            "downside_delta_pct": downside_delta,
            "minimum_downside_delta_pct": -0.25,
            "preserved": (
                excess_delta is not None
                and excess_delta >= -0.05
                and downside_delta is not None
                and downside_delta >= -0.25
            ),
        }
    horizons_preserved = all(
        bool(gate["preserved"])
        for gate in holdout_horizon_gates.values()
    )
    eligible = (
        cohort.get("cohort_complete") is True
        and coverage_ok
        and replay_coverage_ok
        and replay_ranking_pool_complete
        and enough_samples
        and enough_blocks
        and lower_ci is not None
        and lower_ci >= 0
        and training_delta is not None
        and training_delta > 0
        and horizons_preserved
        and before_value != after_value
    )
    if cohort.get("cohort_complete") is not True:
        reason = "insufficient_mature_months"
    elif replay_ranking_pool_status == "capacity_exceeded":
        reason = "replay_workload_limit_exceeded"
    elif not coverage_ok:
        reason = "validated_coverage_below_threshold"
    elif not replay_coverage_ok:
        reason = "replay_coverage_below_threshold"
    elif not replay_ranking_pool_complete:
        reason = "replay_ranking_pool_incomplete"
    elif not enough_samples:
        reason = "insufficient_independent_samples"
    elif not enough_blocks:
        reason = "insufficient_date_blocks"
    elif lower_ci is None or lower_ci < 0 or training_delta is None or training_delta <= 0:
        reason = "training_bootstrap_not_positive"
    elif not horizons_preserved:
        reason = "holdout_horizon_degraded"
    else:
        reason = "eligible"
    return {
        "parameter": parameter,
        "step": direction * step,
        "before": before_value,
        "after": after_value,
        "candidate_config": candidate_config.to_dict(),
        "training": {
            "before": before_training_metrics,
            "after": after_training_metrics,
        },
        "holdout": {
            "before": before_holdout_metrics,
            "after": after_holdout_metrics,
        },
        "training_block_bootstrap": bootstrap,
        "holdout_primary_metric": {
            "metric": objective_name,
            "before": holdout_before,
            "after": holdout_after,
            "delta": holdout_delta,
        },
        "holdout_downside": {
            "before": downside_before,
            "after": downside_after,
            "preserved": downside_preserved,
        },
        "holdout_horizon_gates": holdout_horizon_gates,
        "coverage": {
            "training_selected_rows": sum(
                1 for row in before_training if selection(row)
            ),
            "holdout_selected_rows": sum(
                1 for row in before_holdout if selection(row)
            ),
            "training_independent_samples_by_window": training_sample_counts,
            "holdout_independent_samples_by_window": holdout_sample_counts,
            "after_training_independent_samples_by_window": after_training_sample_counts,
            "after_holdout_independent_samples_by_window": after_holdout_sample_counts,
            "training_block_count": training_block_count,
            "holdout_block_count": holdout_block_count,
            "after_training_block_count": after_training_block_count,
            "after_holdout_block_count": after_holdout_block_count,
            "min_training_block_count": min_training_blocks,
            "min_holdout_block_count": min_holdout_blocks,
            "selected_months_meet_validated_coverage": coverage_ok,
            "selected_months_meet_replay_coverage": replay_coverage_ok,
            "replay_ranking_pool_complete": replay_ranking_pool_complete,
            "replay_ranking_pool_status": replay_ranking_pool_status,
        },
        "auto_change_eligible": eligible,
        "eligibility_reason": reason,
    }


def _replay_daily_radar_rows(
    rows: Sequence[Mapping[str, Any]],
    config: ScoringConfig,
    *,
    excluded_rule_codes: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    replayed: list[dict[str, Any]] = []
    scored_by_candidate: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        snapshot = _mapping(row.get("candidate_snapshot"))
        replay_input = _mapping(_mapping(snapshot.get("input_snapshot")).get("replay_input"))
        candidate_id = row.get("candidate_id")
        scored = (
            scored_by_candidate.get(int(candidate_id))
            if candidate_id is not None
            else None
        )
        if scored is None:
            scored = score_daily_radar_record(
                _mapping(replay_input.get("record")),
                market_context=_mapping(replay_input.get("market_context")),
                prefilter_result=_mapping(replay_input.get("prefilter_result")),
                config=config,
                excluded_rule_codes=excluded_rule_codes,
            )
            if candidate_id is not None:
                scored_by_candidate[int(candidate_id)] = scored
        replayed.append(
            dict(row)
            | {
                "record_date": snapshot.get("record_date") or row.get("signal_date"),
                "replayed_score": int(scored["observation_score"]),
                "_baseline_replay_signature": _baseline_replay_signature({
                    "observation_score": scored["observation_score"],
                    "primary_bucket": scored["primary_bucket"],
                    "secondary_buckets": scored["secondary_buckets"],
                    "bucket_scores": scored["bucket_scores"],
                    "risk_labels": scored["risk_labels"],
                    "matched_rule_codes": [
                        rule["rule_id"]
                        for rule in _as_list(scored.get("matched_rules"))
                        if isinstance(rule, Mapping) and rule.get("rule_id")
                    ],
                }),
                "selected_for_secondary": bool(scored["secondary_buckets"]),
                "selected_for_rank": False,
            }
        )
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in replayed:
        grouped[(str(row["record_date"]), int(row["window_days"]))].append(row)
    for group_rows in grouped.values():
        ordered = sorted(
            group_rows,
            key=lambda row: (
                -int(row["replayed_score"]),
                str(row.get("symbol") or ""),
            ),
        )
        for row in ordered[:DEFAULT_RANK_CUTOFF]:
            row["selected_for_rank"] = True
    return replayed


def _selected_average_excess(rows: Sequence[Mapping[str, Any]]) -> float | None:
    metrics = outcome_metrics(
        rows,
        selection=lambda row: (
            row.get("status") == "validated"
            and row.get("selected_for_rank") is True
        ),
    )
    return _float_or_none(metrics.get("average_excess_return_vs_benchmark_pct"))


def _selected_average_downside(rows: Sequence[Mapping[str, Any]]) -> float | None:
    metrics = outcome_metrics(
        rows,
        selection=lambda row: (
            row.get("status") == "validated"
            and row.get("selected_for_rank") is True
        ),
    )
    return _float_or_none(metrics.get("average_downside_pct"))


def _selected_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if row.get("status") == "validated"
        and row.get("selected_for_rank") is True
    )


def _secondary_average_excess(rows: Sequence[Mapping[str, Any]]) -> float | None:
    metrics = outcome_metrics(
        rows,
        selection=lambda row: (
            row.get("status") == "validated"
            and row.get("selected_for_secondary") is True
        ),
    )
    return _float_or_none(metrics.get("average_excess_return_vs_benchmark_pct"))


def _secondary_average_downside(rows: Sequence[Mapping[str, Any]]) -> float | None:
    metrics = outcome_metrics(
        rows,
        selection=lambda row: (
            row.get("status") == "validated"
            and row.get("selected_for_secondary") is True
        ),
    )
    return _float_or_none(metrics.get("average_downside_pct"))


def _secondary_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if row.get("status") == "validated"
        and row.get("selected_for_secondary") is True
    )


def _rule_groups(row: Mapping[str, Any]) -> list[str]:
    registry = get_rule_registry()
    groups: set[str] = set()
    for code in _rule_codes(row):
        entry = registry.get(code)
        if entry and entry.ablation_group:
            groups.add(entry.ablation_group)
    risk_labels = set(_as_list(_mapping(row.get("candidate_snapshot")).get("risk_labels")))
    if risk_labels & {"margin_crowding"}:
        groups.add("margin_related_risk_labels")
    if risk_labels & {"market_weakness", "overextended", "data_gap"}:
        groups.add("market_regime_penalty")
    relative_bucket = str(_mapping(row.get("candidate_snapshot")).get("relative_strength_bucket") or "")
    if relative_bucket and relative_bucket not in {"missing", "unknown"}:
        groups.add("relative_strength")
    return sorted(groups)


def _rule_codes(row: Mapping[str, Any]) -> list[str]:
    snapshot = _mapping(row.get("candidate_snapshot"))
    codes = snapshot.get("matched_rule_codes")
    if isinstance(codes, list):
        return [str(code) for code in codes]
    matched_rules = snapshot.get("matched_rules")
    return _matched_rule_codes(matched_rules)


def _matched_rule_codes(value: Any) -> list[str]:
    codes: list[str] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            codes.append(str(item.get("rule_id") or item.get("rule_code") or "unknown_rule"))
        else:
            codes.append(str(item))
    return codes


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    forward_returns = [_float_or_none(_mapping(row.get("outcome")).get("forward_return_pct")) for row in rows]
    excess_returns = [_float_or_none(_mapping(row.get("outcome")).get("excess_return_vs_benchmark_pct")) for row in rows]
    positives = [value for value in forward_returns if value is not None and value > 0]
    negatives = [value for value in forward_returns if value is not None and value < 0]
    return {
        "sample_count": len(rows),
        "average_forward_return_pct": _average(forward_returns),
        "average_excess_return_vs_benchmark_pct": _average(excess_returns),
        "hit_rate_above_threshold": _ratio(
            sum(1 for row in rows if _mapping(row.get("outcome")).get("hit_above_threshold") is True),
            len(rows),
        ),
        "profit_factor_like_ratio": _profit_factor_like_ratio(positives, negatives),
    }


def _sample_summary(rows: Sequence[Mapping[str, Any]], windows: Sequence[int]) -> dict[str, Any]:
    validated = [row for row in rows if row.get("status") == "validated"]
    skipped = [row for row in rows if row.get("status") == "skipped"]
    missing = [row for row in rows if row.get("status") == "missing"]
    validated_by_window = Counter(int(row["window_days"]) for row in validated)
    skipped_by_window = Counter(int(row["window_days"]) for row in skipped)
    missing_by_window = Counter(int(row["window_days"]) for row in missing)
    return {
        "candidate_window_count": len(rows),
        "evaluated_sample_count": len(validated) + len(skipped),
        "validated_sample_count": len(validated),
        "skipped_sample_count": len(skipped),
        "missing_sample_count": len(missing),
        "validated_by_window": {str(window): validated_by_window[window] for window in windows},
        "skipped_by_window": {str(window): skipped_by_window[window] for window in windows},
        "missing_by_window": {str(window): missing_by_window[window] for window in windows},
    }


def _registry_summary() -> dict[str, Any]:
    entries = registry_payload()
    return {
        "entry_count": len(entries),
        "by_tier": dict(sorted(Counter(str(entry["tier"]) for entry in entries).items())),
        "by_validation_status": dict(sorted(Counter(str(entry["validation_status"]) for entry in entries).items())),
    }


def _skip_reasons(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("skip_reason")) for row in rows if row.get("skip_reason")).items()))


def _version_manifest() -> dict[str, Any]:
    return {
        "scoring_version": SCORING_VERSION,
        "rule_version": RULE_VERSION,
        "scoring_config_version": SCORING_CONFIG_VERSION,
        "rule_review_report_version": RULE_REVIEW_REPORT_VERSION,
        "baseline_config": ScoringConfig().to_dict(),
        "candidate_constraints": {
            "one_parameter_per_candidate": True,
            "one_step_per_candidate": True,
            "step_sizes": dict(TUNABLE_SCORING_PARAMETERS),
            "increase_only_parameters": sorted(INCREASE_ONLY_SCORING_PARAMETERS),
        },
        "live_scoring_changed": False,
        "live_ranking_changed": False,
        "automated_recommendations_only": True,
    }


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end_exclusive = date(year + 1, 1, 1)
    else:
        end_exclusive = date(year, month + 1, 1)

    return start, end_exclusive - timedelta(days=1)


def _market_regime(candidate: DailyRadarCandidate) -> str:
    market_context = _mapping(_mapping(candidate.input_snapshot).get("market_context"))
    score_market = _mapping(_mapping(_mapping(candidate.score_breakdown).get("market_context")).get("details"))
    return str(market_context.get("regime") or score_market.get("regime") or "unknown")


def _relative_strength_bucket(score_breakdown: Mapping[str, Any]) -> str:
    relative_strength = _mapping(score_breakdown.get("relative_strength"))
    freshness = str(relative_strength.get("freshness") or "")
    if freshness and freshness != "fresh":
        return freshness
    value = _float_or_none(relative_strength.get("relative_value"))
    if value is None:
        return "missing"
    if value >= 0.02:
        return "positive"
    if value <= -0.02:
        return "negative"
    return "neutral"


def _score_decile(score: int) -> str:
    lower = min(90, max(0, (int(score) // 10) * 10))
    return f"{lower:02d}-{lower + 9:02d}"


def _average(values: Iterable[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _profit_factor_like_ratio(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    positive_total = sum(positives)
    negative_total = abs(sum(negatives))
    if negative_total == 0:
        return None if positive_total == 0 else round(positive_total, 4)
    return round(positive_total / negative_total, 4)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _markdown_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


__all__ = [
    "DEFAULT_ABLATION_GROUPS",
    "DEFAULT_MIN_SAMPLE_COUNT",
    "RULE_REVIEW_REPORT_VERSION",
    "MonthlyRuleReviewReport",
    "build_ablation_report",
    "build_monthly_rule_review_report",
    "render_rule_review_markdown",
    "validation_rows_from_results",
    "write_rule_review_artifacts",
]
