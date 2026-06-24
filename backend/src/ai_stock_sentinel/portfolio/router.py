# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
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
    TradeReview,
    UserPortfolio,
)
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.portfolio.application.add_entry import add_entry_to_position
from ai_stock_sentinel.portfolio.application.add_position import create_portfolio
from ai_stock_sentinel.portfolio.application.close_position import close_position as close_position_use_case
from ai_stock_sentinel.portfolio.application.get_risk_summary import build_user_portfolio_risk_summary
from ai_stock_sentinel.portfolio.application.update_position import update_portfolio_record
from ai_stock_sentinel.portfolio.repository import list_active_portfolios, list_closed_portfolios
from ai_stock_sentinel.portfolio.schemas import (
    AddEntryRequest,
    BackfillLifecyclePlanRequest,
    ClosePortfolioRequest,
    PortfolioCreateRequest,
    UpdatePortfolioRequest,
)
from ai_stock_sentinel.shared_context import (
    SHARED_CONTEXT_CONSUMER_PORTFOLIO,
    read_shared_context_for_symbol,
)
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

TRADE_REVIEW_VERSION = "trade-review-v1"
POSITION_LIFECYCLE_REVIEW_VERSION = "position-lifecycle-review-v1"


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


def _lifecycle_plan_values(payload: BackfillLifecyclePlanRequest) -> dict:
    return {
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
    rows = list_active_portfolios(db, user_id=current_user.id)

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
    rows = list_closed_portfolios(db, user_id=current_user.id)
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
    return build_user_portfolio_risk_summary(
        db,
        user_id=current_user.id,
        symbol_name_resolver=resolve_symbol_name,
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


@router.put("/{portfolio_id}/lifecycle-plan")
def update_portfolio_lifecycle_plan(
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

    plan_values = _lifecycle_plan_values(payload)

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
        plan.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(plan)
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

    plan_values = _lifecycle_plan_values(payload)

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
    entry = create_portfolio(
        db,
        user_id=current_user.id,
        payload=payload,
        symbol_exists_checker=check_symbol_exists,
    )
    return _serialize_portfolio(entry)


@router.put("/{portfolio_id}")
def update_portfolio(
    portfolio_id: int,
    payload: UpdatePortfolioRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = update_portfolio_record(
        db,
        portfolio_id=portfolio_id,
        user_id=current_user.id,
        payload=payload,
    )
    return _serialize_portfolio(item)


@router.post("/{portfolio_id}/add-entry", status_code=status.HTTP_201_CREATED)
def add_entry_to_portfolio(
    portfolio_id: int,
    payload: AddEntryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item, event = add_entry_to_position(
        db,
        portfolio_id=portfolio_id,
        user_id=current_user.id,
        payload=payload,
    )
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
    item = close_position_use_case(
        db,
        portfolio_id=portfolio_id,
        user_id=current_user.id,
        payload=payload,
    )
    return _serialize_portfolio(item)


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
