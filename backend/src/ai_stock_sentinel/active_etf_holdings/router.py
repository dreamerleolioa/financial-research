from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ai_stock_sentinel.active_etf_holdings.provider import (
    ActiveEtfProviderError,
    MoneyDjActiveEtfProvider,
)
from ai_stock_sentinel.active_etf_holdings.schemas import (
    ActiveEtfDailyResponse,
    ActiveEtfRefreshRequest,
    ActiveEtfRefreshResponse,
)
from ai_stock_sentinel.active_etf_holdings.service import (
    ActiveEtfHoldingsProvider,
    get_active_etf_daily_response,
    refresh_active_etf_holdings,
)
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.user_models.user import User


router = APIRouter(tags=["active-etf-holdings"])


def get_active_etf_holdings_provider() -> ActiveEtfHoldingsProvider:
    return MoneyDjActiveEtfProvider()


@router.post(
    "/internal/active-etf-holdings/refresh",
    response_model=ActiveEtfRefreshResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_active_etf_holdings_endpoint(
    payload: ActiveEtfRefreshRequest,
    db: Session = Depends(get_db),
    provider: ActiveEtfHoldingsProvider = Depends(get_active_etf_holdings_provider),
) -> ActiveEtfRefreshResponse:
    try:
        return refresh_active_etf_holdings(
            db,
            provider=provider,
            fund_codes=payload.fund_codes,
        )
    except ActiveEtfProviderError as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail={"code": str(exc)},
        ) from exc


@router.get(
    "/active-etf-holdings/daily",
    response_model=ActiveEtfDailyResponse,
)
def get_active_etf_holdings_daily_endpoint(
    data_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> ActiveEtfDailyResponse:
    response = get_active_etf_daily_response(db, data_date=data_date)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "active_etf_holdings_not_found"},
        )
    return response


__all__ = [
    "get_active_etf_holdings_provider",
    "router",
]
