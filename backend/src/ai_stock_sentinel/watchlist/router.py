from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.data_sources.yfinance_client import check_symbol_exists
from ai_stock_sentinel.db.models import UserWatchlist
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.user_models.user import User
from ai_stock_sentinel.watchlist.application.items import (
    WatchlistApplicationError,
    create_watchlist_item as create_watchlist_item_use_case,
    delete_watchlist_item as delete_watchlist_item_use_case,
    list_watchlist_items,
    reorder_watchlist_items as reorder_watchlist_items_use_case,
    update_watchlist_item as update_watchlist_item_use_case,
)
from ai_stock_sentinel.watchlist.schemas import (
    WatchlistCreateRequest,
    WatchlistReorderRequest,
    WatchlistUpdateRequest,
)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _serialize_watchlist_item(item: UserWatchlist) -> dict:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "name": resolve_symbol_name(item.symbol),
        "notes": item.notes,
        "sort_order": item.sort_order,
        "created_at": item.created_at.isoformat() if item.created_at and hasattr(item.created_at, "isoformat") else item.created_at,
        "updated_at": item.updated_at.isoformat() if item.updated_at and hasattr(item.updated_at, "isoformat") else item.updated_at,
    }


def _raise_http(exc: WatchlistApplicationError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("")
def list_watchlist(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = list_watchlist_items(db, current_user.id)
    return [_serialize_watchlist_item(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_watchlist_item(
    payload: WatchlistCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = create_watchlist_item_use_case(
            db,
            current_user.id,
            payload,
            symbol_exists=check_symbol_exists,
        )
    except WatchlistApplicationError as exc:
        _raise_http(exc)

    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _serialize_watchlist_item(result.item)


@router.put("/reorder")
def reorder_watchlist_items(
    payload: WatchlistReorderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        rows = reorder_watchlist_items_use_case(db, current_user.id, payload)
    except WatchlistApplicationError as exc:
        _raise_http(exc)
    return [_serialize_watchlist_item(row) for row in rows]


@router.put("/{item_id}")
def update_watchlist_item(
    item_id: int,
    payload: WatchlistUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        item = update_watchlist_item_use_case(db, current_user.id, item_id, payload)
    except WatchlistApplicationError as exc:
        _raise_http(exc)
    return _serialize_watchlist_item(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        delete_watchlist_item_use_case(db, current_user.id, item_id)
    except WatchlistApplicationError as exc:
        _raise_http(exc)
