from __future__ import annotations

from datetime import date
from decimal import Decimal

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
