# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.position_lifecycle import build_position_lifecycle_analysis
from ai_stock_sentinel.analysis.trade_review import build_trade_review_payload, ensure_trade_review_market_data
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.data_sources.yfinance_client import check_symbol_exists
from ai_stock_sentinel.db.models import PositionEvent, PositionLifecyclePlan, PositionLifecycleReview, TradeReview, UserPortfolio
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.portfolio.fees import calculate_broker_fee, calculate_sell_transaction_tax
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

PORTFOLIO_LIMIT = 8
TRADE_REVIEW_VERSION = "trade-review-v1"
POSITION_LIFECYCLE_REVIEW_VERSION = "position-lifecycle-review-v1"


class PortfolioCreateRequest(BaseModel):
    symbol: str
    entry_price: float = Field(gt=0)
    entry_date: date
    quantity: int = 0
    notes: str | None = None


class ClosePortfolioRequest(BaseModel):
    exit_date: date
    exit_price: float = Field(gt=0, allow_inf_nan=False)
    exit_quantity: int = Field(gt=0)
    fees: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    taxes: float | None = Field(default=None, ge=0, allow_inf_nan=False)


def _serialize_portfolio(item: UserPortfolio) -> dict:
    return {
        "id": item.id,
        "position_group_id": item.position_group_id,
        "symbol": item.symbol,
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


def _serialize_decision_context_status(item: UserPortfolio, plan: PositionLifecyclePlan | None) -> dict:
    return {
        "portfolio_id": item.id,
        "position_group_id": item.position_group_id,
        "symbol": item.symbol,
        "has_operation_plan": plan is not None,
        "operation_plan_status": "present" if plan is not None else "missing",
        "missing_operation_plan": plan is None,
        "decision_context": "present" if plan is not None else "insufficient",
        "source": plan.source if plan is not None else None,
        "created_after_entry": plan.created_after_entry if plan is not None else None,
        "planned_invalidation_present": bool(plan and plan.planned_invalidation),
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
        note=item.notes,
        source=source,
        data_quality_note=data_quality_note,
    )
    db.add(event)
    return event


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

    return {
        str(row.id): _serialize_decision_context_status(row, plan_by_group.get(row.position_group_id))
        for row in rows
    }


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
    if existing_review:
        return _serialize_position_lifecycle_review(existing_review)

    try:
        review_result, evidence_payload = build_position_lifecycle_analysis(
            db,
            user_id=current_user.id,
            position_group_id=position_group_id,
        )
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
    )
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "symbol": entry.symbol}


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
    return {
        "id":          item.id,
        "symbol":      item.symbol,
        "entry_price": float(item.entry_price),
        "quantity":    item.quantity,
        "entry_date":  item.entry_date.isoformat() if hasattr(item.entry_date, "isoformat") else item.entry_date,
        "notes":       item.notes,
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
    row_fees = Decimal(str(payload.fees)) if payload.fees is not None else Decimal("0")
    row_taxes = Decimal(str(payload.taxes)) if payload.taxes is not None else Decimal("0")
    gross_exit_amount = exit_price * exit_quantity
    event_fees = calculate_broker_fee(
        gross_exit_amount,
        actual_fee=Decimal(str(payload.fees)) if payload.fees is not None else None,
    )
    event_taxes = calculate_sell_transaction_tax(
        gross_exit_amount,
        explicit_tax=Decimal(str(payload.taxes)) if payload.taxes is not None else None,
    )
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
