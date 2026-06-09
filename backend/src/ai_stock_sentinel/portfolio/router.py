# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.trade_review import build_trade_review_payload, ensure_trade_review_market_data
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.data_sources.yfinance_client import check_symbol_exists
from ai_stock_sentinel.db.models import TradeReview, UserPortfolio
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

PORTFOLIO_LIMIT = 8
TRADE_REVIEW_VERSION = "trade-review-v1"


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
    fees: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    taxes: float = Field(default=0.0, ge=0, allow_inf_nan=False)


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
    fees = Decimal(str(payload.fees))
    taxes = Decimal(str(payload.taxes))
    realized_pnl = (exit_price - entry_price) * exit_quantity - fees - taxes
    realized_return_pct = realized_pnl / (entry_price * exit_quantity) * Decimal("100")
    holding_days = (payload.exit_date - item.entry_date).days
    updated_at = datetime.now(timezone.utc)

    if payload.exit_quantity == item.quantity:
        item.is_active = False
        item.exit_date = payload.exit_date
        item.exit_price = exit_price
        item.exit_quantity = payload.exit_quantity
        item.exit_fees = fees
        item.exit_taxes = taxes
        item.realized_pnl = realized_pnl
        item.realized_return_pct = realized_return_pct
        item.holding_days = holding_days
        item.updated_at = updated_at
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
        exit_fees=fees,
        exit_taxes=taxes,
        realized_pnl=realized_pnl,
        realized_return_pct=realized_return_pct,
        holding_days=holding_days,
        notes=item.notes,
    )
    db.add(closed_item)
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
