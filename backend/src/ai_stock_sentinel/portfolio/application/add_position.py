from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import PositionLifecyclePlan, UserPortfolio
from ai_stock_sentinel.portfolio.application.events import (
    add_position_event,
    entry_reason_category,
    entry_reason_code,
    entry_record_has_lifecycle_plan,
)
from ai_stock_sentinel.portfolio.repository import count_active_portfolios
from ai_stock_sentinel.portfolio.schemas import PortfolioCreateRequest


PORTFOLIO_LIMIT = 8


def create_portfolio(
    db: Session,
    *,
    user_id: int,
    payload: PortfolioCreateRequest,
    symbol_exists_checker: Callable[[str], bool],
    portfolio_limit: int = PORTFOLIO_LIMIT,
) -> UserPortfolio:
    active_count = count_active_portfolios(db, user_id=user_id)

    if active_count >= portfolio_limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"最多只能追蹤 {portfolio_limit} 筆持股",
        )

    if not symbol_exists_checker(payload.symbol):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"查詢目標不存在：{payload.symbol}",
        )

    entry = UserPortfolio(
        user_id=user_id,
        position_group_id=str(uuid.uuid4()),
        symbol=payload.symbol,
        entry_price=payload.entry_price,
        entry_date=payload.entry_date,
        quantity=payload.quantity,
        notes=payload.notes,
    )
    db.add(entry)
    db.flush()
    add_position_event(
        db,
        item=entry,
        event_type="initial_entry",
        event_date=entry.entry_date,
        price=Decimal(str(entry.entry_price)),
        quantity=entry.quantity,
        source_portfolio_id=entry.id,
        note=payload.entry_record.note if payload.entry_record and payload.entry_record.note is not None else None,
        reason_category=entry_reason_category(payload.entry_record.entry_reason) if payload.entry_record else None,
        reason_code=entry_reason_code(payload.entry_record.entry_reason) if payload.entry_record else None,
    )
    if payload.entry_record and entry_record_has_lifecycle_plan(payload.entry_record):
        db.add(PositionLifecyclePlan(
            user_id=entry.user_id,
            position_group_id=entry.position_group_id,
            symbol=entry.symbol,
            source_portfolio_id=entry.id,
            planned_holding_period=payload.entry_record.planned_holding_period,
            default_stop_rule=payload.entry_record.default_stop_rule,
            add_entry_condition=payload.entry_record.add_entry_condition,
            source="user_recorded_at_event_time",
            created_after_entry=False,
        ))
    db.commit()
    db.refresh(entry)
    return entry
