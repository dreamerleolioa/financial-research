from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import UserWatchlist


def select_user_watchlist_items(db: Session, user_id: int) -> list[UserWatchlist]:
    return db.execute(
        select(UserWatchlist)
        .where(UserWatchlist.user_id == user_id)
        .order_by(UserWatchlist.sort_order.asc(), UserWatchlist.created_at.desc(), UserWatchlist.id.desc())
    ).scalars().all()


def get_user_watchlist_item(db: Session, user_id: int, item_id: int) -> UserWatchlist | None:
    return db.execute(
        select(UserWatchlist).where(
            UserWatchlist.id == item_id,
            UserWatchlist.user_id == user_id,
        )
    ).scalar_one_or_none()


def get_user_watchlist_item_by_symbol(db: Session, user_id: int, symbol: str) -> UserWatchlist | None:
    return db.execute(
        select(UserWatchlist).where(
            UserWatchlist.user_id == user_id,
            UserWatchlist.symbol == symbol,
        )
    ).scalar_one_or_none()


def next_sort_order(db: Session, user_id: int) -> int:
    max_sort_order = db.execute(
        select(func.max(UserWatchlist.sort_order)).where(UserWatchlist.user_id == user_id)
    ).scalar_one()
    return (max_sort_order if max_sort_order is not None else -1) + 1
