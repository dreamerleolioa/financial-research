from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import UserPortfolio
from ai_stock_sentinel.portfolio.schemas import UpdatePortfolioRequest


def update_portfolio_record(
    db: Session,
    *,
    portfolio_id: int,
    user_id: int,
    payload: UpdatePortfolioRequest,
) -> UserPortfolio:
    item = db.get(UserPortfolio, portfolio_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=403, detail="無權限")
    item.entry_price = payload.entry_price
    item.quantity = payload.quantity
    item.entry_date = payload.entry_date
    item.notes = payload.notes
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item
