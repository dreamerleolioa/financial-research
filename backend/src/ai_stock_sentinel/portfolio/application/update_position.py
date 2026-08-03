from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import UserPortfolio
from ai_stock_sentinel.portfolio.application.events import ensure_position_event_ledger
from ai_stock_sentinel.portfolio.repository import get_owned_portfolio
from ai_stock_sentinel.portfolio.schemas import UpdatePortfolioRequest


ENTRY_FACT_CORRECTION_NOTE = (
    "Entry price, quantity, or date was manually corrected after initial recording."
)


def update_portfolio_record(
    db: Session,
    *,
    portfolio_id: int,
    user_id: int,
    payload: UpdatePortfolioRequest,
) -> UserPortfolio:
    item = get_owned_portfolio(db, portfolio_id=portfolio_id, user_id=user_id, for_update=True)
    if not item:
        raise HTTPException(status_code=403, detail="無權限")

    if not item.is_active:
        raise HTTPException(status_code=409, detail="已結案紀錄不可直接修改，請使用明確的更正流程")

    economic_fields_changed = (
        Decimal(str(item.entry_price)) != Decimal(str(payload.entry_price))
        or item.quantity != payload.quantity
        or item.entry_date != payload.entry_date
    )
    if economic_fields_changed:
        events = ensure_position_event_ledger(db, item)
        if any(event.event_type != "initial_entry" for event in events):
            raise HTTPException(status_code=409, detail="部位已有後續事件，成本、股數與日期不可直接改寫")
        if len(events) != 1:
            raise HTTPException(status_code=409, detail="部位事件帳本不唯一，請使用明確的更正流程")

        initial_event = events[0]
        initial_event.event_date = payload.entry_date
        initial_event.price = Decimal(str(payload.entry_price))
        initial_event.quantity = payload.quantity
        initial_event.source = "manual_record_correction"
        initial_event.data_quality_note = ENTRY_FACT_CORRECTION_NOTE
        initial_event.updated_at = datetime.now(timezone.utc)

    item.entry_price = payload.entry_price
    item.quantity = payload.quantity
    item.entry_date = payload.entry_date
    item.notes = payload.notes
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item
