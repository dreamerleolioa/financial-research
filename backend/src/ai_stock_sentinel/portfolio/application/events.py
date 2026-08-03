from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import PositionEvent, UserPortfolio
from ai_stock_sentinel.portfolio.entry_record_contract import EntryRecordContext


ENTRY_REASON_CATEGORIES = {
    "breakout_confirmation": "technical",
    "pullback_held_support": "technical",
    "pullback_held_ma20": "technical",
    "institutional_flow_strengthened": "institutional_flow",
    "fundamental_thesis_improved": "fundamental",
    "event_or_news_catalyst": "news",
    "long_term_accumulation": "plan_execution",
    "value_revaluation": "fundamental",
    "other": "plan_execution",
}


def add_position_event(
    db: Session,
    *,
    item: UserPortfolio,
    event_type: str,
    event_date: date,
    price: Decimal,
    quantity: int,
    fees: Decimal = Decimal("0"),
    taxes: Decimal = Decimal("0"),
    source_portfolio_id: int | None = None,
    note: str | None = None,
    reason_category: str | None = None,
    reason_code: str | None = None,
    source: str = "user_recorded_at_event_time",
    data_quality_note: str | None = None,
) -> PositionEvent:
    event = PositionEvent(
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        event_type=event_type,
        event_date=event_date,
        price=price,
        quantity=quantity,
        fees=fees,
        taxes=taxes,
        source_portfolio_id=source_portfolio_id if source_portfolio_id is not None else item.id,
        note=item.notes if note is None else note,
        reason_category=reason_category,
        reason_code=reason_code,
        source=source,
        data_quality_note=data_quality_note,
    )
    db.add(event)
    return event


def ensure_position_event_ledger(db: Session, item: UserPortfolio) -> list[PositionEvent]:
    events = list(db.execute(
        select(PositionEvent)
        .where(
            PositionEvent.user_id == item.user_id,
            PositionEvent.position_group_id == item.position_group_id,
        )
        .order_by(PositionEvent.event_date.asc(), PositionEvent.created_at.asc(), PositionEvent.id.asc())
        .with_for_update()
    ).scalars().all())
    if events:
        return events

    sibling_portfolio_ids = list(db.execute(
        select(UserPortfolio.id).where(
            UserPortfolio.user_id == item.user_id,
            UserPortfolio.position_group_id == item.position_group_id,
            UserPortfolio.id != item.id,
        )
    ).scalars().all())
    if sibling_portfolio_ids:
        raise HTTPException(
            status_code=409,
            detail="舊部位群組缺少事件帳本且已有分批紀錄，無法安全自動補帳",
        )

    initial_event = add_position_event(
        db,
        item=item,
        event_type="initial_entry",
        event_date=item.entry_date,
        price=Decimal(str(item.entry_price)),
        quantity=item.quantity,
        source_portfolio_id=item.id,
        source="user_backfilled",
        data_quality_note="legacy portfolio row backfilled before lifecycle mutation",
    )
    return [initial_event]


def ledger_open_quantity(events: list[PositionEvent]) -> int:
    entry_quantity = sum(
        int(event.quantity)
        for event in events
        if event.event_type in {"initial_entry", "add_entry"}
    )
    exit_quantity = sum(
        int(event.quantity)
        for event in events
        if event.event_type in {"partial_exit", "full_exit"}
    )
    return entry_quantity - exit_quantity


def entry_reason_category(entry_reason: str | None) -> str | None:
    if entry_reason is None:
        return None
    if entry_reason == "not_recorded":
        return "not_recorded"
    return ENTRY_REASON_CATEGORIES[entry_reason]


def entry_reason_code(entry_reason: str | None) -> str | None:
    if entry_reason in (None, "not_recorded"):
        return None
    return entry_reason


def add_entry_reason_category(reason_code: str) -> str:
    if reason_code == "not_recorded":
        return "not_recorded"
    if reason_code in {"planned_scale_in", "averaging_down", "chasing_momentum"}:
        return "plan_execution"
    return ENTRY_REASON_CATEGORIES[reason_code]


def add_entry_reason_code(reason_code: str) -> str | None:
    return None if reason_code == "not_recorded" else reason_code


def entry_record_has_lifecycle_plan(entry_record: EntryRecordContext) -> bool:
    return any(
        field in entry_record.model_fields_set
        for field in ("planned_holding_period", "default_stop_rule", "planned_stop_price", "add_entry_condition")
    )
