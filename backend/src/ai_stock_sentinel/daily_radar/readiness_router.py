"""Authenticated operational reads, separate from public research summaries."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.readiness import read_readiness
from ai_stock_sentinel.db.session import get_db

router = APIRouter()


@router.get("/internal/daily-radar/readiness", dependencies=[Depends(require_daily_radar_internal_auth)])
def get_readiness(run_date: date, market: Literal["TW"] = "TW",
                  lookback_days: int = Query(default=365, ge=1, le=1095), db: Session = Depends(get_db),
                  include_all_validation_gaps: bool = False):
    with db.no_autoflush:
        return read_readiness(db, run_date=run_date, lookback_days=lookback_days,
                              include_all_validation_gaps=include_all_validation_gaps)
