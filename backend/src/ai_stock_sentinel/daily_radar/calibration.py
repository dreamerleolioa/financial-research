from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_BUCKETS
from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_fixture_records
from ai_stock_sentinel.daily_radar.prefilter import run_stage1_prefilter
from ai_stock_sentinel.daily_radar.repository import PUBLIC_RUN_STATUSES
from ai_stock_sentinel.daily_radar.scoring import RULE_VERSION, SCORING_VERSION, ScoringConfig, score_daily_radar_record
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun


CALIBRATION_REPORT_VERSION = "daily-radar-calibration-v1"
DEFAULT_RANK_CUTOFFS = (10, 20, 50, 100)
DEFAULT_BUCKET_THRESHOLDS = (45, 55, 65)


def build_calibration_report(
    candidates: Iterable[Mapping[str, Any]],
    *,
    market: str,
    sample_source: str,
    rank_cutoffs: Sequence[int] = DEFAULT_RANK_CUTOFFS,
    bucket_thresholds: Sequence[int] = DEFAULT_BUCKET_THRESHOLDS,
) -> dict[str, Any]:
    normalized, invalid_skip_reasons = _valid_candidates(candidates)
    ordered = sorted(normalized, key=lambda candidate: (-_int(candidate.get("observation_score")), str(candidate.get("symbol"))))
    skip_reasons = Counter(invalid_skip_reasons)
    skip_reasons.update(_component_skip_reasons(ordered))

    return {
        "report_version": CALIBRATION_REPORT_VERSION,
        "sample_source": sample_source,
        "market": market,
        "sample_count": len(ordered),
        "excluded_sample_count": sum(invalid_skip_reasons.values()),
        "version_manifest": _version_manifest(),
        "bucket_distribution": _bucket_distribution(ordered),
        "rank_cutoff_impact": [
            _rank_cutoff_row(ordered, cutoff)
            for cutoff in _ordered_positive_values(rank_cutoffs)
        ],
        "bucket_threshold_impact": {
            "current_secondary_bucket_threshold": ScoringConfig().secondary_bucket_threshold,
            "thresholds": [
                _bucket_threshold_row(ordered, threshold)
                for threshold in _ordered_positive_values(bucket_thresholds)
            ],
        },
        "risk_penalty_impact": _risk_penalty_impact(ordered),
        "overheat_impact": _overheat_impact(ordered),
        "relative_strength_impact": _relative_strength_impact(ordered),
        "skip_reasons": dict(sorted(skip_reasons.items())),
    }


def calibration_candidates_from_fixture(
    *,
    fixture_dir: str | Path,
    run_date: date,
    market: str,
    candidate_limit: int = 100,
) -> list[dict[str, Any]]:
    fixture_path = Path(fixture_dir)
    records = load_daily_radar_fixture_records(fixture_path)
    market_context = _load_optional_json(fixture_path / "market_context.json")
    prefilter_results = run_stage1_prefilter(records, limit=candidate_limit, include_rejected=True)
    accepted_prefilters = {
        str(result["symbol"]): result
        for result in prefilter_results
        if result.get("prefilter_status") == "accepted"
    }
    records_by_symbol = {str(record["symbol"]): record for record in records}

    scored: list[dict[str, Any]] = []
    for symbol in sorted(accepted_prefilters):
        scored.append(
            score_daily_radar_record(
                records_by_symbol[symbol],
                market_context=market_context,
                prefilter_result=accepted_prefilters[symbol],
            )
        )
    return [
        dict(candidate) | {"record_date": str(candidate.get("record_date") or run_date.isoformat())}
        for candidate in sorted(scored, key=lambda candidate: (-int(candidate["observation_score"]), str(candidate["symbol"])))
    ]


def calibration_candidates_from_runs(
    session: Session,
    *,
    market: str,
    start_date: date | None = None,
    end_date: date | None = None,
    statuses: tuple[str, ...] = PUBLIC_RUN_STATUSES,
) -> list[dict[str, Any]]:
    query = (
        select(DailyRadarCandidate, DailyRadarRun)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .where(DailyRadarRun.market == market, DailyRadarRun.status.in_(statuses))
    )
    if start_date is not None:
        query = query.where(DailyRadarRun.run_date >= start_date)
    if end_date is not None:
        query = query.where(DailyRadarRun.run_date <= end_date)
    rows = session.execute(
        query.order_by(DailyRadarRun.run_date.asc(), DailyRadarCandidate.observation_score.desc(), DailyRadarCandidate.symbol.asc())
    ).all()
    return [_candidate_snapshot(candidate, run) for candidate, run in rows]


def write_report(report: Mapping[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _valid_candidates(candidates: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    valid: list[dict[str, Any]] = []
    skip_reasons: Counter[str] = Counter()
    for candidate in candidates:
        if candidate.get("observation_score") is None:
            skip_reasons["invalid_sample:missing_observation_score"] += 1
            continue
        if not candidate.get("primary_bucket"):
            skip_reasons["invalid_sample:missing_primary_bucket"] += 1
            continue
        valid.append(dict(candidate))
    return valid, skip_reasons


def _component_skip_reasons(candidates: Sequence[Mapping[str, Any]]) -> Counter[str]:
    skip_reasons: Counter[str] = Counter()
    for candidate in candidates:
        relative_strength = _relative_strength(candidate)
        freshness = str(relative_strength.get("freshness") or "")
        missing_reason = relative_strength.get("missing_reason")
        if not relative_strength:
            skip_reasons["relative_strength:missing_component"] += 1
        elif freshness != "fresh":
            skip_reasons[f"relative_strength:{missing_reason or freshness or 'not_fresh'}"] += 1
    return skip_reasons


def _version_manifest() -> dict[str, Any]:
    return {
        "scoring_version": SCORING_VERSION,
        "rule_version": RULE_VERSION,
        "calibration_report_version": CALIBRATION_REPORT_VERSION,
        "live_scoring_changed": False,
        "change_reason": "Phase 1D adds a deterministic calibration report workflow only; live scoring weights remain unchanged.",
        "tunables": {
            "secondary_bucket_threshold": ScoringConfig().secondary_bucket_threshold,
            "relative_strength_lookback_days": ScoringConfig().relative_strength_lookback_days,
            "rank_cutoffs": list(DEFAULT_RANK_CUTOFFS),
            "bucket_thresholds": list(DEFAULT_BUCKET_THRESHOLDS),
        },
    }


def _bucket_distribution(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(candidate.get("primary_bucket")) for candidate in candidates)
    return {bucket: counts[bucket] for bucket in sorted(counts)}


def _rank_cutoff_row(candidates: Sequence[Mapping[str, Any]], cutoff: int) -> dict[str, Any]:
    included = list(candidates[:cutoff])
    return {
        "cutoff": cutoff,
        "included": len(included),
        "average_observation_score": _average([_int(candidate.get("observation_score")) for candidate in included]),
        "bucket_distribution": _bucket_distribution(included),
        "risk_label_counts": _risk_label_counts(included),
        "relative_strength": _relative_strength_summary(included),
    }


def _bucket_threshold_row(candidates: Sequence[Mapping[str, Any]], threshold: int) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    matched_counts: list[int] = []
    for candidate in candidates:
        matched = [
            bucket
            for bucket, score in _mapping(candidate.get("bucket_scores")).items()
            if _float(score) >= threshold
        ]
        matched_counts.append(len(matched))
        counts.update(str(bucket) for bucket in matched)
    return {
        "threshold": threshold,
        "matched_bucket_counts": {bucket: counts[bucket] for bucket in sorted(counts)},
        "average_matched_buckets_per_sample": _average(matched_counts),
    }


def _risk_penalty_impact(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    penalties = [
        penalty
        for candidate in candidates
        for penalty in _risk_penalties(candidate)
    ]
    adjustments = [_int(penalty.get("score_adjustment")) for penalty in penalties]
    labels = Counter(str(penalty.get("label")) for penalty in penalties if penalty.get("label"))
    return {
        "samples_with_penalty": sum(1 for candidate in candidates if _risk_penalties(candidate)),
        "penalty_count": len(penalties),
        "label_counts": {label: labels[label] for label in sorted(labels)},
        "total_adjustment": sum(adjustments),
        "average_adjustment": _average(adjustments, denominator=len(candidates)),
    }


def _overheat_impact(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    overheat_adjustments = [
        _int(penalty.get("score_adjustment"))
        for candidate in candidates
        for penalty in _risk_penalties(candidate)
        if str(penalty.get("label")) == "overextended"
    ]
    return {
        "label": "overextended",
        "samples": len(overheat_adjustments),
        "average_adjustment": _average(overheat_adjustments),
    }


def _relative_strength_impact(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    traces = [_relative_strength(candidate) for candidate in candidates]
    fresh = [trace for trace in traces if str(trace.get("freshness")) == "fresh"]
    score_adjustments = [_int(trace.get("score")) for trace in traces]
    relative_values = [_float(trace.get("relative_value")) for trace in fresh if trace.get("relative_value") is not None]
    return {
        "fresh_samples": len(fresh),
        "missing_or_stale_samples": len(traces) - len(fresh),
        "positive_samples": sum(1 for trace in fresh if _float(trace.get("relative_value")) > 0),
        "negative_samples": sum(1 for trace in fresh if _float(trace.get("relative_value")) < 0),
        "neutral_samples": sum(1 for trace in fresh if _float(trace.get("relative_value")) == 0),
        "average_relative_value": _average(relative_values),
        "average_score_adjustment": _average(score_adjustments, denominator=len(candidates)),
        "score_adjustment_counts": _score_adjustment_counts(score_adjustments),
    }


def _relative_strength_summary(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    traces = [_relative_strength(candidate) for candidate in candidates]
    fresh = [trace for trace in traces if str(trace.get("freshness")) == "fresh"]
    return {
        "fresh": len(fresh),
        "missing_or_stale": len(traces) - len(fresh),
        "positive": sum(1 for trace in fresh if _float(trace.get("relative_value")) > 0),
        "negative": sum(1 for trace in fresh if _float(trace.get("relative_value")) < 0),
        "neutral": sum(1 for trace in fresh if _float(trace.get("relative_value")) == 0),
    }


def _risk_label_counts(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(label)
        for candidate in candidates
        for label in _as_list(candidate.get("risk_labels"))
    )
    return {label: counts[label] for label in sorted(counts)}


def _risk_penalties(candidate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        penalty
        for penalty in _as_list(_mapping(candidate.get("score_breakdown")).get("risk_penalties"))
        if isinstance(penalty, Mapping)
    ]


def _relative_strength(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(candidate.get("score_breakdown")).get("relative_strength"))


def _score_adjustment_counts(values: Sequence[int]) -> dict[str, int]:
    counts = Counter(str(value) for value in values)
    return {value: counts[value] for value in sorted(counts, key=lambda item: int(item))}


def _candidate_snapshot(candidate: DailyRadarCandidate, run: DailyRadarRun) -> dict[str, Any]:
    return {
        "symbol": candidate.symbol,
        "name": candidate.name,
        "record_date": run.run_date.isoformat(),
        "primary_bucket": candidate.primary_bucket,
        "secondary_buckets": list(candidate.secondary_buckets or []),
        "observation_score": candidate.observation_score,
        "bucket_scores": dict(candidate.bucket_scores or {}),
        "risk_labels": list(candidate.risk_labels or []),
        "matched_rules": list(candidate.matched_rules or []),
        "score_breakdown": dict(candidate.score_breakdown or {}),
        "input_snapshot": dict(candidate.input_snapshot or {}),
        "data_dates": dict(candidate.data_dates or {}),
    }


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _ordered_positive_values(values: Sequence[int]) -> list[int]:
    return sorted({int(value) for value in values if int(value) > 0})


def _average(values: Sequence[float | int], *, denominator: int | None = None) -> float | None:
    active_denominator = denominator if denominator is not None else len(values)
    if active_denominator == 0:
        return None
    return round(float(sum(values)) / active_denominator, 2)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    return float(value)


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


__all__ = [
    "CALIBRATION_REPORT_VERSION",
    "DEFAULT_BUCKET_THRESHOLDS",
    "DEFAULT_RANK_CUTOFFS",
    "build_calibration_report",
    "calibration_candidates_from_fixture",
    "calibration_candidates_from_runs",
    "write_report",
]
