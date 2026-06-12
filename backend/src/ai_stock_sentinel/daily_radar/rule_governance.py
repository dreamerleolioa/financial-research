from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_FORWARD_WINDOWS,
    FORWARD_VALIDATION_VERSION,
)
from ai_stock_sentinel.daily_radar.rule_registry import (
    RuleRegistryEntry,
    get_rule_registry,
    registry_payload,
)
from ai_stock_sentinel.daily_radar.scoring import RULE_VERSION, SCORING_VERSION
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarRun,
)


RULE_REVIEW_REPORT_VERSION = "daily-radar-rule-review-v1"
DEFAULT_MIN_SAMPLE_COUNT = 20
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
) -> MonthlyRuleReviewReport:
    month_start, month_end = _month_bounds(year, month)
    rows = validation_rows_from_results(
        session,
        market=market,
        start_date=month_start,
        end_date=month_end,
        validation_version=validation_version,
    )
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
    report = {
        "metadata": {
            "report_version": RULE_REVIEW_REPORT_VERSION,
            "market": market,
            "month": f"{year:04d}-{month:02d}",
            "sample_source": "production_db",
            "validation_version": version,
            "min_sample_count": min_sample_count,
            "positioning": "automated_rule_quality_recommendations_not_human_approved_strategy_update",
        },
        "sample_summary": ablation["sample_summary"],
        "registry_summary": _registry_summary(),
        "ablation_summary": ablation["ablation_groups"],
        "rule_recommendations": rule_recommendations,
        "skip_reasons": _skip_reasons(rows),
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
    query = (
        select(DailyRadarForwardValidationResult, DailyRadarCandidate, DailyRadarRun)
        .join(DailyRadarCandidate, DailyRadarForwardValidationResult.candidate_id == DailyRadarCandidate.id)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.run_date >= start_date,
            DailyRadarRun.run_date <= end_date,
        )
        .order_by(
            DailyRadarRun.run_date.asc(),
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
        "primary_bucket": candidate.primary_bucket,
        "secondary_buckets": list(candidate.secondary_buckets or []),
        "matched_rule_codes": _matched_rule_codes(candidate.matched_rules),
        "risk_labels": list(candidate.risk_labels or []),
        "market_regime": _market_regime(candidate),
        "relative_strength_bucket": _relative_strength_bucket(score_breakdown),
        "repeat_status": candidate.repeat_status,
        "score_decile": _score_decile(candidate.observation_score),
        "data_freshness_status": "data_gap" if "data_gap" in set(candidate.risk_labels or []) else "fresh",
    }


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
        "rule_review_report_version": RULE_REVIEW_REPORT_VERSION,
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
