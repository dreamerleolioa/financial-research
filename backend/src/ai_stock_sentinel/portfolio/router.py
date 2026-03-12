# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import UserPortfolio
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

PORTFOLIO_LIMIT = 5


class PortfolioCreateRequest(BaseModel):
    symbol: str
    entry_price: float
    entry_date: date
    quantity: int = 0
    notes: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def add_portfolio(
    payload: PortfolioCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    active_count = db.execute(
        select(func.count()).select_from(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,
        )
    ).scalar()

    if active_count >= PORTFOLIO_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"最多只能追蹤 {PORTFOLIO_LIMIT} 筆持股",
        )

    entry = UserPortfolio(
        user_id=current_user.id,
        symbol=payload.symbol,
        entry_price=payload.entry_price,
        entry_date=payload.entry_date,
        quantity=payload.quantity,
        notes=payload.notes,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "symbol": entry.symbol}
