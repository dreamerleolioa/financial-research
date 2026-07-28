from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, and_, case, cast, extract, func, or_, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.confidence_scorer import (
    BASE_CONFIDENCE,
    CONFIDENCE_CONFIG_VERSION,
    ConfidenceScoringConfig,
    compute_confidence,
)
from ai_stock_sentinel.calibration.governance import (
    DEFAULT_MIN_REPLAY_COVERAGE,
    DEFAULT_MIN_VALIDATED_COVERAGE,
    block_bootstrap_delta,
    confidence_excess_correlation,
    independent_block_count,
    independent_sample_counts_by_window,
    metrics_by_window,
    month_bounds,
    month_key,
    replay_coverage_summary,
    required_block_counts,
    select_training_and_holdout_months,
    validated_coverage,
)
from ai_stock_sentinel.calibration.forward_validation import (
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_FORWARD_WINDOWS,
    ForwardValidationAdapter,
    close_on,
    due_windows_by_candidate,
    evaluate_forward_validation,
    number as forward_number,
)
from ai_stock_sentinel.config import STRATEGY_VERSION
from ai_stock_sentinel.db.models import (
    AnalysisCalibrationSample,
    AnalysisForwardValidationResult,
)


ANALYSIS_FORWARD_VALIDATION_VERSION = "general-analysis-forward-validation-v1"
ANALYSIS_CALIBRATION_REPORT_VERSION = "general-analysis-confidence-review-v3"
GENERAL_REPLAY_INPUT_VERSION = "general-analysis-replay-input-v1"
GENERAL_REPLAY_CACHE_KEY = "_calibration_replay_input"
GENERAL_ANALYSIS_TYPES = ("general",)
OPTIMIZER_STRATEGY_TYPES = ("short_term", "mid_term")
TUNABLE_CONFIDENCE_PARAMETERS = (
    "positive_sentiment_points",
    "institutional_accumulation_points",
    "bullish_technical_points",
    "three_resonance_bonus",
)
logger = logging.getLogger(__name__)


def capture_general_analysis_calibration_sample(
    session: Session,
    *,
    symbol: str,
    record_date: date,
    result: Mapping[str, Any],
    is_final: bool,
    strategy_version: str = STRATEGY_VERSION,
    replay_input: Mapping[str, Any] | None = None,
) -> AnalysisCalibrationSample | None:
    if not is_final:
        return None
    partition = _general_analysis_calibration_partition(symbol)
    if partition is None:
        logger.info(
            "Skip unsupported general-analysis calibration market for %s",
            symbol,
        )
        return None
    market, benchmark_symbol = partition
    active_replay_input = (
        dict(replay_input)
        if replay_input is not None
        else build_general_analysis_replay_input(result)
    )
    if not _is_complete_replay_input(active_replay_input):
        logger.warning(
            "Skip incomplete general-analysis calibration replay input for %s on %s",
            symbol,
            record_date,
        )
        return None
    input_hash = _canonical_hash(active_replay_input)
    existing = session.execute(
        select(AnalysisCalibrationSample).where(
            AnalysisCalibrationSample.analysis_type == "general",
            AnalysisCalibrationSample.market == market,
            AnalysisCalibrationSample.symbol == symbol,
            AnalysisCalibrationSample.record_date == record_date,
            AnalysisCalibrationSample.strategy_version == strategy_version,
            AnalysisCalibrationSample.input_hash == input_hash,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    action_plan = _mapping(result.get("action_plan"))
    sample = AnalysisCalibrationSample(
        symbol=symbol,
        record_date=record_date,
        analysis_type="general",
        market=market,
        benchmark_symbol=benchmark_symbol,
        strategy_version=strategy_version,
        confidence_config_version=CONFIDENCE_CONFIG_VERSION,
        input_hash=input_hash,
        replay_input=active_replay_input,
        output_snapshot={
            "signal_confidence": result.get("signal_confidence"),
            "strategy_type": result.get("strategy_type"),
            "conviction_level": action_plan.get("conviction_level"),
            "action_tag": result.get("action_plan_tag"),
        },
        signal_confidence=_decimal_or_none(result.get("signal_confidence")),
        strategy_type=_string_or_none(result.get("strategy_type")),
        conviction_level=_string_or_none(action_plan.get("conviction_level")),
        action_tag=_string_or_none(result.get("action_plan_tag")),
        analysis_is_final=True,
    )
    session.add(sample)
    session.flush()
    return sample


def build_general_analysis_replay_input(result: Mapping[str, Any]) -> dict[str, Any]:
    cleaned_news = _mapping(result.get("cleaned_news"))
    institutional = _mapping(result.get("institutional_flow"))
    quality = _mapping(result.get("cleaned_news_quality"))
    flags = [str(flag) for flag in _as_list(quality.get("quality_flags"))]
    snapshot = _mapping(result.get("snapshot"))
    current_price = _number(snapshot.get("current_price"))
    if current_price is None:
        current_price = _last_number(snapshot.get("recent_closes"))
    return {
        "schema_version": GENERAL_REPLAY_INPUT_VERSION,
        "base_score": BASE_CONFIDENCE,
        "news_sentiment": str(cleaned_news.get("sentiment_label") or "neutral"),
        "sentiment_strength": _number(cleaned_news.get("sentiment_strength")) or 1.0,
        "institutional_flow": (
            "unknown"
            if not institutional or institutional.get("error")
            else str(institutional.get("flow_label") or "unknown")
        ),
        "technical_signal": str(result.get("technical_signal") or "sideways"),
        "date_unknown": "DATE_UNKNOWN" in flags,
        "entry_price": current_price,
        "baseline_config": ConfidenceScoringConfig().to_dict(),
    }


def general_validation_samples(
    session: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    market: str = "TW",
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
) -> list[dict[str, Any]]:
    query = select(AnalysisCalibrationSample).where(
        AnalysisCalibrationSample.analysis_type.in_(GENERAL_ANALYSIS_TYPES),
        AnalysisCalibrationSample.analysis_is_final.is_(True),
        AnalysisCalibrationSample.market == market,
        AnalysisCalibrationSample.benchmark_symbol == benchmark_symbol,
    )
    if start_date is not None:
        query = query.where(AnalysisCalibrationSample.record_date >= start_date)
    if end_date is not None:
        query = query.where(AnalysisCalibrationSample.record_date <= end_date)
    samples = session.scalars(
        query.order_by(
            AnalysisCalibrationSample.record_date.asc(),
            AnalysisCalibrationSample.symbol.asc(),
            AnalysisCalibrationSample.id.asc(),
        )
    ).all()
    return [_sample_snapshot(sample) for sample in samples]


def evaluate_general_analysis_forward_validation(
    samples: Sequence[Mapping[str, Any]],
    *,
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    as_of_date: date,
    market: str = "TW",
    windows: Sequence[int] = DEFAULT_FORWARD_WINDOWS,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
    due_only: bool = True,
    windows_by_sample: Mapping[str, Sequence[int]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    active_windows_by_sample = windows_by_sample
    if due_only and active_windows_by_sample is None:
        active_windows_by_sample = due_windows_by_candidate(
            samples,
            adapter=GENERAL_ANALYSIS_FORWARD_ADAPTER,
            as_of_date=as_of_date,
            windows=windows,
            price_series_by_symbol=price_series_by_symbol,
            benchmark_prices=benchmark_prices,
        )
    evaluation = evaluate_forward_validation(
        samples,
        price_series_by_symbol=price_series_by_symbol,
        benchmark_prices=benchmark_prices,
        adapter=GENERAL_ANALYSIS_FORWARD_ADAPTER,
        as_of_date=as_of_date,
        windows=windows,
        benchmark_symbol=benchmark_symbol,
        validation_version=ANALYSIS_FORWARD_VALIDATION_VERSION,
        hit_threshold_pct=3.0,
        windows_by_candidate=active_windows_by_sample,
    )
    outcomes = [
        dict(outcome) | {"sample_id": _sample_id_from_candidate(outcome)}
        for outcome in evaluation.outcomes
    ]
    report = {
        "metadata": {
            "report_version": "general-analysis-forward-validation-report-v1",
            "validation_version": ANALYSIS_FORWARD_VALIDATION_VERSION,
            "market": market,
            "sample_source": "general_analysis_calibration_samples",
            "as_of_date": as_of_date.isoformat(),
            "windows": evaluation.active_windows,
            "benchmark_symbol": benchmark_symbol,
            "hit_threshold_pct": 3.0,
            "positioning": "rule_quality_calibration_diagnostic_not_performance_marketing",
        },
        "sample_summary": _forward_sample_summary(
            candidate_count=evaluation.candidate_count,
            outcomes=evaluation.outcomes,
            windows=evaluation.active_windows,
        ),
        "bucket_outcomes": {},
        "secondary_bucket_outcomes": {},
        "rule_outcomes": {},
        "risk_label_outcomes": {},
        "market_regime_outcomes": {},
        "relative_strength_bucket_outcomes": {},
        "repeat_status_outcomes": {},
        "score_decile_outcomes": {},
        "data_freshness_outcomes": {},
        "ablation_candidates": [],
        "skip_reasons": evaluation.skipped_reasons,
        "version_manifest": {
            "validation_version": ANALYSIS_FORWARD_VALIDATION_VERSION,
            "report_version": "general-analysis-forward-validation-report-v1",
            "live_scoring_changed": False,
            "diagnostic_only": True,
        },
    }
    report["metadata"] = dict(report["metadata"]) | {
        "track": "general_analysis",
        "optimizer_scope": "final_/analyze_only",
    }
    return report, outcomes


def upsert_general_analysis_validation_results(
    session: Session,
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    written = validated = skipped = 0
    for outcome in outcomes:
        sample_id = outcome.get("sample_id")
        if sample_id is None:
            continue
        existing = session.execute(
            select(AnalysisForwardValidationResult).where(
                AnalysisForwardValidationResult.sample_id == int(sample_id),
                AnalysisForwardValidationResult.window_days == int(outcome["window_days"]),
                AnalysisForwardValidationResult.validation_version == str(outcome["validation_version"]),
            )
        ).scalar_one_or_none()
        payload = dict(_mapping(outcome.get("outcome")))
        if outcome.get("status") == "skipped":
            payload = {"skip_reason": outcome.get("skip_reason")}
        if existing is None:
            existing = AnalysisForwardValidationResult(
                sample_id=int(sample_id),
                window_days=int(outcome["window_days"]),
                validation_version=str(outcome["validation_version"]),
                status=str(outcome["status"]),
                signal_date=_parse_date(outcome.get("signal_date")) or date.min,
                target_date=_parse_date(outcome.get("target_date")),
                benchmark_symbol=str(outcome.get("benchmark_symbol") or ""),
                outcome=payload,
                skip_reason=_string_or_none(outcome.get("skip_reason")),
            )
        else:
            existing.status = str(outcome["status"])
            existing.signal_date = _parse_date(outcome.get("signal_date")) or date.min
            existing.target_date = _parse_date(outcome.get("target_date"))
            existing.benchmark_symbol = str(outcome.get("benchmark_symbol") or "")
            existing.outcome = payload
            existing.skip_reason = _string_or_none(outcome.get("skip_reason"))
        session.add(existing)
        written += 1
        if outcome.get("status") == "validated":
            validated += 1
        else:
            skipped += 1
    session.flush()
    return {
        "records_written": written,
        "validated_count": validated,
        "skipped_count": skipped,
    }


def build_general_analysis_monthly_report(
    session: Session,
    *,
    through_year: int,
    through_month: int,
    min_sample_count: int = 20,
    min_validated_coverage: float = DEFAULT_MIN_VALIDATED_COVERAGE,
    min_replay_coverage: float = DEFAULT_MIN_REPLAY_COVERAGE,
    market: str = "TW",
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
) -> tuple[dict[str, Any], str]:
    through_date = _month_end(through_year, through_month)
    watermarks = _general_completeness_watermarks(
        session,
        through_date=through_date,
        market=market,
        benchmark_symbol=benchmark_symbol,
    )
    cohort = select_training_and_holdout_months(watermarks)
    selected_months = [str(value) for value in cohort["selected_months"]]
    selected_month_filter = _month_filter(
        AnalysisCalibrationSample.record_date,
        selected_months,
    )
    samples = session.scalars(
        select(AnalysisCalibrationSample)
        .where(
            AnalysisCalibrationSample.analysis_type == "general",
            AnalysisCalibrationSample.analysis_is_final.is_(True),
            AnalysisCalibrationSample.market == market,
            AnalysisCalibrationSample.benchmark_symbol == benchmark_symbol,
            selected_month_filter,
        )
        .order_by(AnalysisCalibrationSample.record_date.asc(), AnalysisCalibrationSample.id.asc())
    ).all()
    rows = _validation_rows(
        session,
        market=market,
        benchmark_symbol=benchmark_symbol,
        selected_months=selected_months,
    )
    selected_months = set(cohort["selected_months"])
    selected_rows = [row for row in rows if row["month"] in selected_months and row["status"] == "validated"]
    sample_by_id = {sample.id: sample for sample in samples}

    exclusions: Counter[str] = Counter()
    excluded_sample_ids: dict[str, set[int]] = {}
    optimizer_scope_rows: list[dict[str, Any]] = []
    eligible_rows: list[dict[str, Any]] = []
    for row in selected_rows:
        sample_id = int(row["sample_id"])
        sample = sample_by_id.get(sample_id)
        if sample is None:
            excluded_sample_ids.setdefault("sample_missing", set()).add(sample_id)
            continue
        if sample.strategy_type not in OPTIMIZER_STRATEGY_TYPES:
            reason = f"strategy_type_excluded:{sample.strategy_type or 'missing'}"
            excluded_sample_ids.setdefault(reason, set()).add(sample_id)
            continue
        optimizer_row = row | {"sample": sample}
        optimizer_scope_rows.append(optimizer_row)
        if not _is_complete_replay_input(sample.replay_input):
            excluded_sample_ids.setdefault(
                "replay_input_incomplete",
                set(),
            ).add(sample_id)
            continue
        eligible_rows.append(optimizer_row)
    exclusions.update({
        reason: len(sample_ids)
        for reason, sample_ids in excluded_sample_ids.items()
    })

    replay_coverage = replay_coverage_summary(
        optimizer_scope_rows,
        eligible_rows,
        sample_key="sample_id",
        date_key="record_date",
        selected_months=[str(value) for value in cohort["selected_months"]],
        minimum_coverage=min_replay_coverage,
    )
    min_training_blocks, min_holdout_blocks = required_block_counts()

    baseline_config = ConfidenceScoringConfig()
    candidates = [
        _confidence_candidate_report(
            eligible_rows,
            baseline_config=baseline_config,
            parameter=parameter,
            direction=direction,
            cohort=cohort,
            watermarks=watermarks,
            min_sample_count=min_sample_count,
            min_validated_coverage=min_validated_coverage,
            replay_coverage_ok=bool(replay_coverage["meets_threshold"]),
        )
        for parameter in TUNABLE_CONFIDENCE_PARAMETERS
        for direction in (-1, 1)
    ]
    report = {
        "metadata": {
            "report_version": ANALYSIS_CALIBRATION_REPORT_VERSION,
            "track": "general_analysis",
            "market": market,
            "benchmark_symbol": benchmark_symbol,
            "through_month": f"{through_year:04d}-{through_month:02d}",
            "validation_version": ANALYSIS_FORWARD_VALIDATION_VERSION,
            "confidence_config_version": CONFIDENCE_CONFIG_VERSION,
            "strategy_version": STRATEGY_VERSION,
            "min_sample_count": min_sample_count,
            "min_validated_coverage": min_validated_coverage,
            "min_replay_coverage": min_replay_coverage,
            "min_training_block_count": min_training_blocks,
            "min_holdout_block_count": min_holdout_blocks,
            "positioning": "confidence_calibration_governance_not_trading_performance_marketing",
        },
        "cohort": cohort,
        "completeness_watermarks": watermarks,
        "baseline_config": baseline_config.to_dict(),
        "locked_parameters": [
            "negative_sentiment_points",
            "distribution_penalty",
            "retail_chasing_penalty",
            "bearish_technical_penalty",
            "bullish_distribution_penalty",
            "date_unknown_penalty",
            "strategy_generator_rules",
        ],
        "candidate_configs": candidates,
        "coverage": {
            **replay_coverage,
            "all_selected_validation_rows": len(selected_rows),
            "optimizer_scope_validation_rows": len(optimizer_scope_rows),
            "optimizer_eligible_rows": len(eligible_rows),
            "replay_coverage": replay_coverage["coverage"],
            "exclusion_reasons": dict(sorted(exclusions.items())),
        },
        "auto_change_eligible": any(candidate["auto_change_eligible"] for candidate in candidates),
        "human_approval_boundary": {
            "automated_report": True,
            "updates_live_scoring": False,
            "requires_human_approved_single_parameter_change": True,
        },
    }
    return report, render_general_analysis_monthly_markdown(report)


def render_general_analysis_monthly_markdown(report: Mapping[str, Any]) -> str:
    metadata = _mapping(report.get("metadata"))
    cohort = _mapping(report.get("cohort"))
    coverage = _mapping(report.get("coverage"))
    lines = [
        f"# General Analysis Confidence Review through {metadata.get('through_month')}",
        "",
        "This report calibrates finalized `/analyze` confidence scores. It does not modify production configuration.",
        "",
        "## Cohort",
        "",
        f"- Training months: {', '.join(str(value) for value in _as_list(cohort.get('training_months'))) or 'insufficient'}",
        f"- Holdout month: {cohort.get('holdout_month') or 'insufficient'}",
        f"- Replay coverage: {coverage.get('replay_coverage')}",
        f"- Replay coverage meets threshold: {coverage.get('meets_threshold')}",
        f"- Minimum training date blocks: {metadata.get('min_training_block_count')}",
        f"- Minimum holdout date blocks: {metadata.get('min_holdout_block_count')}",
        f"- Auto-change eligible: {report.get('auto_change_eligible')}",
        "",
        "## Candidate Actions",
        "",
        "| Parameter | Before | After | Training delta | Holdout delta | Eligible | Reason |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for candidate in _as_list(report.get("candidate_configs")):
        bootstrap = _mapping(candidate.get("training_block_bootstrap"))
        holdout = _mapping(candidate.get("holdout_primary_metric"))
        lines.append(
            "| {parameter} | {before} | {after} | {training} | {holdout} | {eligible} | {reason} |".format(
                parameter=candidate.get("parameter"),
                before=candidate.get("before"),
                after=candidate.get("after"),
                training=bootstrap.get("delta"),
                holdout=holdout.get("delta"),
                eligible=candidate.get("auto_change_eligible"),
                reason=candidate.get("eligibility_reason"),
            )
        )
    lines.extend([
        "",
        "Negative evidence, safety penalties, strategy rules, and position-analysis behavior are locked in this version.",
        "",
    ])
    return "\n".join(lines)


def _confidence_candidate_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_config: ConfidenceScoringConfig,
    parameter: str,
    direction: int,
    cohort: Mapping[str, Any],
    watermarks: Sequence[Mapping[str, Any]],
    min_sample_count: int,
    min_validated_coverage: float,
    replay_coverage_ok: bool,
) -> dict[str, Any]:
    before_value = int(getattr(baseline_config, parameter))
    after_value = max(0, before_value + direction)
    candidate_config = replace(baseline_config, **{parameter: after_value})
    baseline_rows = _replay_rows(rows, baseline_config)
    candidate_rows = _replay_rows(rows, candidate_config)
    training_months = set(str(value) for value in _as_list(cohort.get("training_months")))
    holdout_month = str(cohort.get("holdout_month") or "")
    baseline_training = [row for row in baseline_rows if row["month"] in training_months]
    candidate_training = [row for row in candidate_rows if row["month"] in training_months]
    baseline_holdout = [row for row in baseline_rows if row["month"] == holdout_month]
    candidate_holdout = [row for row in candidate_rows if row["month"] == holdout_month]
    before_training_metrics = metrics_by_window(
        baseline_training,
        score_key="replayed_score",
        selection=lambda row: int(row["replayed_score"]) >= 60,
    )
    after_training_metrics = metrics_by_window(
        candidate_training,
        score_key="replayed_score",
        selection=lambda row: int(row["replayed_score"]) >= 60,
    )
    before_holdout_metrics = metrics_by_window(
        baseline_holdout,
        score_key="replayed_score",
        selection=lambda row: int(row["replayed_score"]) >= 60,
    )
    after_holdout_metrics = metrics_by_window(
        candidate_holdout,
        score_key="replayed_score",
        selection=lambda row: int(row["replayed_score"]) >= 60,
    )
    bootstrap = block_bootstrap_delta(
        baseline_training,
        candidate_training,
        metric=confidence_excess_correlation,
        block_key="record_date",
    )
    holdout_before = confidence_excess_correlation(baseline_holdout)
    holdout_after = confidence_excess_correlation(candidate_holdout)
    holdout_delta = _delta(holdout_after, holdout_before)
    selected_watermarks = [
        row
        for row in watermarks
        if str(row.get("month")) in set(_as_list(cohort.get("selected_months")))
    ]
    coverage_ok = bool(selected_watermarks) and all(
        (row.get("validated_coverage") or 0) >= min_validated_coverage
        for row in selected_watermarks
    )
    lower_ci = _number(_as_list(bootstrap.get("ci_95"))[0]) if _as_list(bootstrap.get("ci_95")) else None
    training_delta = _number(bootstrap.get("delta"))
    training_sample_counts = independent_sample_counts_by_window(
        baseline_training,
        sample_key="sample_id",
    )
    holdout_sample_counts = independent_sample_counts_by_window(
        baseline_holdout,
        sample_key="sample_id",
    )
    enough_samples = (
        all(count >= min_sample_count for count in training_sample_counts.values())
        and all(count >= min_sample_count for count in holdout_sample_counts.values())
    )
    training_block_count = independent_block_count(
        baseline_training,
        block_key="record_date",
    )
    holdout_block_count = independent_block_count(
        baseline_holdout,
        block_key="record_date",
    )
    min_training_blocks, min_holdout_blocks = required_block_counts()
    enough_blocks = (
        training_block_count >= min_training_blocks
        and holdout_block_count >= min_holdout_blocks
    )
    eligible = (
        cohort.get("cohort_complete") is True
        and coverage_ok
        and replay_coverage_ok
        and enough_samples
        and enough_blocks
        and lower_ci is not None
        and lower_ci >= 0
        and training_delta is not None
        and training_delta > 0
        and holdout_delta is not None
        and holdout_delta >= -0.01
        and before_value != after_value
    )
    if cohort.get("cohort_complete") is not True:
        reason = "insufficient_mature_months"
    elif not coverage_ok:
        reason = "validated_coverage_below_threshold"
    elif not replay_coverage_ok:
        reason = "replay_coverage_below_threshold"
    elif not enough_samples:
        reason = "insufficient_independent_samples"
    elif not enough_blocks:
        reason = "insufficient_date_blocks"
    elif lower_ci is None or lower_ci < 0 or training_delta is None or training_delta <= 0:
        reason = "training_bootstrap_not_positive"
    elif holdout_delta is None or holdout_delta < -0.01:
        reason = "holdout_not_preserved"
    else:
        reason = "eligible"
    return {
        "parameter": parameter,
        "step": direction,
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
            "metric": "confidence_outcome_correlation",
            "before": holdout_before,
            "after": holdout_after,
            "delta": holdout_delta,
        },
        "coverage": {
            "training_rows": len(baseline_training),
            "holdout_rows": len(baseline_holdout),
            "training_independent_samples_by_window": training_sample_counts,
            "holdout_independent_samples_by_window": holdout_sample_counts,
            "training_block_count": training_block_count,
            "holdout_block_count": holdout_block_count,
            "min_training_block_count": min_training_blocks,
            "min_holdout_block_count": min_holdout_blocks,
            "selected_months_meet_validated_coverage": coverage_ok,
            "selected_months_meet_replay_coverage": replay_coverage_ok,
        },
        "auto_change_eligible": eligible,
        "eligibility_reason": reason,
    }


def _replay_rows(
    rows: Sequence[Mapping[str, Any]],
    config: ConfidenceScoringConfig,
) -> list[dict[str, Any]]:
    replayed: list[dict[str, Any]] = []
    for row in rows:
        sample = row.get("sample")
        if not isinstance(sample, AnalysisCalibrationSample):
            continue
        replay = _mapping(sample.replay_input)
        result = compute_confidence(
            int(replay.get("base_score") or BASE_CONFIDENCE),
            news_sentiment=str(replay.get("news_sentiment") or "neutral"),
            inst_flow=str(replay.get("institutional_flow") or "unknown"),
            technical_signal=str(replay.get("technical_signal") or "sideways"),
            date_unknown=bool(replay.get("date_unknown")),
            sentiment_strength=_number(replay.get("sentiment_strength")) or 1.0,
            config=config,
        )
        replayed.append(
            {key: value for key, value in row.items() if key != "sample"}
            | {"replayed_score": int(result["signal_confidence"])}
        )
    return replayed


def _validation_rows(
    session: Session,
    *,
    market: str,
    benchmark_symbol: str,
    selected_months: Sequence[str],
) -> list[dict[str, Any]]:
    query = (
        select(AnalysisForwardValidationResult, AnalysisCalibrationSample)
        .join(
            AnalysisCalibrationSample,
            AnalysisForwardValidationResult.sample_id == AnalysisCalibrationSample.id,
        )
        .where(
            AnalysisCalibrationSample.analysis_type == "general",
            AnalysisCalibrationSample.market == market,
            AnalysisCalibrationSample.benchmark_symbol == benchmark_symbol,
            _month_filter(
                AnalysisCalibrationSample.record_date,
                selected_months,
            ),
            AnalysisForwardValidationResult.validation_version == ANALYSIS_FORWARD_VALIDATION_VERSION,
        )
        .order_by(
            AnalysisCalibrationSample.record_date.asc(),
            AnalysisForwardValidationResult.window_days.asc(),
            AnalysisCalibrationSample.id.asc(),
        )
    )
    return [
        {
            "sample_id": sample.id,
            "symbol": sample.symbol,
            "record_date": sample.record_date.isoformat(),
            "month": month_key(sample.record_date),
            "window_days": result.window_days,
            "status": result.status,
            "skip_reason": result.skip_reason,
            "outcome": dict(result.outcome or {}),
        }
        for result, sample in session.execute(query).all()
    ]


def _general_completeness_watermarks(
    session: Session,
    *,
    through_date: date,
    market: str,
    benchmark_symbol: str,
) -> list[dict[str, Any]]:
    year_value = cast(extract("year", AnalysisCalibrationSample.record_date), Integer)
    month_value = cast(extract("month", AnalysisCalibrationSample.record_date), Integer)
    expected_count = func.count(func.distinct(AnalysisCalibrationSample.id))
    evaluated_count = func.count(
        func.distinct(
            case(
                (
                    AnalysisForwardValidationResult.id.is_not(None),
                    AnalysisCalibrationSample.id,
                )
            )
        )
    )
    validated_count = func.count(
        func.distinct(
            case(
                (
                    AnalysisForwardValidationResult.status == "validated",
                    AnalysisCalibrationSample.id,
                )
            )
        )
    )
    query = (
        select(
            year_value,
            month_value,
            expected_count,
            evaluated_count,
            validated_count,
        )
        .select_from(AnalysisCalibrationSample)
        .outerjoin(
            AnalysisForwardValidationResult,
            and_(
                AnalysisForwardValidationResult.sample_id
                == AnalysisCalibrationSample.id,
                AnalysisForwardValidationResult.window_days == 20,
                AnalysisForwardValidationResult.validation_version
                == ANALYSIS_FORWARD_VALIDATION_VERSION,
            ),
        )
        .where(
            AnalysisCalibrationSample.analysis_type == "general",
            AnalysisCalibrationSample.analysis_is_final.is_(True),
            AnalysisCalibrationSample.market == market,
            AnalysisCalibrationSample.benchmark_symbol == benchmark_symbol,
            AnalysisCalibrationSample.record_date <= through_date,
        )
        .group_by(year_value, month_value)
        .order_by(year_value.asc(), month_value.asc())
    )
    watermarks: list[dict[str, Any]] = []
    for year, month, expected, evaluated, validated in session.execute(query):
        expected_value = int(expected or 0)
        evaluated_value = int(evaluated or 0)
        validated_value = int(validated or 0)
        watermarks.append({
            "month": f"{int(year):04d}-{int(month):02d}",
            "expected_20d_samples": expected_value,
            "evaluated_20d_samples": evaluated_value,
            "validated_20d_samples": validated_value,
            "maturity_complete": (
                expected_value > 0
                and evaluated_value >= expected_value
            ),
            "validated_coverage": validated_coverage(
                validated_value,
                expected_value,
            ),
        })
    return watermarks


def _month_filter(column: Any, months: Sequence[str]) -> Any:
    predicates = []
    for value in months:
        year_text, month_text = value.split("-", 1)
        start, end = month_bounds(int(year_text), int(month_text))
        predicates.append(and_(column >= start, column <= end))
    return or_(*predicates) if predicates else column.in_([])


def _sample_snapshot(sample: AnalysisCalibrationSample) -> dict[str, Any]:
    replay = _mapping(sample.replay_input)
    entry_price = _number(replay.get("entry_price"))
    return {
        "candidate_id": sample.id,
        "sample_id": sample.id,
        "symbol": sample.symbol,
        "market": sample.market,
        "benchmark_symbol": sample.benchmark_symbol,
        "record_date": sample.record_date.isoformat(),
        "data_dates": {"ohlcv": sample.record_date.isoformat()},
        "input_snapshot": {"ohlcv": {"close": entry_price}},
        "strategy_type": sample.strategy_type,
        "signal_confidence": _number(sample.signal_confidence),
    }


def _sample_id_from_candidate(outcome: Mapping[str, Any]) -> int | None:
    candidate_id = outcome.get("candidate_id")
    return int(candidate_id) if candidate_id is not None else None


def _is_complete_replay_input(value: Any) -> bool:
    replay = _mapping(value)
    return (
        replay.get("schema_version") == GENERAL_REPLAY_INPUT_VERSION
        and all(
            key in replay
            for key in (
                "base_score",
                "news_sentiment",
                "sentiment_strength",
                "institutional_flow",
                "technical_signal",
                "date_unknown",
                "baseline_config",
            )
        )
    )


def _general_analysis_calibration_partition(
    symbol: str,
) -> tuple[str, str] | None:
    normalized = symbol.strip().upper()
    if normalized.endswith((".TW", ".TWO")):
        return "TW", DEFAULT_BENCHMARK_SYMBOL
    return None


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, 1) - timedelta(days=1)
    return date(year, month + 1, 1) - timedelta(days=1)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _last_number(value: Any) -> float | None:
    if not isinstance(value, list):
        return None
    for item in reversed(value):
        number = _number(item)
        if number is not None:
            return number
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    number = _number(value)
    return Decimal(str(number)) if number is not None else None


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _general_forward_snapshot(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "primary_bucket": "unknown",
        "secondary_buckets": ["none"],
        "matched_rule_codes": ["none"],
        "risk_labels": ["none"],
        "market_regime": "unknown",
        "relative_strength_bucket": "missing",
        "repeat_status": "unknown",
        "score_decile": "00-09",
        "data_freshness_status": _general_forward_freshness(candidate),
    }


def _general_forward_entry_price(
    candidate: Mapping[str, Any],
    prices: Mapping[date, Mapping[str, float]],
    signal_date: date,
) -> float | None:
    snapshot_close = forward_number(
        _mapping(_mapping(candidate.get("input_snapshot")).get("ohlcv")).get("close")
    )
    if snapshot_close is not None and snapshot_close > 0:
        return snapshot_close
    return close_on(prices, signal_date)


def _general_forward_defense_reference(
    _candidate: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {"source": None, "value": None}


def _general_forward_freshness(candidate: Mapping[str, Any]) -> str:
    signal_date = _parse_date(candidate.get("record_date"))
    ohlcv_date = _parse_date(_mapping(candidate.get("data_dates")).get("ohlcv"))
    if signal_date is not None and ohlcv_date is not None and ohlcv_date < signal_date:
        return "stale"
    if ohlcv_date is None:
        return "unknown"
    return "fresh"


def _forward_sample_summary(
    *,
    candidate_count: int,
    outcomes: Sequence[Mapping[str, Any]],
    windows: Sequence[int],
) -> dict[str, Any]:
    validated_by_window = Counter(
        int(outcome["window_days"])
        for outcome in outcomes
        if outcome["status"] == "validated"
    )
    skipped_by_window = Counter(
        int(outcome["window_days"])
        for outcome in outcomes
        if outcome["status"] == "skipped"
    )
    return {
        "candidate_count": candidate_count,
        "window_count": len(windows),
        "evaluated_sample_count": len(outcomes),
        "validated_sample_count": sum(validated_by_window.values()),
        "skipped_sample_count": sum(skipped_by_window.values()),
        "validated_by_window": {
            str(window): validated_by_window[window]
            for window in windows
        },
        "skipped_by_window": {
            str(window): skipped_by_window[window]
            for window in windows
        },
    }


GENERAL_ANALYSIS_FORWARD_ADAPTER = ForwardValidationAdapter(
    candidate_snapshot=_general_forward_snapshot,
    entry_price=_general_forward_entry_price,
    defense_reference=_general_forward_defense_reference,
    freshness_status=_general_forward_freshness,
)


__all__ = [
    "ANALYSIS_CALIBRATION_REPORT_VERSION",
    "ANALYSIS_FORWARD_VALIDATION_VERSION",
    "GENERAL_REPLAY_INPUT_VERSION",
    "GENERAL_REPLAY_CACHE_KEY",
    "GENERAL_ANALYSIS_FORWARD_ADAPTER",
    "TUNABLE_CONFIDENCE_PARAMETERS",
    "build_general_analysis_monthly_report",
    "build_general_analysis_replay_input",
    "capture_general_analysis_calibration_sample",
    "evaluate_general_analysis_forward_validation",
    "general_validation_samples",
    "render_general_analysis_monthly_markdown",
    "upsert_general_analysis_validation_results",
]
