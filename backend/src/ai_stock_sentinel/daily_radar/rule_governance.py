from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.governance import (
    DEFAULT_MIN_VALIDATED_COVERAGE,
    block_bootstrap_delta,
    metrics_by_window,
    outcome_metrics,
    select_training_and_holdout_months,
    validated_coverage,
)
from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_FORWARD_WINDOWS,
    FORWARD_VALIDATION_VERSION,
)
from ai_stock_sentinel.daily_radar.repository import PUBLIC_RUN_STATUSES
from ai_stock_sentinel.daily_radar.rule_registry import (
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


RULE_REVIEW_REPORT_VERSION = "daily-radar-rule-review-v1"
DEFAULT_MIN_SAMPLE_COUNT = 20
DEFAULT_RANK_CUTOFF = 20
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
    windows = sorted({int(row["window_days"]) for row in validated_rows}) or list(DEFAULT_FORWARD_WINDOWS)
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
            "positioning": "rule_quality_governance_diagnostic_not_live_scoring_change",
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
) -> MonthlyRuleReviewReport:
    month_start, month_end = _month_bounds(year, month)
    all_rows = validation_rows_from_results(
        session,
        market=market,
        start_date=date(2000, 1, 1),
        end_date=month_end,
        validation_version=validation_version,
    )
    rows = [
        row
        for row in all_rows
        if month_start <= (_parse_date(row.get("signal_date")) or date.min) <= month_end
    ]
    version = validation_version or _dominant_validation_version(rows) or FORWARD_VALIDATION_VERSION
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
        rows=all_rows,
    )
    cohort = select_training_and_holdout_months(watermarks)
    selected_months = set(_as_list(cohort.get("selected_months")))
    optimizer_rows = [
        row
        for row in all_rows
        if row.get("status") == "validated"
        and str(row.get("signal_date") or "")[:7] in selected_months
    ]
    candidate_configs, replay_coverage = _scoring_candidate_reports(
        optimizer_rows,
        baseline_config=ScoringConfig(),
        cohort=cohort,
        watermarks=watermarks,
        min_sample_count=min_sample_count,
        min_validated_coverage=min_validated_coverage,
    )
    report = {
        "metadata": {
            "report_version": RULE_REVIEW_REPORT_VERSION,
            "market": market,
            "month": f"{year:04d}-{month:02d}",
            "sample_source": "production_db",
            "validation_version": version,
            "min_sample_count": min_sample_count,
            "min_validated_coverage": min_validated_coverage,
            "positioning": "automated_rule_quality_recommendations_not_human_approved_strategy_update",
        },
        "sample_summary": ablation["sample_summary"],
        "registry_summary": _registry_summary(),
        "ablation_summary": ablation["ablation_groups"],
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
    start_date: date,
    end_date: date,
    validation_version: str | None = None,
) -> list[dict[str, Any]]:
    run_query = (
        select(DailyRadarRun)
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.status.in_(PUBLIC_RUN_STATUSES),
            DailyRadarRun.run_date >= start_date,
            DailyRadarRun.run_date <= end_date,
        )
        .order_by(
            DailyRadarRun.run_date.asc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
        )
    )
    latest_run_by_date: dict[date, int] = {}
    for run in session.scalars(run_query).all():
        latest_run_by_date.setdefault(run.run_date, run.id)
    latest_run_ids = set(latest_run_by_date.values())
    if not latest_run_ids:
        return []

    query = (
        select(DailyRadarForwardValidationResult, DailyRadarCandidate, DailyRadarRun)
        .join(DailyRadarCandidate, DailyRadarForwardValidationResult.candidate_id == DailyRadarCandidate.id)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.run_date >= start_date,
            DailyRadarRun.run_date <= end_date,
            DailyRadarRun.id.in_(latest_run_ids),
        )
        .order_by(
            DailyRadarRun.run_date.asc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
            DailyRadarCandidate.symbol.asc(),
            DailyRadarForwardValidationResult.window_days.asc(),
        )
    )
    if validation_version:
        query = query.where(DailyRadarForwardValidationResult.validation_version == validation_version)
    return [
        _row_from_result(result, candidate, run)
        for result, candidate, run in session.execute(query).all()
    ]


def render_rule_review_markdown(report: Mapping[str, Any]) -> str:
    metadata = _mapping(report.get("metadata"))
    summary = _mapping(report.get("sample_summary"))
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
        f"- Validated samples: {summary.get('validated_sample_count', 0)}",
        f"- Skipped samples: {summary.get('skipped_sample_count', 0)}",
        "",
        "## Automated Ablation Recommendations",
        "",
        "| Group | Window | With sample | Delta excess pct | Recommendation |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in _as_list(report.get("ablation_summary")):
        lines.append(
            "| {group} | {window} | {sample} | {delta} | {recommendation} |".format(
                group=row.get("group"),
                window=row.get("window_days"),
                sample=row.get("sample_count_with_group"),
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
    result: DailyRadarForwardValidationResult,
    candidate: DailyRadarCandidate,
    run: DailyRadarRun,
) -> dict[str, Any]:
    snapshot = _candidate_snapshot(candidate, run)
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
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    query = (
        select(DailyRadarCandidate, DailyRadarRun)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.status.in_(PUBLIC_RUN_STATUSES),
            DailyRadarRun.run_date <= through_date,
        )
        .order_by(
            DailyRadarRun.run_date.asc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
            DailyRadarCandidate.id.asc(),
        )
    )
    latest_run_by_date: dict[date, int] = {}
    expected_by_month: dict[str, set[int]] = defaultdict(set)
    for candidate, run in session.execute(query).all():
        selected_run_id = latest_run_by_date.setdefault(run.run_date, run.id)
        if run.id == selected_run_id:
            expected_by_month[f"{run.run_date.year:04d}-{run.run_date.month:02d}"].add(candidate.id)

    results_by_month: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if (
            int(row.get("window_days") or 0) == 20
            and str(row.get("validation_version") or validation_version) == validation_version
        ):
            signal_date = _parse_date(row.get("signal_date"))
            if signal_date is not None:
                results_by_month[f"{signal_date.year:04d}-{signal_date.month:02d}"].append(row)

    watermarks: list[dict[str, Any]] = []
    for month in sorted(expected_by_month):
        expected_ids = expected_by_month[month]
        month_rows = results_by_month.get(month, [])
        evaluated_ids = {int(row["candidate_id"]) for row in month_rows}
        validated_ids = {
            int(row["candidate_id"])
            for row in month_rows
            if row.get("status") == "validated"
        }
        watermarks.append({
            "month": month,
            "expected_20d_samples": len(expected_ids),
            "evaluated_20d_samples": len(evaluated_ids),
            "validated_20d_samples": len(validated_ids),
            "maturity_complete": bool(expected_ids) and expected_ids <= evaluated_ids,
            "validated_coverage": validated_coverage(len(validated_ids), len(expected_ids)),
        })
    return watermarks


def _scoring_candidate_reports(
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_config: ScoringConfig,
    cohort: Mapping[str, Any],
    watermarks: Sequence[Mapping[str, Any]],
    min_sample_count: int,
    min_validated_coverage: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    eligible_rows: list[dict[str, Any]] = []
    exclusions: Counter[str] = Counter()
    for row in rows:
        snapshot = _mapping(row.get("candidate_snapshot"))
        replay_input = _mapping(_mapping(snapshot.get("input_snapshot")).get("replay_input"))
        if replay_input.get("schema_version") != "daily-radar-replay-input-v1":
            exclusions["replay_input_incomplete"] += 1
            continue
        eligible_rows.append(dict(row))
    reports = [
        _scoring_candidate_report(
            eligible_rows,
            baseline_config=baseline_config,
            parameter=parameter,
            direction=direction,
            step=step,
            cohort=cohort,
            watermarks=watermarks,
            min_sample_count=min_sample_count,
            min_validated_coverage=min_validated_coverage,
        )
        for parameter, step in TUNABLE_SCORING_PARAMETERS.items()
        for direction in ((1,) if parameter in INCREASE_ONLY_SCORING_PARAMETERS else (-1, 1))
    ]
    return reports, {
        "selected_validation_rows": len(rows),
        "replay_eligible_rows": len(eligible_rows),
        "coverage": validated_coverage(len(eligible_rows), len(rows)),
        "exclusion_reasons": dict(sorted(exclusions.items())),
    }


def _scoring_candidate_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_config: ScoringConfig,
    parameter: str,
    direction: int,
    step: float | int,
    cohort: Mapping[str, Any],
    watermarks: Sequence[Mapping[str, Any]],
    min_sample_count: int,
    min_validated_coverage: float,
) -> dict[str, Any]:
    before_value = getattr(baseline_config, parameter)
    raw_after = before_value + (direction * step)
    after_value: float | int
    if parameter == "secondary_bucket_threshold":
        after_value = max(0, min(100, int(raw_after)))
    else:
        after_value = max(0.0, round(float(raw_after), 4))
    candidate_config = replace(baseline_config, **{parameter: after_value})
    baseline_rows = _replay_daily_radar_rows(rows, baseline_config)
    candidate_rows = _replay_daily_radar_rows(rows, candidate_config)
    training_months = set(str(value) for value in _as_list(cohort.get("training_months")))
    holdout_month = str(cohort.get("holdout_month") or "")
    before_training = [row for row in baseline_rows if str(row["record_date"])[:7] in training_months]
    after_training = [row for row in candidate_rows if str(row["record_date"])[:7] in training_months]
    before_holdout = [row for row in baseline_rows if str(row["record_date"])[:7] == holdout_month]
    after_holdout = [row for row in candidate_rows if str(row["record_date"])[:7] == holdout_month]
    is_secondary_threshold = parameter == "secondary_bucket_threshold"
    selection_key = "selected_for_secondary" if is_secondary_threshold else "selected_for_rank"
    selection = lambda row: row.get(selection_key) is True
    primary_metric = _secondary_average_excess if is_secondary_threshold else _selected_average_excess
    downside_metric = _secondary_average_downside if is_secondary_threshold else _selected_average_downside
    count_metric = _secondary_count if is_secondary_threshold else _selected_count
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
    bootstrap = block_bootstrap_delta(
        before_training,
        after_training,
        metric=primary_metric,
        block_key="record_date",
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
        (row.get("validated_coverage") or 0) >= min_validated_coverage
        for row in selected_watermarks
    )
    ci_values = _as_list(bootstrap.get("ci_95"))
    lower_ci = _float_or_none(ci_values[0]) if ci_values else None
    training_delta = _float_or_none(bootstrap.get("delta"))
    enough_samples = (
        count_metric(before_training) >= min_sample_count
        and count_metric(before_holdout) >= min_sample_count
    )
    downside_preserved = (
        downside_before is not None
        and downside_after is not None
        and downside_after >= downside_before - 0.25
    )
    eligible = (
        cohort.get("cohort_complete") is True
        and coverage_ok
        and enough_samples
        and lower_ci is not None
        and lower_ci >= 0
        and training_delta is not None
        and training_delta > 0
        and holdout_delta is not None
        and holdout_delta >= -0.05
        and downside_preserved
        and before_value != after_value
    )
    if cohort.get("cohort_complete") is not True:
        reason = "insufficient_mature_months"
    elif not coverage_ok:
        reason = "validated_coverage_below_threshold"
    elif not enough_samples:
        reason = "insufficient_samples"
    elif lower_ci is None or lower_ci < 0 or training_delta is None or training_delta <= 0:
        reason = "training_bootstrap_not_positive"
    elif holdout_delta is None or holdout_delta < -0.05:
        reason = "holdout_not_preserved"
    elif not downside_preserved:
        reason = "holdout_downside_worsened"
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
        "coverage": {
            "training_selected_rows": count_metric(before_training),
            "holdout_selected_rows": count_metric(before_holdout),
            "selected_months_meet_validated_coverage": coverage_ok,
        },
        "auto_change_eligible": eligible,
        "eligibility_reason": reason,
    }


def _replay_daily_radar_rows(
    rows: Sequence[Mapping[str, Any]],
    config: ScoringConfig,
) -> list[dict[str, Any]]:
    replayed: list[dict[str, Any]] = []
    for row in rows:
        snapshot = _mapping(row.get("candidate_snapshot"))
        replay_input = _mapping(_mapping(snapshot.get("input_snapshot")).get("replay_input"))
        scored = score_daily_radar_record(
            _mapping(replay_input.get("record")),
            market_context=_mapping(replay_input.get("market_context")),
            prefilter_result=_mapping(replay_input.get("prefilter_result")),
            config=config,
        )
        replayed.append(
            dict(row)
            | {
                "record_date": snapshot.get("record_date") or row.get("signal_date"),
                "replayed_score": int(scored["observation_score"]),
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
        selection=lambda row: row.get("selected_for_rank") is True,
    )
    return _float_or_none(metrics.get("average_excess_return_vs_benchmark_pct"))


def _selected_average_downside(rows: Sequence[Mapping[str, Any]]) -> float | None:
    metrics = outcome_metrics(
        rows,
        selection=lambda row: row.get("selected_for_rank") is True,
    )
    return _float_or_none(metrics.get("average_downside_pct"))


def _selected_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("selected_for_rank") is True)


def _secondary_average_excess(rows: Sequence[Mapping[str, Any]]) -> float | None:
    metrics = outcome_metrics(
        rows,
        selection=lambda row: row.get("selected_for_secondary") is True,
    )
    return _float_or_none(metrics.get("average_excess_return_vs_benchmark_pct"))


def _secondary_average_downside(rows: Sequence[Mapping[str, Any]]) -> float | None:
    metrics = outcome_metrics(
        rows,
        selection=lambda row: row.get("selected_for_secondary") is True,
    )
    return _float_or_none(metrics.get("average_downside_pct"))


def _secondary_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("selected_for_secondary") is True)


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
    validated_by_window = Counter(int(row["window_days"]) for row in validated)
    skipped_by_window = Counter(int(row["window_days"]) for row in skipped)
    return {
        "evaluated_sample_count": len(rows),
        "validated_sample_count": len(validated),
        "skipped_sample_count": len(skipped),
        "validated_by_window": {str(window): validated_by_window[window] for window in windows},
        "skipped_by_window": {str(window): skipped_by_window[window] for window in windows},
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


def _dominant_validation_version(rows: Sequence[Mapping[str, Any]]) -> str | None:
    versions = Counter(str(row.get("validation_version")) for row in rows if row.get("validation_version"))
    if not versions:
        return None
    return versions.most_common(1)[0][0]


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
