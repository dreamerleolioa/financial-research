# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.position_lifecycle import build_position_lifecycle_analysis
from ai_stock_sentinel.analysis.review_sources import attach_source_fingerprint, market_snapshot_regressed
from ai_stock_sentinel.analysis.trade_review import (
    TRADE_REVIEW_PROVIDER_UPGRADE_MIN_COVERAGE_RATIO,
    TradeReviewMarketTarget,
    build_trade_review_payload,
    ensure_trade_review_market_data,
    trade_review_source_payload,
)
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler, check_symbol_exists
from ai_stock_sentinel.db.models import (
    PositionEvent,
    PositionLifecyclePlan,
    PositionLifecycleReview,
    TradeReview,
    UserPortfolio,
)
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.portfolio.application.add_entry import add_entry_to_position
from ai_stock_sentinel.portfolio.application.events import ledger_open_quantity
from ai_stock_sentinel.portfolio.application.add_position import create_portfolio
from ai_stock_sentinel.portfolio.application.close_position import close_position as close_position_use_case
from ai_stock_sentinel.portfolio.application.get_risk_summary import build_user_portfolio_risk_summary
from ai_stock_sentinel.portfolio.application.refresh_prices import (
    PortfolioPriceRefreshTargetNotFound,
    refresh_user_portfolio_prices,
)
from ai_stock_sentinel.portfolio.application.update_position import update_portfolio_record
from ai_stock_sentinel.portfolio.repository import list_active_portfolios, list_closed_portfolios
from ai_stock_sentinel.portfolio.schemas import (
    AddEntryRequest,
    BackfillLifecyclePlanRequest,
    ClosePortfolioRequest,
    PortfolioCreateRequest,
    PortfolioPriceRefreshRequest,
    UpdatePortfolioRequest,
)
from ai_stock_sentinel.shared_context import (
    SHARED_CONTEXT_CONSUMER_PORTFOLIO,
    read_shared_context_for_symbol,
)
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

TRADE_REVIEW_VERSION = "trade-review-v3"
KNOWN_TRADE_REVIEW_VERSIONS = {"trade-review-v1", "trade-review-v2", TRADE_REVIEW_VERSION}
POSITION_LIFECYCLE_REVIEW_VERSION = "position-lifecycle-review-v2"
KNOWN_POSITION_LIFECYCLE_REVIEW_VERSIONS = {
    "position-lifecycle-review-v1",
    POSITION_LIFECYCLE_REVIEW_VERSION,
}
TRADE_REVIEW_SUCCESS_REFRESH_TTL = timedelta(hours=6)
TRADE_REVIEW_FAILURE_RETRY_TTL = timedelta(minutes=5)
TRADE_REVIEW_PARTIAL_COVERAGE_RETRY_TTL = timedelta(hours=24)


class _TradeReviewRefreshSlot:
    def __init__(self) -> None:
        self.lock = Lock()
        self.users = 0


_TRADE_REVIEW_REFRESH_SLOTS: dict[tuple[int, int], _TradeReviewRefreshSlot] = {}
_TRADE_REVIEW_REFRESH_SLOTS_GUARD = Lock()


@contextmanager
def _trade_review_refresh_singleflight(key: tuple[int, int]) -> Iterator[bool]:
    """Reserve one in-process refresh slot without blocking a sync worker."""
    with _TRADE_REVIEW_REFRESH_SLOTS_GUARD:
        slot = _TRADE_REVIEW_REFRESH_SLOTS.setdefault(key, _TradeReviewRefreshSlot())
        slot.users += 1
    acquired = slot.lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            slot.lock.release()
        with _TRADE_REVIEW_REFRESH_SLOTS_GUARD:
            slot.users -= 1
            if slot.users == 0 and _TRADE_REVIEW_REFRESH_SLOTS.get(key) is slot:
                del _TRADE_REVIEW_REFRESH_SLOTS[key]


def get_portfolio_quote_fetcher():
    return YFinanceCrawler().fetch_portfolio_snapshot


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


def _get_reviewable_portfolio(
    db: Session,
    portfolio_id: int,
    user_id: int,
    *,
    lock: bool = False,
) -> UserPortfolio:
    statement = select(UserPortfolio).where(
        UserPortfolio.id == portfolio_id,
        UserPortfolio.user_id == user_id,
    )
    if lock:
        statement = statement.with_for_update()
    item = db.execute(statement).scalar_one_or_none()
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


def _get_owned_closed_position_group(
    db: Session,
    position_group_id: str,
    user_id: int,
    *,
    lock: bool = False,
) -> UserPortfolio:
    statement = select(UserPortfolio).where(
        UserPortfolio.user_id == user_id,
        UserPortfolio.position_group_id == position_group_id,
    ).order_by(UserPortfolio.id.asc())
    if lock:
        statement = statement.with_for_update()
    rows = db.execute(statement).scalars().all()
    if not rows:
        raise HTTPException(status_code=403, detail="無權限")
    events = db.execute(
        select(PositionEvent)
        .where(
            PositionEvent.user_id == user_id,
            PositionEvent.position_group_id == position_group_id,
        )
        .order_by(PositionEvent.event_date.asc(), PositionEvent.created_at.asc(), PositionEvent.id.asc())
    ).scalars().all()
    if (
        any(row.is_active for row in rows)
        or not any(event.event_type == "full_exit" for event in events)
        or ledger_open_quantity(events) != 0
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "position_lifecycle_not_closed",
                "message": "持股生命週期尚未完整結案，不能建立或讀取結案回顧。",
            },
        )
    return rows[0]


def _get_position_lifecycle_review(db: Session, position_group_id: str, user_id: int) -> PositionLifecycleReview | None:
    return db.execute(
        select(PositionLifecycleReview).where(
            PositionLifecycleReview.user_id == user_id,
            PositionLifecycleReview.position_group_id == position_group_id,
            PositionLifecycleReview.review_version == POSITION_LIFECYCLE_REVIEW_VERSION,
        )
    ).scalar_one_or_none()


def _get_latest_saved_position_lifecycle_review(
    db: Session,
    position_group_id: str,
    user_id: int,
) -> PositionLifecycleReview | None:
    reviews = db.execute(
        select(PositionLifecycleReview)
        .where(
            PositionLifecycleReview.user_id == user_id,
            PositionLifecycleReview.position_group_id == position_group_id,
        )
        .order_by(PositionLifecycleReview.created_at.desc(), PositionLifecycleReview.id.desc())
    ).scalars().all()
    unknown = next(
        (review for review in reviews if review.review_version not in KNOWN_POSITION_LIFECYCLE_REVIEW_VERSIONS),
        None,
    )
    if unknown is not None:
        return unknown
    current = next((review for review in reviews if review.review_version == POSITION_LIFECYCLE_REVIEW_VERSION), None)
    return current or (reviews[0] if reviews else None)


def _get_unknown_position_lifecycle_review(
    db: Session,
    position_group_id: str,
    user_id: int,
) -> PositionLifecycleReview | None:
    return next(
        (
            review
            for review in db.execute(
                select(PositionLifecycleReview)
                .where(
                    PositionLifecycleReview.user_id == user_id,
                    PositionLifecycleReview.position_group_id == position_group_id,
                )
                .order_by(PositionLifecycleReview.created_at.desc(), PositionLifecycleReview.id.desc())
            ).scalars()
            if review.review_version not in KNOWN_POSITION_LIFECYCLE_REVIEW_VERSIONS
        ),
        None,
    )


def _market_snapshot_regressed(existing_market: object, new_market: object) -> bool:
    return market_snapshot_regressed(
        existing_market,
        new_market,
        provider_upgrade_min_coverage_ratio=TRADE_REVIEW_PROVIDER_UPGRADE_MIN_COVERAGE_RATIO,
    )


def _trade_review_snapshot_regressed(existing_review: TradeReview, evidence_payload: dict) -> bool:
    existing_evidence = existing_review.evidence_payload if isinstance(existing_review.evidence_payload, dict) else {}
    if existing_evidence.get("trade") != evidence_payload.get("trade"):
        return False
    return _market_snapshot_regressed(
        existing_evidence.get("market_snapshot"),
        evidence_payload.get("market_snapshot"),
    )


def _lifecycle_review_snapshot_regressed(
    existing_review: PositionLifecycleReview,
    evidence_payload: dict,
) -> bool:
    existing_evidence = existing_review.evidence_payload if isinstance(existing_review.evidence_payload, dict) else {}
    for key in ("events", "plan_snapshot", "shared_context"):
        if existing_evidence.get(key) != evidence_payload.get(key):
            return False
    return _market_snapshot_regressed(
        existing_evidence.get("market_snapshot"),
        evidence_payload.get("market_snapshot"),
    )


def _parse_utc_datetime(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def _trade_review_cache_reusable(
    review: TradeReview,
    item: UserPortfolio,
    *,
    now: datetime | None = None,
) -> bool:
    if review.review_version != TRADE_REVIEW_VERSION:
        return False
    evidence = review.evidence_payload if isinstance(review.evidence_payload, dict) else {}
    if evidence.get("trade") != trade_review_source_payload(item):
        return False
    market = evidence.get("market_snapshot")
    if not isinstance(market, dict):
        return False
    fetched_at = _parse_utc_datetime(market.get("fetched_at"))
    if fetched_at is None:
        return False
    quality = market.get("quality") if isinstance(market.get("quality"), dict) else {}
    missing_reason = quality.get("missing_reason")
    if missing_reason == "provider_coverage_insufficient":
        ttl = TRADE_REVIEW_PARTIAL_COVERAGE_RETRY_TTL
    elif missing_reason:
        ttl = TRADE_REVIEW_FAILURE_RETRY_TTL
    else:
        ttl = TRADE_REVIEW_SUCCESS_REFRESH_TTL
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = current_time - fetched_at
    return timedelta(0) <= age < ttl


def _trade_review_refresh_superseded(
    existing_review: TradeReview,
    item: UserPortfolio,
    market_snapshot: object,
) -> bool:
    """Arbitrate a snapshot fetched before the row lock was acquired."""
    if not _trade_review_cache_reusable(existing_review, item):
        return False
    candidate_market = getattr(market_snapshot, "evidence", None)
    if not isinstance(candidate_market, dict):
        return True
    existing_evidence = existing_review.evidence_payload if isinstance(existing_review.evidence_payload, dict) else {}
    existing_market = existing_evidence.get("market_snapshot")
    if _market_snapshot_regressed(existing_market, candidate_market):
        return True
    if _market_snapshot_regressed(candidate_market, existing_market):
        return False
    existing_fetched_at = _parse_utc_datetime(
        existing_market.get("fetched_at") if isinstance(existing_market, dict) else None
    )
    candidate_fetched_at = _parse_utc_datetime(candidate_market.get("fetched_at"))
    if existing_fetched_at is None:
        return False
    return candidate_fetched_at is None or existing_fetched_at >= candidate_fetched_at


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


@router.post("/risk-summary/refresh-prices")
def refresh_portfolio_prices(
    payload: PortfolioPriceRefreshRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    quote_fetcher=Depends(get_portfolio_quote_fetcher),
):
    try:
        return refresh_user_portfolio_prices(
            db,
            user_id=current_user.id,
            portfolio_ids=payload.portfolio_ids,
            quote_fetcher=quote_fetcher,
            symbol_name_resolver=resolve_symbol_name,
        )
    except PortfolioPriceRefreshTargetNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="持股不存在或已結案",
        ) from exc


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
        plan_changed = any(getattr(plan, key) != value for key, value in plan_values.items())
        for key, value in plan_values.items():
            setattr(plan, key, value)
        plan.source_portfolio_id = item.id
        if plan_changed:
            plan.source = "user_backfilled"
            plan.created_after_entry = True
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
    _get_owned_closed_position_group(db, position_group_id, current_user.id)
    review = _get_latest_saved_position_lifecycle_review(db, position_group_id, current_user.id)
    if not review:
        raise HTTPException(status_code=404, detail="尚未建立持股生命週期審核")
    return _serialize_position_lifecycle_review(review)


@router.post("/groups/{position_group_id}/lifecycle-review")
def create_position_lifecycle_review(
    position_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = _get_owned_closed_position_group(db, position_group_id, current_user.id, lock=True)
    unknown_review = _get_unknown_position_lifecycle_review(db, position_group_id, current_user.id)
    if unknown_review is not None:
        return _serialize_position_lifecycle_review(unknown_review)
    existing_review = _get_position_lifecycle_review(db, position_group_id, current_user.id)

    try:
        review_result, evidence_payload = build_position_lifecycle_analysis(
            db,
            user_id=current_user.id,
            position_group_id=position_group_id,
        )
        if existing_review and _lifecycle_review_snapshot_regressed(existing_review, evidence_payload):
            return _serialize_position_lifecycle_review(existing_review)
        source_fingerprint = attach_source_fingerprint(
            evidence_payload,
            ruleset_version=POSITION_LIFECYCLE_REVIEW_VERSION,
        )
        if (
            existing_review
            and isinstance(existing_review.evidence_payload, dict)
            and existing_review.evidence_payload.get("source_fingerprint") == source_fingerprint
        ):
            return _serialize_position_lifecycle_review(existing_review)
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
    user_id = current_user.id
    item = _get_reviewable_portfolio(db, portfolio_id, user_id)
    existing_review = db.execute(
        select(TradeReview).where(
            TradeReview.portfolio_id == portfolio_id,
            TradeReview.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing_review and existing_review.review_version not in KNOWN_TRADE_REVIEW_VERSIONS:
        return _serialize_trade_review(existing_review)
    if existing_review and _trade_review_cache_reusable(existing_review, item):
        return _serialize_trade_review(existing_review)

    db.rollback()
    with _trade_review_refresh_singleflight((user_id, portfolio_id)) as refresh_acquired:
        if not refresh_acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="交易審核正在更新，請稍後重試",
                headers={"Retry-After": "1"},
            )
        # Re-read after reserving the in-process slot and before provider I/O.
        item = _get_reviewable_portfolio(db, portfolio_id, user_id)
        existing_review = db.execute(
            select(TradeReview).where(
                TradeReview.portfolio_id == portfolio_id,
                TradeReview.user_id == user_id,
            )
        ).scalar_one_or_none()
        if existing_review and existing_review.review_version not in KNOWN_TRADE_REVIEW_VERSIONS:
            return _serialize_trade_review(existing_review)
        if existing_review and _trade_review_cache_reusable(existing_review, item):
            return _serialize_trade_review(existing_review)

        market_target = TradeReviewMarketTarget(
            symbol=item.symbol,
            entry_date=item.entry_date,
            exit_date=item.exit_date,
        )
        db.rollback()
        market_snapshot = ensure_trade_review_market_data(db, market_target)

        item = _get_reviewable_portfolio(db, portfolio_id, user_id, lock=True)
        existing_review = db.execute(
            select(TradeReview).where(
                TradeReview.portfolio_id == portfolio_id,
                TradeReview.user_id == user_id,
            )
        ).scalar_one_or_none()
        if existing_review and existing_review.review_version not in KNOWN_TRADE_REVIEW_VERSIONS:
            return _serialize_trade_review(existing_review)
        # A different process may have committed a fresher snapshot while this
        # request was fetching. Row locks serialize writes, while this second
        # freshness check prevents an older observation from winning afterward.
        if existing_review and _trade_review_refresh_superseded(existing_review, item, market_snapshot):
            return _serialize_trade_review(existing_review)

        review_result, evidence_payload = build_trade_review_payload(db, item, market_snapshot=market_snapshot)
        if (
            existing_review
            and existing_review.review_version == TRADE_REVIEW_VERSION
            and _trade_review_snapshot_regressed(existing_review, evidence_payload)
        ):
            return _serialize_trade_review(existing_review)
        source_fingerprint = attach_source_fingerprint(
            evidence_payload,
            ruleset_version=TRADE_REVIEW_VERSION,
        )
        if (
            existing_review
            and existing_review.review_version == TRADE_REVIEW_VERSION
            and isinstance(existing_review.evidence_payload, dict)
            and existing_review.evidence_payload.get("source_fingerprint") == source_fingerprint
        ):
            existing_market = existing_review.evidence_payload.get("market_snapshot")
            candidate_market = evidence_payload.get("market_snapshot")
            existing_fetched_at = _parse_utc_datetime(
                existing_market.get("fetched_at") if isinstance(existing_market, dict) else None
            )
            candidate_fetched_at = _parse_utc_datetime(
                candidate_market.get("fetched_at") if isinstance(candidate_market, dict) else None
            )
            if candidate_fetched_at is not None and (
                existing_fetched_at is None or candidate_fetched_at > existing_fetched_at
            ):
                existing_review.evidence_payload = evidence_payload
                existing_review.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(existing_review)
            return _serialize_trade_review(existing_review)
        if existing_review:
            review = existing_review
            review.review_version = TRADE_REVIEW_VERSION
            review.review_result = review_result
            review.evidence_payload = evidence_payload
            review.llm_summary = None
            review.updated_at = datetime.now(timezone.utc)
        else:
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
