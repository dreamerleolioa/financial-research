from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.data_sources.yfinance_client import check_symbol_exists
from ai_stock_sentinel.db.models import UserWatchlist
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistCreateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=500)


class WatchlistUpdateRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=500)


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _normalize_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    normalized = notes.strip()
    return normalized or None


def _serialize_watchlist_item(item: UserWatchlist) -> dict:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "name": resolve_symbol_name(item.symbol),
        "notes": item.notes,
        "created_at": item.created_at.isoformat() if item.created_at and hasattr(item.created_at, "isoformat") else item.created_at,
        "updated_at": item.updated_at.isoformat() if item.updated_at and hasattr(item.updated_at, "isoformat") else item.updated_at,
    }


@router.get("")
def list_watchlist(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(UserWatchlist)
        .where(UserWatchlist.user_id == current_user.id)
        .order_by(UserWatchlist.created_at.desc(), UserWatchlist.id.desc())
    ).scalars().all()
    return [_serialize_watchlist_item(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_watchlist_item(
    payload: WatchlistCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    symbol = _normalize_symbol(payload.symbol)
    if not symbol:
        raise HTTPException(status_code=422, detail="股票代碼不可為空")
    if not check_symbol_exists(symbol):
        raise HTTPException(status_code=404, detail=f"查詢目標不存在：{symbol}")

    existing = db.execute(
        select(UserWatchlist).where(
            UserWatchlist.user_id == current_user.id,
            UserWatchlist.symbol == symbol,
        )
    ).scalar_one_or_none()
    notes_was_provided = "notes" in payload.model_fields_set
    notes = _normalize_notes(payload.notes)
    if existing is not None:
        if notes_was_provided and notes != existing.notes:
            existing.notes = notes
            existing.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
        response.status_code = status.HTTP_200_OK
        return _serialize_watchlist_item(existing)

    item = UserWatchlist(
        user_id=current_user.id,
        symbol=symbol,
        notes=notes,
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(UserWatchlist).where(
                UserWatchlist.user_id == current_user.id,
                UserWatchlist.symbol == symbol,
            )
        ).scalar_one()
        response.status_code = status.HTTP_200_OK
        return _serialize_watchlist_item(existing)

    db.refresh(item)
    return _serialize_watchlist_item(item)


@router.put("/{item_id}")
def update_watchlist_item(
    item_id: int,
    payload: WatchlistUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.execute(
        select(UserWatchlist).where(
            UserWatchlist.id == item_id,
            UserWatchlist.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="找不到關注項目")

    item.notes = _normalize_notes(payload.notes)
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return _serialize_watchlist_item(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    item = db.execute(
        select(UserWatchlist).where(
            UserWatchlist.id == item_id,
            UserWatchlist.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="找不到關注項目")

    db.delete(item)
    db.commit()
