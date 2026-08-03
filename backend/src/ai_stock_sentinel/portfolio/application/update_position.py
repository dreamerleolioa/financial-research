from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import PositionEvent, UserPortfolio
from ai_stock_sentinel.portfolio.application.events import add_position_event
from ai_stock_sentinel.portfolio.repository import get_owned_portfolio
from ai_stock_sentinel.portfolio.schemas import UpdatePortfolioRequest


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
    events = list(db.execute(
        select(PositionEvent)
        .where(
            PositionEvent.user_id == user_id,
            PositionEvent.position_group_id == item.position_group_id,
        )
        .order_by(PositionEvent.event_date.asc(), PositionEvent.created_at.asc(), PositionEvent.id.asc())
        .with_for_update()
    ).scalars().all())

    if economic_fields_changed and any(event.event_type != "initial_entry" for event in events):
        raise HTTPException(status_code=409, detail="部位已有後續事件，成本、股數與日期不可直接改寫")
    if economic_fields_changed and len(events) > 1:
        raise HTTPException(status_code=409, detail="部位事件帳本不唯一，請使用明確的更正流程")

    if economic_fields_changed:
        if events:
            initial_event = events[0]
            initial_event.event_date = payload.entry_date
            initial_event.price = Decimal(str(payload.entry_price))
            initial_event.quantity = payload.quantity
            initial_event.updated_at = datetime.now(timezone.utc)
        else:
            add_position_event(
                db,
                item=item,
                event_type="initial_entry",
                event_date=payload.entry_date,
                price=Decimal(str(payload.entry_price)),
                quantity=payload.quantity,
                source_portfolio_id=item.id,
                note=payload.notes,
                source="user_backfilled",
                data_quality_note="legacy portfolio row corrected before lifecycle events",
            )

    item.entry_price = payload.entry_price
    item.quantity = payload.quantity
    item.entry_date = payload.entry_date
    item.notes = payload.notes
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item
