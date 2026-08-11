from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import UserPortfolio
from ai_stock_sentinel.portfolio.application.events import (
    add_position_event,
    ensure_position_event_ledger,
    exit_reason_category,
    exit_reason_code,
    ledger_open_quantity,
)
from ai_stock_sentinel.portfolio.fees import calculate_broker_fee, calculate_sell_transaction_tax
from ai_stock_sentinel.portfolio.repository import get_owned_portfolio
from ai_stock_sentinel.portfolio.schemas import ClosePortfolioRequest
from ai_stock_sentinel.portfolio.storage_limits import (
    POSITION_EVENT_MONEY_MAX,
    POSITION_EVENT_MONEY_QUANTUM,
    REALIZED_PNL_MAX,
    REALIZED_PNL_QUANTUM,
    REALIZED_RETURN_PCT_MAX,
    REALIZED_RETURN_PCT_QUANTUM,
    quantize_for_storage,
)


def close_position(
    db: Session,
    *,
    portfolio_id: int,
    user_id: int,
    payload: ClosePortfolioRequest,
) -> UserPortfolio:
    item = get_owned_portfolio(db, portfolio_id=portfolio_id, user_id=user_id, for_update=True)
    if not item:
        raise HTTPException(status_code=403, detail="無權限")

    if not item.is_active:
        raise HTTPException(status_code=409, detail="持倉已關閉")

    if payload.exit_quantity > item.quantity:
        raise HTTPException(status_code=422, detail="出場股數不可大於持有股數")

    if payload.exit_date < item.entry_date:
        raise HTTPException(status_code=422, detail="出場日期不可早於進場日期")

    entry_price = Decimal(str(item.entry_price))
    if entry_price <= 0:
        raise HTTPException(status_code=422, detail="成本價必須大於 0")

    events = ensure_position_event_ledger(db, item)
    latest_event_date = max(event.event_date for event in events)
    if payload.exit_date < latest_event_date:
        raise HTTPException(status_code=422, detail="事件日期不可早於目前帳本的最新事件")
    if ledger_open_quantity(events) != item.quantity:
        raise HTTPException(status_code=409, detail="事件帳本持有股數與持倉資料不一致，請先更正帳本")

    exit_price = Decimal(str(payload.exit_price))
    exit_quantity = Decimal(payload.exit_quantity)
    gross_exit_amount = exit_price * exit_quantity
    explicit_fee = Decimal(str(payload.fees)) if payload.fees is not None else None
    explicit_tax = Decimal(str(payload.taxes)) if payload.taxes is not None else None
    row_fees = quantize_for_storage(
        calculate_broker_fee(gross_exit_amount, actual_fee=explicit_fee),
        POSITION_EVENT_MONEY_QUANTUM,
    )
    row_taxes = quantize_for_storage(
        calculate_sell_transaction_tax(gross_exit_amount, explicit_tax=explicit_tax),
        POSITION_EVENT_MONEY_QUANTUM,
    )
    raw_realized_pnl = (exit_price - entry_price) * exit_quantity - row_fees - row_taxes
    realized_pnl = quantize_for_storage(raw_realized_pnl, REALIZED_PNL_QUANTUM)
    realized_return_pct = quantize_for_storage(
        raw_realized_pnl / (entry_price * exit_quantity) * Decimal("100"),
        REALIZED_RETURN_PCT_QUANTUM,
    )
    if (
        row_fees > POSITION_EVENT_MONEY_MAX
        or row_taxes > POSITION_EVENT_MONEY_MAX
        or abs(realized_pnl) > REALIZED_PNL_MAX
        or abs(realized_return_pct) > REALIZED_RETURN_PCT_MAX
    ):
        raise HTTPException(status_code=422, detail="結案金額超過系統可儲存範圍")

    holding_days = (payload.exit_date - item.entry_date).days
    updated_at = datetime.now(timezone.utc)

    if payload.exit_quantity == item.quantity:
        item.is_active = False
        item.exit_date = payload.exit_date
        item.exit_price = exit_price
        item.exit_quantity = payload.exit_quantity
        item.exit_fees = row_fees
        item.exit_taxes = row_taxes
        item.realized_pnl = realized_pnl
        item.realized_return_pct = realized_return_pct
        item.holding_days = holding_days
        item.updated_at = updated_at
        add_position_event(
            db,
            item=item,
            event_type="full_exit",
            event_date=payload.exit_date,
            price=exit_price,
            quantity=payload.exit_quantity,
            fees=row_fees,
            taxes=row_taxes,
            source_portfolio_id=item.id,
            reason_category=exit_reason_category(payload.reason_code),
            reason_code=exit_reason_code(payload.reason_code),
            plan_adherence=payload.plan_adherence,
            confidence_level=payload.confidence_level,
        )
        db.commit()
        db.refresh(item)
        return item

    item.quantity -= payload.exit_quantity
    item.updated_at = updated_at
    closed_item = UserPortfolio(
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        entry_price=item.entry_price,
        quantity=payload.exit_quantity,
        entry_date=item.entry_date,
        is_active=False,
        exit_date=payload.exit_date,
        exit_price=exit_price,
        exit_quantity=payload.exit_quantity,
        exit_fees=row_fees,
        exit_taxes=row_taxes,
        realized_pnl=realized_pnl,
        realized_return_pct=realized_return_pct,
        holding_days=holding_days,
        notes=item.notes,
    )
    db.add(closed_item)
    db.flush()
    add_position_event(
        db,
        item=closed_item,
        event_type="partial_exit",
        event_date=payload.exit_date,
        price=exit_price,
        quantity=payload.exit_quantity,
        fees=row_fees,
        taxes=row_taxes,
        source_portfolio_id=closed_item.id,
        reason_category=exit_reason_category(payload.reason_code),
        reason_code=exit_reason_code(payload.reason_code),
        plan_adherence=payload.plan_adherence,
        confidence_level=payload.confidence_level,
    )
    db.commit()
    db.refresh(closed_item)
    return closed_item
