from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import PositionEvent, UserPortfolio
from ai_stock_sentinel.portfolio.application.events import (
    add_entry_reason_category,
    add_entry_reason_code,
    add_position_event,
)
from ai_stock_sentinel.portfolio.fees import calculate_broker_fee
from ai_stock_sentinel.portfolio.repository import get_owned_portfolio
from ai_stock_sentinel.portfolio.schemas import AddEntryRequest


def add_entry_to_position(
    db: Session,
    *,
    portfolio_id: int,
    user_id: int,
    payload: AddEntryRequest,
) -> tuple[UserPortfolio, PositionEvent]:
    item = get_owned_portfolio(db, portfolio_id=portfolio_id, user_id=user_id, for_update=True)
    if not item:
        raise HTTPException(status_code=403, detail="無權限")
    if not item.is_active:
        raise HTTPException(status_code=409, detail="持倉已關閉")
    if payload.event_date < item.entry_date:
        raise HTTPException(status_code=422, detail="加碼日期不可早於初始進場日期")

    add_price = Decimal(str(payload.price))
    add_quantity = Decimal(payload.quantity)
    existing_quantity = Decimal(item.quantity)
    new_quantity = existing_quantity + add_quantity
    gross_amount = add_price * add_quantity
    event_fees = calculate_broker_fee(
        gross_amount,
        actual_fee=Decimal(str(payload.fees)) if payload.fees is not None else None,
    )
    event_taxes = Decimal(str(payload.taxes)) if payload.taxes is not None else Decimal("0")
    item.entry_price = ((Decimal(str(item.entry_price)) * existing_quantity) + gross_amount) / new_quantity
    item.quantity = int(new_quantity)
    item.updated_at = datetime.now(timezone.utc)

    event = add_position_event(
        db,
        item=item,
        event_type="add_entry",
        event_date=payload.event_date,
        price=add_price,
        quantity=payload.quantity,
        fees=event_fees,
        taxes=event_taxes,
        source_portfolio_id=item.id,
        note=payload.note,
        reason_category=add_entry_reason_category(payload.reason_code),
        reason_code=add_entry_reason_code(payload.reason_code),
        source="user_recorded_at_event_time",
    )
    event.plan_adherence = payload.plan_adherence
    event.confidence_level = payload.confidence_level
    db.commit()
    db.refresh(item)
    db.refresh(event)
    return item, event
