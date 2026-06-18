from __future__ import annotations

from datetime import date
from typing import Any

from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarCandidateResponse,
    DailyRadarRunResponse,
    DailyRadarRunTriggerResponse,
)
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun


def run_trigger_response(run: DailyRadarRun) -> DailyRadarRunTriggerResponse:
    return DailyRadarRunTriggerResponse(
        run_id=run.id,
        run_date=run.run_date,
        market=run.market,
        status=run.status,
        universe_count=run.universe_count,
        prefilter_count=run.prefilter_count,
        candidate_count=run.candidate_count,
        errors=list(run.errors or []),
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def public_run_response(
    run: DailyRadarRun,
    *,
    bucket: str | None,
    limit: int,
) -> DailyRadarRunResponse:
    candidates = [candidate_response(candidate) for candidate in _ordered_candidates(run.candidates)]
    if bucket is not None:
        candidates = [candidate for candidate in candidates if matches_bucket(candidate.model_dump(), bucket)]
    candidates = candidates[:limit]
    return DailyRadarRunResponse(
        run_date=run.run_date,
        status=run.status,
        data_dates=_run_data_dates(candidates),
        market_context=_run_market_context(candidates),
        candidates=candidates,
    )


def history_response(item: dict[str, Any]) -> dict[str, Any]:
    response = {
        "symbol": item["symbol"],
        "name": _stored_display_name(item["symbol"], item["name"]),
        "record_date": item["record_date"],
        "primary_bucket": item["primary_bucket"],
        "secondary_buckets": list(item.get("secondary_buckets") or []),
        "observation_score": item["observation_score"],
        "risk_labels": list(item.get("risk_labels") or []),
        "repeat_status": item["repeat_status"],
        "bucket_scores": dict(item.get("bucket_scores") or {}),
        "matched_rules": _matched_rules(item.get("matched_rules") or []),
        "score_breakdown": dict(item.get("score_breakdown") or {}),
        "input_snapshot": dict(item.get("input_snapshot") or {}),
        "data_dates": {key: value.isoformat() for key, value in _date_mapping(item.get("data_dates") or {}).items()},
        "background_context_labels": _background_context_labels(item.get("input_snapshot")),
    }
    scoring_version = item.get("scoring_version") or _trace_version(item.get("score_breakdown"), "scoring_version")
    rule_version = item.get("rule_version") or _trace_version(item.get("score_breakdown"), "rule_version")
    if scoring_version is not None:
        response["scoring_version"] = scoring_version
    if rule_version is not None:
        response["rule_version"] = rule_version
    return response


def candidate_response(candidate: DailyRadarCandidate) -> DailyRadarCandidateResponse:
    return DailyRadarCandidateResponse(
        symbol=candidate.symbol,
        name=_stored_display_name(candidate.symbol, candidate.name),
        primary_bucket=candidate.primary_bucket,
        secondary_buckets=list(candidate.secondary_buckets or []),
        observation_score=candidate.observation_score,
        risk_labels=list(candidate.risk_labels or []),
        repeat_status=candidate.repeat_status,
        explanation=candidate.explanation,
        scoring_version=_trace_version(candidate.score_breakdown, "scoring_version"),
        rule_version=_trace_version(candidate.score_breakdown, "rule_version"),
        bucket_scores=dict(candidate.bucket_scores or {}),
        score_breakdown=dict(candidate.score_breakdown or {}),
        input_snapshot=dict(candidate.input_snapshot or {}),
        data_dates=_date_mapping(candidate.data_dates or {}),
        matched_rules=_matched_rules(candidate.matched_rules or []),
        background_context_labels=_background_context_labels(candidate.input_snapshot),
    )


def matches_bucket(item: dict[str, Any], bucket: str | None) -> bool:
    if bucket is None:
        return True
    return item.get("primary_bucket") == bucket or bucket in set(item.get("secondary_buckets") or [])


def _ordered_candidates(candidates: list[DailyRadarCandidate]) -> list[DailyRadarCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (-candidate.observation_score, candidate.symbol),
    )


def _stored_display_name(symbol: str, name: str | None) -> str:
    normalized_name = str(name or "").strip()
    return normalized_name or symbol


def _matched_rules(raw_rules: list[Any]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for rule in raw_rules:
        if isinstance(rule, dict):
            rules.append(
                {
                    "rule_id": str(rule.get("rule_id", "unknown_rule")),
                    "label": str(rule.get("label", rule.get("rule_id", "unknown_rule"))),
                    "details": dict(rule.get("details") or {}),
                }
            )
        else:
            rules.append({"rule_id": str(rule), "label": str(rule), "details": {}})
    return rules


def _background_context_labels(input_snapshot: Any) -> list[dict[str, Any]]:
    if not isinstance(input_snapshot, dict):
        return []
    labels = input_snapshot.get("background_context_labels")
    if not isinstance(labels, list):
        return []
    return [dict(label) for label in labels if isinstance(label, dict)]


def _trace_version(payload: Any, key: str) -> str | None:
    if isinstance(payload, dict) and payload.get(key) is not None:
        return str(payload[key])
    return None


def _run_data_dates(candidates: list[DailyRadarCandidateResponse]) -> dict[str, date]:
    data_dates: dict[str, date] = {}
    for candidate in candidates:
        for key, value in candidate.data_dates.items():
            if key not in data_dates or value > data_dates[key]:
                data_dates[key] = value
    return data_dates


def _run_market_context(candidates: list[DailyRadarCandidateResponse]) -> dict[str, Any]:
    for candidate in candidates:
        market_context = candidate.input_snapshot.get("market_context")
        if isinstance(market_context, dict) and market_context:
            return dict(market_context)
    return {}


def _date_mapping(raw_dates: dict[str, Any]) -> dict[str, date]:
    data_dates: dict[str, date] = {}
    for key, value in raw_dates.items():
        parsed = parse_date(value)
        if parsed is not None:
            data_dates[str(key)] = parsed
    return data_dates


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = [
    "candidate_response",
    "history_response",
    "matches_bucket",
    "parse_date",
    "public_run_response",
    "run_trigger_response",
]
