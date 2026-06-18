from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import UserWatchlist
from ai_stock_sentinel.watchlist.repository import (
    get_user_watchlist_item,
    get_user_watchlist_item_by_symbol,
    next_sort_order,
    select_user_watchlist_items,
)
from ai_stock_sentinel.watchlist.schemas import (
    WatchlistCreateRequest,
    WatchlistReorderRequest,
    WatchlistUpdateRequest,
)


@dataclass(frozen=True)
class WatchlistApplicationError(Exception):
    status_code: int
    detail: str


@dataclass(frozen=True)
class CreateWatchlistItemResult:
    item: UserWatchlist
    created: bool


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def normalize_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    normalized = notes.strip()
    return normalized or None


def list_watchlist_items(db: Session, user_id: int) -> list[UserWatchlist]:
    return select_user_watchlist_items(db, user_id)


def create_watchlist_item(
    db: Session,
    user_id: int,
    payload: WatchlistCreateRequest,
    *,
    symbol_exists: Callable[[str], bool],
) -> CreateWatchlistItemResult:
    symbol = normalize_symbol(payload.symbol)
    if not symbol:
        raise WatchlistApplicationError(status_code=422, detail="股票代碼不可為空")
    if not symbol_exists(symbol):
        raise WatchlistApplicationError(status_code=404, detail=f"查詢目標不存在：{symbol}")

    notes_was_provided = "notes" in payload.model_fields_set
    notes = normalize_notes(payload.notes)
    existing = get_user_watchlist_item_by_symbol(db, user_id, symbol)
    if existing is not None:
        if notes_was_provided and notes != existing.notes:
            existing.notes = notes
            existing.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
        return CreateWatchlistItemResult(item=existing, created=False)

    item = UserWatchlist(
        user_id=user_id,
        symbol=symbol,
        notes=notes,
        sort_order=next_sort_order(db, user_id),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = get_user_watchlist_item_by_symbol(db, user_id, symbol)
        if existing is None:
            raise
        return CreateWatchlistItemResult(item=existing, created=False)

    db.refresh(item)
    return CreateWatchlistItemResult(item=item, created=True)


def reorder_watchlist_items(
    db: Session,
    user_id: int,
    payload: WatchlistReorderRequest,
) -> list[UserWatchlist]:
    rows = select_user_watchlist_items(db, user_id)
    requested_ids = payload.item_ids
    requested_id_set = set(requested_ids)
    existing_id_set = {row.id for row in rows}

    if len(requested_ids) != len(requested_id_set):
        raise WatchlistApplicationError(status_code=400, detail="排序清單不可包含重複項目")
    if requested_id_set != existing_id_set:
        raise WatchlistApplicationError(status_code=400, detail="排序清單必須包含所有關注項目")

    now = datetime.now(timezone.utc)
    rows_by_id = {row.id: row for row in rows}
    ordered_rows: list[UserWatchlist] = []
    for sort_order, item_id in enumerate(requested_ids):
        row = rows_by_id[item_id]
        row.sort_order = sort_order
        row.updated_at = now
        ordered_rows.append(row)

    db.commit()
    for row in ordered_rows:
        db.refresh(row)
    return ordered_rows


def update_watchlist_item(
    db: Session,
    user_id: int,
    item_id: int,
    payload: WatchlistUpdateRequest,
) -> UserWatchlist:
    item = get_user_watchlist_item(db, user_id, item_id)
    if item is None:
        raise WatchlistApplicationError(status_code=404, detail="找不到關注項目")

    item.notes = normalize_notes(payload.notes)
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item


def delete_watchlist_item(db: Session, user_id: int, item_id: int) -> None:
    item = get_user_watchlist_item(db, user_id, item_id)
    if item is None:
        raise WatchlistApplicationError(status_code=404, detail="找不到關注項目")

    db.delete(item)
    db.commit()
