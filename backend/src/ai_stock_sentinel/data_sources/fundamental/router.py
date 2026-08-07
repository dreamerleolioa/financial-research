from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.data_sources.fundamental.service import (
    backfill_fundamentals,
    refresh_official_fundamentals,
    resolve_managed_fundamental_symbols,
)
from ai_stock_sentinel.data_sources.fundamental.schemas import (
    FundamentalBackfillRequest,
    FundamentalBackfillResponse,
    FundamentalRefreshResponse,
)
from ai_stock_sentinel.db.session import get_db


router = APIRouter(tags=["fundamentals"])


@router.post(
    "/internal/fundamentals/refresh",
    response_model=FundamentalRefreshResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_fundamentals_endpoint(db: Session = Depends(get_db)) -> dict:
    result = refresh_official_fundamentals(db)
    db.commit()
    return asdict(result)


@router.post(
    "/internal/fundamentals/backfill",
    response_model=FundamentalBackfillResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def backfill_fundamentals_endpoint(
    payload: FundamentalBackfillRequest,
    db: Session = Depends(get_db),
) -> dict:
    symbols = payload.symbols or resolve_managed_fundamental_symbols(db)
    result = backfill_fundamentals(
        db,
        symbols=symbols,
        after_symbol=payload.after_symbol,
        limit=payload.limit,
    )
    db.commit()
    return asdict(result)


__all__ = ["FundamentalBackfillRequest", "router"]
