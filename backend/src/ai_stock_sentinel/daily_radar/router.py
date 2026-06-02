from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.repository import (
    get_daily_radar_run_by_date,
    get_final_raw_data_rows_for_date,
    get_latest_daily_radar_run,
    get_symbol_candidate_history,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarCandidateResponse,
    DailyRadarRunResponse,
)
from ai_stock_sentinel.daily_radar.service import run_daily_radar
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun
from ai_stock_sentinel.db.session import get_db


router = APIRouter(tags=["daily-radar"])


class DailyRadarRunRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)


class DailyRadarRunTriggerResponse(BaseModel):
    run_id: int
    run_date: date
    market: str
    status: Literal["completed", "running", "failed", "stale_data"]
    universe_count: int
    prefilter_count: int
    candidate_count: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None


@router.post(
    "/internal/daily-radar/run",
    response_model=DailyRadarRunTriggerResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_endpoint(
    payload: DailyRadarRunRequest | None = None,
    db: Session = Depends(get_db),
) -> DailyRadarRunTriggerResponse:
    request = payload or DailyRadarRunRequest()
    run_date = request.run_date or _backend_today()
    cache_rows = get_final_raw_data_rows_for_date(db, run_date=run_date)
    if not cache_rows:
        raise HTTPException(
            status_code=409,
            detail=f"No final StockRawData rows are available for {run_date.isoformat()}.",
        )
    run = run_daily_radar(
        run_date,
        request.market,
        session=db,
        cache_rows=cache_rows,
        market_context={},
        allow_fixture_fallback=False,
    )
    db.commit()
    return _run_response(run)


@router.get("/daily-radar/latest", response_model=DailyRadarRunResponse)
def get_latest_daily_radar_endpoint(
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyRadarRunResponse:
    run = get_latest_daily_radar_run(db, market=market)
    if run is None:
        raise HTTPException(status_code=404, detail="No public Daily Radar run is available.")
    return _public_run_response(run, bucket=bucket, limit=limit)


@router.get("/daily-radar/symbol/{symbol}", response_model=list[dict[str, Any]])
def get_daily_radar_symbol_history_endpoint(
    symbol: str,
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    lookback_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    history = get_symbol_candidate_history(
        db,
        symbols=[symbol],
        before_date=_backend_today() + timedelta(days=1),
        lookback_days=lookback_days,
        market=market,
    )
    filtered = [_history_response(item) for item in history if _matches_bucket(item, bucket)]
    return filtered[:limit]


@router.get("/daily-radar/{run_date}", response_model=DailyRadarRunResponse)
def get_daily_radar_by_date_endpoint(
    run_date: date,
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyRadarRunResponse:
    run = get_daily_radar_run_by_date(db, run_date=run_date, market=market)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"No public Daily Radar run is available for {run_date.isoformat()}.",
        )
    return _public_run_response(run, bucket=bucket, limit=limit)


def _backend_today() -> date:
    return date.today()


def _run_response(run: DailyRadarRun) -> DailyRadarRunTriggerResponse:
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


def _public_run_response(
    run: DailyRadarRun,
    *,
    bucket: str | None,
    limit: int,
) -> DailyRadarRunResponse:
    candidates = [_candidate_response(candidate) for candidate in _ordered_candidates(run.candidates)]
    if bucket is not None:
        candidates = [candidate for candidate in candidates if _matches_bucket(candidate.model_dump(), bucket)]
    candidates = candidates[:limit]
    return DailyRadarRunResponse(
        run_date=run.run_date,
        status=run.status,
        data_dates=_run_data_dates(candidates),
        market_context=_run_market_context(candidates),
        candidates=candidates,
    )


def _ordered_candidates(candidates: list[DailyRadarCandidate]) -> list[DailyRadarCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (-candidate.observation_score, candidate.symbol),
    )


def _candidate_response(candidate: DailyRadarCandidate) -> DailyRadarCandidateResponse:
    return DailyRadarCandidateResponse(
        symbol=candidate.symbol,
        name=candidate.name,
        primary_bucket=candidate.primary_bucket,
        secondary_buckets=list(candidate.secondary_buckets or []),
        observation_score=candidate.observation_score,
        risk_labels=list(candidate.risk_labels or []),
        repeat_status=candidate.repeat_status,
        explanation=candidate.explanation,
        bucket_scores=dict(candidate.bucket_scores or {}),
        score_breakdown=dict(candidate.score_breakdown or {}),
        input_snapshot=dict(candidate.input_snapshot or {}),
        data_dates=_date_mapping(candidate.data_dates or {}),
        matched_rules=_matched_rules(candidate.matched_rules or []),
    )


def _history_response(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": item["symbol"],
        "name": item["name"],
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
    }


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


def _matches_bucket(item: dict[str, Any], bucket: str | None) -> bool:
    if bucket is None:
        return True
    return item.get("primary_bucket") == bucket or bucket in set(item.get("secondary_buckets") or [])


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
        parsed = _parse_date(value)
        if parsed is not None:
            data_dates[str(key)] = parsed
    return data_dates


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = ["router"]
