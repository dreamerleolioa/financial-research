"""Read-only public Radar endpoints backed by persisted snapshots."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import dependencies
from ai_stock_sentinel.daily_radar.presenter import (
    history_response,
    matches_bucket,
    public_run_response,
)
from ai_stock_sentinel.daily_radar.repository import (
    get_daily_radar_run_by_date,
    get_latest_daily_radar_run,
    get_symbol_candidate_history,
)
from ai_stock_sentinel.daily_radar.schemas import DailyRadarRunResponse
from ai_stock_sentinel.db.session import get_db


router = APIRouter()


@router.get("/daily-radar/latest", response_model=DailyRadarRunResponse, response_model_exclude_none=True)
def get_latest_daily_radar_endpoint(
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyRadarRunResponse:
    run = get_latest_daily_radar_run(db, market=market)
    if run is None:
        raise HTTPException(status_code=404, detail="No public Daily Radar run is available.")
    return public_run_response(run, bucket=bucket, limit=limit)


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
        before_date=dependencies._backend_today() + timedelta(days=1),
        lookback_days=lookback_days,
        market=market,
    )
    filtered = [history_response(item) for item in history if matches_bucket(item, bucket)]
    return filtered[:limit]


@router.get("/daily-radar/{run_date}", response_model=DailyRadarRunResponse, response_model_exclude_none=True)
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
    return public_run_response(run, bucket=bucket, limit=limit)
