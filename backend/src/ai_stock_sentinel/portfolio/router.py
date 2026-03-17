# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
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


@router.get("")
def list_portfolio(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,
        ).order_by(UserPortfolio.created_at.desc())
    ).scalars().all()

    return [
        {
            "id":          r.id,
            "symbol":      r.symbol,
            "entry_price": float(r.entry_price),
            "quantity":    r.quantity,
            "entry_date":  r.entry_date.isoformat(),
            "notes":       r.notes,
        }
        for r in rows
    ]


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


class UpdatePortfolioRequest(BaseModel):
    entry_price: float
    quantity: int
    entry_date: date
    notes: str | None = None


@router.put("/{portfolio_id}")
def update_portfolio(
    portfolio_id: int,
    payload: UpdatePortfolioRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(UserPortfolio, portfolio_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="無權限")
    item.entry_price = payload.entry_price
    item.quantity = payload.quantity
    item.entry_date = payload.entry_date
    item.notes = payload.notes
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return {
        "id":          item.id,
        "symbol":      item.symbol,
        "entry_price": float(item.entry_price),
        "quantity":    item.quantity,
        "entry_date":  item.entry_date.isoformat() if hasattr(item.entry_date, "isoformat") else item.entry_date,
        "notes":       item.notes,
    }


@router.delete("/{portfolio_id}", status_code=204)
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(UserPortfolio, portfolio_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="無權限")
    db.execute(
        text("DELETE FROM daily_analysis_log WHERE user_id = :uid AND symbol = :sym"),
        {"uid": current_user.id, "sym": item.symbol},
    )
    db.delete(item)
    db.commit()
