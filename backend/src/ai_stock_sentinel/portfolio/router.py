# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.position_lifecycle import build_position_lifecycle_analysis
from ai_stock_sentinel.analysis.trade_review import build_trade_review_payload, ensure_trade_review_market_data
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.data_sources.yfinance_client import check_symbol_exists
from ai_stock_sentinel.db.models import (
    PositionEvent,
    PositionLifecyclePlan,
    PositionLifecycleReview,
    StockRawData,
    TradeReview,
    UserPortfolio,
)
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.portfolio.entry_record_contract import EntryRecordContext
from ai_stock_sentinel.portfolio.fees import calculate_broker_fee, calculate_sell_transaction_tax
from ai_stock_sentinel.portfolio.risk_summary import build_portfolio_risk_summary
from ai_stock_sentinel.shared_context import (
    SHARED_CONTEXT_CONSUMER_PORTFOLIO,
    read_shared_context_for_symbol,
)
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

PORTFOLIO_LIMIT = 8
TRADE_REVIEW_VERSION = "trade-review-v1"
POSITION_LIFECYCLE_REVIEW_VERSION = "position-lifecycle-review-v1"

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


class PortfolioCreateRequest(BaseModel):
    symbol: str
    entry_price: float = Field(gt=0)
    entry_date: date
    quantity: int = 0
    notes: str | None = None
    entry_record: EntryRecordContext | None = None


class ClosePortfolioRequest(BaseModel):
    exit_date: date
    exit_price: float = Field(gt=0, allow_inf_nan=False)
    exit_quantity: int = Field(gt=0)
    fees: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    taxes: float | None = Field(default=None, ge=0, allow_inf_nan=False)


AddEntryReasonCode = Literal[
    "breakout_confirmation",
    "pullback_held_support",
    "pullback_held_ma20",
    "institutional_flow_strengthened",
    "fundamental_thesis_improved",
    "event_or_news_catalyst",
    "long_term_accumulation",
    "value_revaluation",
    "other",
    "planned_scale_in",
    "averaging_down",
    "chasing_momentum",
    "not_recorded",
]

LifecycleSetupType = Literal[
    "breakout",
    "pullback",
    "mean_reversion",
    "value_revaluation",
    "earnings_or_event",
    "momentum_continuation",
    "long_term_accumulation",
    "defensive_rebalance",
    "other",
]

PlannedHoldingPeriod = Literal["short_term", "swing", "medium_term", "long_term", "not_recorded"]
DefaultStopRule = Literal[
    "break_20d_low",
    "break_ma20",
    "break_ma60",
    "cost_minus_pct",
    "fixed_price",
    "no_stop_recorded",
    "not_recorded",
]
AddEntryCondition = Literal[
    "no_add_entry",
    "breakout_above_prior_high",
    "pullback_holds_ma20",
    "pullback_holds_support",
    "institutional_flow_continues",
    "profit_threshold_reached",
    "data_quality_complete_only",
    "no_averaging_down",
    "custom_plan_required",
    "not_recorded",
]


class AddEntryRequest(BaseModel):
    event_date: date
    price: float = Field(gt=0, allow_inf_nan=False)
    quantity: int = Field(gt=0)
    fees: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    taxes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reason_code: AddEntryReasonCode
    plan_adherence: Literal["yes", "partial", "no", "not_recorded"]
    confidence_level: Literal["high", "medium", "low", "not_recorded"]
    note: str | None = None


class BackfillLifecyclePlanRequest(BaseModel):
    thesis: str | None = None
    setup_type: LifecycleSetupType | None = None
    planned_holding_period: PlannedHoldingPeriod | None = None
    default_stop_rule: DefaultStopRule | None = None
    add_entry_condition: AddEntryCondition | None = None
    planned_invalidation: str | None = None
    planned_stop_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    planned_target_or_scale_out_rule: str | None = None
    planned_risk_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    planned_risk_pct: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    position_sizing_rationale: str | None = None


def _serialize_portfolio(item: UserPortfolio) -> dict:
    return {
        "id": item.id,
        "position_group_id": item.position_group_id,
        "symbol": item.symbol,
        "name": resolve_symbol_name(item.symbol),
        "entry_price": float(item.entry_price),
        "quantity": item.quantity,
        "entry_date": item.entry_date.isoformat() if hasattr(item.entry_date, "isoformat") else item.entry_date,
        "is_active": item.is_active,
        "exit_date": item.exit_date.isoformat() if item.exit_date and hasattr(item.exit_date, "isoformat") else item.exit_date,
        "exit_price": float(item.exit_price) if item.exit_price is not None else None,
        "exit_quantity": item.exit_quantity,
        "exit_fees": float(item.exit_fees) if item.exit_fees is not None else None,
        "exit_taxes": float(item.exit_taxes) if item.exit_taxes is not None else None,
        "realized_pnl": float(item.realized_pnl) if item.realized_pnl is not None else None,
        "realized_return_pct": float(item.realized_return_pct) if item.realized_return_pct is not None else None,
        "holding_days": item.holding_days,
        "notes": item.notes,
    }


def _serialize_trade_review(review: TradeReview) -> dict:
    return {
        "id": review.id,
        "portfolio_id": review.portfolio_id,
        "user_id": review.user_id,
        "position_group_id": review.position_group_id,
        "symbol": review.symbol,
        "review_version": review.review_version,
        "review_result": review.review_result,
        "evidence_payload": review.evidence_payload,
        "llm_summary": review.llm_summary,
        "created_at": review.created_at.isoformat() if review.created_at and hasattr(review.created_at, "isoformat") else review.created_at,
        "updated_at": review.updated_at.isoformat() if review.updated_at and hasattr(review.updated_at, "isoformat") else review.updated_at,
    }


def _serialize_position_lifecycle_review(review: PositionLifecycleReview) -> dict:
    return {
        "id": review.id,
        "user_id": review.user_id,
        "position_group_id": review.position_group_id,
        "symbol": review.symbol,
        "review_version": review.review_version,
        "review_result": review.review_result,
        "evidence_payload": review.evidence_payload,
        "llm_summary": review.llm_summary,
        "created_at": review.created_at.isoformat() if review.created_at and hasattr(review.created_at, "isoformat") else review.created_at,
        "updated_at": review.updated_at.isoformat() if review.updated_at and hasattr(review.updated_at, "isoformat") else review.updated_at,
    }


def _serialize_position_event(event: PositionEvent) -> dict:
    return {
        "id": event.id,
        "position_group_id": event.position_group_id,
        "symbol": event.symbol,
        "event_type": event.event_type,
        "event_date": event.event_date.isoformat() if hasattr(event.event_date, "isoformat") else event.event_date,
        "price": float(event.price),
        "quantity": event.quantity,
        "fees": float(event.fees),
        "taxes": float(event.taxes),
        "source_portfolio_id": event.source_portfolio_id,
        "note": event.note,
        "reason_category": event.reason_category,
        "reason_code": event.reason_code,
        "plan_adherence": event.plan_adherence,
        "confidence_level": event.confidence_level,
        "source": event.source,
        "data_quality_note": event.data_quality_note,
        "created_at": event.created_at.isoformat() if event.created_at and hasattr(event.created_at, "isoformat") else event.created_at,
        "updated_at": event.updated_at.isoformat() if event.updated_at and hasattr(event.updated_at, "isoformat") else event.updated_at,
    }


def _serialize_lifecycle_plan(item: UserPortfolio, plan: PositionLifecyclePlan | None) -> dict:
    return {
        "portfolio_id": item.id,
        "position_group_id": item.position_group_id,
        "symbol": item.symbol,
        "thesis": plan.thesis if plan else None,
        "setup_type": plan.setup_type if plan else None,
        "planned_holding_period": plan.planned_holding_period if plan else None,
        "default_stop_rule": plan.default_stop_rule if plan else None,
        "add_entry_condition": plan.add_entry_condition if plan else None,
        "planned_invalidation": plan.planned_invalidation if plan else None,
        "planned_stop_price": float(plan.planned_stop_price) if plan and plan.planned_stop_price is not None else None,
        "planned_target_or_scale_out_rule": plan.planned_target_or_scale_out_rule if plan else None,
        "planned_risk_amount": float(plan.planned_risk_amount) if plan and plan.planned_risk_amount is not None else None,
        "planned_risk_pct": float(plan.planned_risk_pct) if plan and plan.planned_risk_pct is not None else None,
        "position_sizing_rationale": plan.position_sizing_rationale if plan else None,
        "source": plan.source if plan else None,
        "created_after_entry": plan.created_after_entry if plan else None,
    }


def _serialize_decision_context_status(
    item: UserPortfolio,
    plan: PositionLifecyclePlan | None,
    *,
    shared_context: dict | None = None,
) -> dict:
    operation_plan_status = "missing"
    if plan is not None:
        operation_plan_status = "backfilled" if plan.source == "user_backfilled" or plan.created_after_entry else "present"
    return {
        "portfolio_id": item.id,
        "position_group_id": item.position_group_id,
        "symbol": item.symbol,
        "has_operation_plan": plan is not None,
        "operation_plan_status": operation_plan_status,
        "missing_operation_plan": plan is None,
        "decision_context": "present" if plan is not None else "insufficient",
        "source": plan.source if plan is not None else None,
        "created_after_entry": plan.created_after_entry if plan is not None else None,
        "planned_invalidation_present": bool(plan and plan.planned_invalidation),
        "shared_context": shared_context,
    }


def _add_position_event(
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


def _entry_reason_category(entry_reason: str | None) -> str | None:
    if entry_reason is None:
        return None
    if entry_reason == "not_recorded":
        return "not_recorded"
    return ENTRY_REASON_CATEGORIES[entry_reason]


def _entry_reason_code(entry_reason: str | None) -> str | None:
    if entry_reason in (None, "not_recorded"):
        return None
    return entry_reason


def _add_entry_reason_category(reason_code: str) -> str:
    if reason_code == "not_recorded":
        return "not_recorded"
    if reason_code in {"planned_scale_in", "averaging_down", "chasing_momentum"}:
        return "plan_execution"
    return ENTRY_REASON_CATEGORIES[reason_code]


def _add_entry_reason_code(reason_code: str) -> str | None:
    return None if reason_code == "not_recorded" else reason_code


def _entry_record_has_lifecycle_plan(entry_record: EntryRecordContext) -> bool:
    return any(
        field in entry_record.model_fields_set
        for field in ("planned_holding_period", "default_stop_rule", "add_entry_condition")
    )


def _get_reviewable_portfolio(db: Session, portfolio_id: int, user_id: int) -> UserPortfolio:
    item = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.id == portfolio_id,
            UserPortfolio.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=403, detail="無權限")
    if item.is_active or item.exit_date is None:
        raise HTTPException(status_code=422, detail="僅可審核已結案持倉")
    return item


def _get_owned_active_portfolio_for_update(db: Session, portfolio_id: int, user_id: int) -> UserPortfolio:
    item = db.execute(
        select(UserPortfolio)
        .where(
            UserPortfolio.id == portfolio_id,
            UserPortfolio.user_id == user_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=403, detail="無權限")
    if not item.is_active:
        raise HTTPException(status_code=409, detail="持倉已關閉")
    return item


def _get_owned_position_group(db: Session, position_group_id: str, user_id: int) -> UserPortfolio:
    group = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == user_id,
            UserPortfolio.position_group_id == position_group_id,
        )
    ).scalars().first()
    if not group:
        raise HTTPException(status_code=403, detail="無權限")
    return group


def _get_position_lifecycle_review(db: Session, position_group_id: str, user_id: int) -> PositionLifecycleReview | None:
    return db.execute(
        select(PositionLifecycleReview).where(
            PositionLifecycleReview.user_id == user_id,
            PositionLifecycleReview.position_group_id == position_group_id,
            PositionLifecycleReview.review_version == POSITION_LIFECYCLE_REVIEW_VERSION,
        )
    ).scalar_one_or_none()


def _as_comparable_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _get_position_lifecycle_source_updated_at(db: Session, position_group_id: str, user_id: int) -> datetime | None:
    latest_event_updated_at = db.execute(
        select(func.max(PositionEvent.updated_at)).where(
            PositionEvent.user_id == user_id,
            PositionEvent.position_group_id == position_group_id,
        )
    ).scalar_one_or_none()
    latest_plan_updated_at = db.execute(
        select(func.max(PositionLifecyclePlan.updated_at)).where(
            PositionLifecyclePlan.user_id == user_id,
            PositionLifecyclePlan.position_group_id == position_group_id,
        )
    ).scalar_one_or_none()
    updated_ats = [updated_at for updated_at in (latest_event_updated_at, latest_plan_updated_at) if updated_at is not None]
    if not updated_ats:
        return None
    return max(updated_ats, key=_as_comparable_datetime)


def _position_lifecycle_review_is_fresh(review: PositionLifecycleReview, source_updated_at: datetime | None) -> bool:
    if source_updated_at is None:
        return True
    if review.updated_at is None:
        return False
    return _as_comparable_datetime(source_updated_at) <= _as_comparable_datetime(review.updated_at)


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
            "name":        resolve_symbol_name(r.symbol),
            "entry_price": float(r.entry_price),
            "quantity":    r.quantity,
            "entry_date":  r.entry_date.isoformat(),
            "notes":       r.notes,
        }
        for r in rows
    ]


@router.get("/closed")
def list_closed_portfolio(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == False,
            UserPortfolio.exit_date.is_not(None),
        ).order_by(UserPortfolio.exit_date.desc(), UserPortfolio.updated_at.desc())
    ).scalars().all()

    return [_serialize_portfolio(row) for row in rows]


@router.get("/decision-context-status")
def list_decision_context_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,
        ).order_by(UserPortfolio.created_at.desc())
    ).scalars().all()

    group_ids = [row.position_group_id for row in rows]
    plans = []
    if group_ids:
        plans = db.execute(
            select(PositionLifecyclePlan).where(
                PositionLifecyclePlan.user_id == current_user.id,
                PositionLifecyclePlan.position_group_id.in_(group_ids),
            )
        ).scalars().all()
    plan_by_group = {plan.position_group_id: plan for plan in plans}

    reference_date = date.today()
    shared_context_by_portfolio_id = {
        row.id: read_shared_context_for_symbol(
            db,
            symbol=row.symbol,
            consumer=SHARED_CONTEXT_CONSUMER_PORTFOLIO,
            reference_date=reference_date,
            point_in_time=True,
        )
        for row in rows
    }

    return {
        str(row.id): _serialize_decision_context_status(
            row,
            plan_by_group.get(row.position_group_id),
            shared_context=shared_context_by_portfolio_id.get(row.id),
        )
        for row in rows
    }


@router.get("/risk-summary")
def get_portfolio_risk_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,
        ).order_by(UserPortfolio.created_at.desc())
    ).scalars().all()

    group_ids = [row.position_group_id for row in rows]
    plans = []
    if group_ids:
        plans = db.execute(
            select(PositionLifecyclePlan).where(
                PositionLifecyclePlan.user_id == current_user.id,
                PositionLifecyclePlan.position_group_id.in_(group_ids),
            )
        ).scalars().all()
    plans_by_group = {plan.position_group_id: plan for plan in plans}

    symbols = sorted({row.symbol for row in rows})
    raw_data_by_symbol = {}
    if symbols:
        raw_rows = db.execute(
            select(StockRawData)
            .where(
                StockRawData.symbol.in_(symbols),
                StockRawData.raw_data_is_final.is_(True),
            )
            .order_by(StockRawData.symbol.asc(), StockRawData.record_date.desc(), StockRawData.id.desc())
        ).scalars().all()
        for raw_row in raw_rows:
            raw_data_by_symbol.setdefault(raw_row.symbol, raw_row)

    return build_portfolio_risk_summary(
        rows,
        plans_by_group=plans_by_group,
        raw_data_by_symbol=raw_data_by_symbol,
        symbol_names_by_symbol={symbol: resolve_symbol_name(symbol) for symbol in symbols},
        as_of_date=date.today(),
    )


@router.get("/groups/{position_group_id}/events")
def get_position_group_events(
    position_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.position_group_id == position_group_id,
        )
    ).scalars().first()
    if not group:
        raise HTTPException(status_code=403, detail="無權限")

    events = db.execute(
        select(PositionEvent)
        .where(
            PositionEvent.user_id == current_user.id,
            PositionEvent.position_group_id == position_group_id,
        )
        .order_by(PositionEvent.event_date.asc(), PositionEvent.created_at.asc(), PositionEvent.id.asc())
    ).scalars().all()

    return {
        "position_group_id": position_group_id,
        "symbol": group.symbol,
        "events": [_serialize_position_event(event) for event in events],
    }


@router.get("/{portfolio_id}/lifecycle-plan")
def get_portfolio_lifecycle_plan(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(UserPortfolio, portfolio_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="無權限")

    plan = db.execute(
        select(PositionLifecyclePlan).where(
            PositionLifecyclePlan.user_id == current_user.id,
            PositionLifecyclePlan.position_group_id == item.position_group_id,
        )
    ).scalar_one_or_none()
    return _serialize_lifecycle_plan(item, plan)


@router.put("/{portfolio_id}/lifecycle-plan/backfill")
def backfill_portfolio_lifecycle_plan(
    portfolio_id: int,
    payload: BackfillLifecyclePlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = _get_owned_active_portfolio_for_update(db, portfolio_id, current_user.id)
    plan = db.execute(
        select(PositionLifecyclePlan).where(
            PositionLifecyclePlan.user_id == current_user.id,
            PositionLifecyclePlan.position_group_id == item.position_group_id,
        )
    ).scalar_one_or_none()

    if plan is not None and plan.source != "user_backfilled":
        raise HTTPException(status_code=409, detail="已有原始進場計畫，不可改為事後補填")

    plan_values = {
        "thesis": payload.thesis,
        "setup_type": payload.setup_type,
        "planned_holding_period": payload.planned_holding_period,
        "default_stop_rule": payload.default_stop_rule,
        "add_entry_condition": payload.add_entry_condition,
        "planned_invalidation": payload.planned_invalidation,
        "planned_stop_price": Decimal(str(payload.planned_stop_price)) if payload.planned_stop_price is not None else None,
        "planned_target_or_scale_out_rule": payload.planned_target_or_scale_out_rule,
        "planned_risk_amount": Decimal(str(payload.planned_risk_amount)) if payload.planned_risk_amount is not None else None,
        "planned_risk_pct": Decimal(str(payload.planned_risk_pct)) if payload.planned_risk_pct is not None else None,
        "position_sizing_rationale": payload.position_sizing_rationale,
    }

    if plan is None:
        plan = PositionLifecyclePlan(
            user_id=item.user_id,
            position_group_id=item.position_group_id,
            symbol=item.symbol,
            source_portfolio_id=item.id,
            source="user_backfilled",
            created_after_entry=True,
            **plan_values,
        )
        db.add(plan)
    else:
        for key, value in plan_values.items():
            setattr(plan, key, value)
        plan.source_portfolio_id = item.id
        plan.source = "user_backfilled"
        plan.created_after_entry = True
        plan.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(plan)
    return _serialize_lifecycle_plan(item, plan)


@router.get("/groups/{position_group_id}/lifecycle-review")
def get_position_lifecycle_review(
    position_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_position_group(db, position_group_id, current_user.id)
    review = _get_position_lifecycle_review(db, position_group_id, current_user.id)
    if not review:
        raise HTTPException(status_code=404, detail="尚未建立持股生命週期審核")
    return _serialize_position_lifecycle_review(review)


@router.post("/groups/{position_group_id}/lifecycle-review")
def create_position_lifecycle_review(
    position_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = _get_owned_position_group(db, position_group_id, current_user.id)
    existing_review = _get_position_lifecycle_review(db, position_group_id, current_user.id)
    source_updated_at = _get_position_lifecycle_source_updated_at(db, position_group_id, current_user.id)
    if existing_review and _position_lifecycle_review_is_fresh(existing_review, source_updated_at):
        return _serialize_position_lifecycle_review(existing_review)

    try:
        review_result, evidence_payload = build_position_lifecycle_analysis(
            db,
            user_id=current_user.id,
            position_group_id=position_group_id,
        )
        if existing_review:
            review = existing_review
            review.symbol = group.symbol
            review.review_result = review_result
            review.evidence_payload = evidence_payload
            review.llm_summary = None
            review.updated_at = datetime.now(timezone.utc)
        else:
            review = PositionLifecycleReview(
                user_id=current_user.id,
                position_group_id=position_group_id,
                symbol=group.symbol,
                review_version=POSITION_LIFECYCLE_REVIEW_VERSION,
                review_result=review_result,
                evidence_payload=evidence_payload,
                llm_summary=None,
            )
            db.add(review)
        db.commit()
        db.refresh(review)
    except Exception:
        db.rollback()
        raise
    return _serialize_position_lifecycle_review(review)


@router.get("/{portfolio_id}/review")
def get_trade_review(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_reviewable_portfolio(db, portfolio_id, current_user.id)
    review = db.execute(
        select(TradeReview).where(
            TradeReview.portfolio_id == portfolio_id,
            TradeReview.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="尚未建立交易審核")
    return _serialize_trade_review(review)


@router.post("/{portfolio_id}/review")
def create_trade_review(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = _get_reviewable_portfolio(db, portfolio_id, current_user.id)
    existing_review = db.execute(
        select(TradeReview).where(
            TradeReview.portfolio_id == portfolio_id,
            TradeReview.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if existing_review:
        return _serialize_trade_review(existing_review)

    ensure_trade_review_market_data(db, item)
    review_result, evidence_payload = build_trade_review_payload(db, item)
    review = TradeReview(
        portfolio_id=item.id,
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        review_version=TRADE_REVIEW_VERSION,
        review_result=review_result,
        evidence_payload=evidence_payload,
        llm_summary=None,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return _serialize_trade_review(review)


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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"最多只能追蹤 {PORTFOLIO_LIMIT} 筆持股",
        )

    if not check_symbol_exists(payload.symbol):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"查詢目標不存在：{payload.symbol}",
        )

    entry = UserPortfolio(
        user_id=current_user.id,
        position_group_id=str(uuid.uuid4()),
        symbol=payload.symbol,
        entry_price=payload.entry_price,
        entry_date=payload.entry_date,
        quantity=payload.quantity,
        notes=payload.notes,
    )
    db.add(entry)
    db.flush()
    _add_position_event(
        db,
        item=entry,
        event_type="initial_entry",
        event_date=entry.entry_date,
        price=Decimal(str(entry.entry_price)),
        quantity=entry.quantity,
        source_portfolio_id=entry.id,
        note=payload.entry_record.note if payload.entry_record and payload.entry_record.note is not None else None,
        reason_category=_entry_reason_category(payload.entry_record.entry_reason) if payload.entry_record else None,
        reason_code=_entry_reason_code(payload.entry_record.entry_reason) if payload.entry_record else None,
    )
    if payload.entry_record and _entry_record_has_lifecycle_plan(payload.entry_record):
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
    return _serialize_portfolio(entry)


class UpdatePortfolioRequest(BaseModel):
    entry_price: float = Field(gt=0)
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
    return _serialize_portfolio(item)


@router.post("/{portfolio_id}/add-entry", status_code=status.HTTP_201_CREATED)
def add_entry_to_portfolio(
    portfolio_id: int,
    payload: AddEntryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = _get_owned_active_portfolio_for_update(db, portfolio_id, current_user.id)
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

    event = _add_position_event(
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
        reason_category=_add_entry_reason_category(payload.reason_code),
        reason_code=_add_entry_reason_code(payload.reason_code),
        source="user_recorded_at_event_time",
    )
    event.plan_adherence = payload.plan_adherence
    event.confidence_level = payload.confidence_level
    db.commit()
    db.refresh(item)
    db.refresh(event)
    return {
        "portfolio": _serialize_portfolio(item),
        "event": _serialize_position_event(event),
    }


@router.post("/{portfolio_id}/close")
def close_portfolio(
    portfolio_id: int,
    payload: ClosePortfolioRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.execute(
        select(UserPortfolio)
        .where(
            UserPortfolio.id == portfolio_id,
            UserPortfolio.user_id == current_user.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=403, detail="無權限")

    if not item.is_active:
        raise HTTPException(status_code=409, detail="持倉已關閉")

    if payload.exit_quantity > item.quantity:
        raise HTTPException(status_code=422, detail="出場股數不可大於持有股數")

    if payload.exit_date < item.entry_date:
        raise HTTPException(status_code=422, detail="出場日期不可早於進場日期")

    exit_price = Decimal(str(payload.exit_price))
    entry_price = Decimal(str(item.entry_price))
    if entry_price <= 0:
        raise HTTPException(status_code=422, detail="成本價必須大於 0")
    exit_quantity = Decimal(payload.exit_quantity)
    gross_exit_amount = exit_price * exit_quantity
    explicit_fee = Decimal(str(payload.fees)) if payload.fees is not None else None
    explicit_tax = Decimal(str(payload.taxes)) if payload.taxes is not None else None
    row_fees = calculate_broker_fee(
        gross_exit_amount,
        actual_fee=explicit_fee,
    )
    row_taxes = calculate_sell_transaction_tax(
        gross_exit_amount,
        explicit_tax=explicit_tax,
    )
    event_fees = row_fees
    event_taxes = row_taxes
    realized_pnl = (exit_price - entry_price) * exit_quantity - row_fees - row_taxes
    realized_return_pct = realized_pnl / (entry_price * exit_quantity) * Decimal("100")
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
        _add_position_event(
            db,
            item=item,
            event_type="full_exit",
            event_date=payload.exit_date,
            price=exit_price,
            quantity=payload.exit_quantity,
            fees=event_fees,
            taxes=event_taxes,
            source_portfolio_id=item.id,
        )
        db.commit()
        db.refresh(item)
        return _serialize_portfolio(item)

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
    _add_position_event(
        db,
        item=closed_item,
        event_type="partial_exit",
        event_date=payload.exit_date,
        price=exit_price,
        quantity=payload.exit_quantity,
        fees=event_fees,
        taxes=event_taxes,
        source_portfolio_id=closed_item.id,
    )
    db.commit()
    db.refresh(closed_item)
    return _serialize_portfolio(closed_item)


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
