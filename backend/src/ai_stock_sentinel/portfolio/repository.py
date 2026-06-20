from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import PositionLifecyclePlan, StockRawData, UserPortfolio


def list_active_portfolios(db: Session, *, user_id: int) -> list[UserPortfolio]:
    return db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == user_id,
            UserPortfolio.is_active == True,
        ).order_by(UserPortfolio.created_at.desc())
    ).scalars().all()


def list_closed_portfolios(db: Session, *, user_id: int) -> list[UserPortfolio]:
    return db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == user_id,
            UserPortfolio.is_active == False,
            UserPortfolio.exit_date.is_not(None),
        ).order_by(UserPortfolio.exit_date.desc(), UserPortfolio.updated_at.desc())
    ).scalars().all()


def get_owned_portfolio(
    db: Session,
    *,
    portfolio_id: int,
    user_id: int,
    for_update: bool = False,
) -> UserPortfolio | None:
    statement = select(UserPortfolio).where(
        UserPortfolio.id == portfolio_id,
        UserPortfolio.user_id == user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def list_lifecycle_plans_for_groups(
    db: Session,
    *,
    user_id: int,
    position_group_ids: Iterable[str],
) -> list[PositionLifecyclePlan]:
    group_ids = list(position_group_ids)
    if not group_ids:
        return []
    return db.execute(
        select(PositionLifecyclePlan).where(
            PositionLifecyclePlan.user_id == user_id,
            PositionLifecyclePlan.position_group_id.in_(group_ids),
        )
    ).scalars().all()


def latest_final_raw_data_by_symbol(
    db: Session,
    *,
    symbols: Iterable[str],
) -> dict[str, StockRawData]:
    symbol_list = list(symbols)
    if not symbol_list:
        return {}

    raw_rows = db.execute(
        select(StockRawData)
        .where(
            StockRawData.symbol.in_(symbol_list),
            StockRawData.raw_data_is_final.is_(True),
        )
        .order_by(StockRawData.symbol.asc(), StockRawData.record_date.desc(), StockRawData.id.desc())
    ).scalars().all()

    raw_data_by_symbol = {}
    for raw_row in raw_rows:
        raw_data_by_symbol.setdefault(raw_row.symbol, raw_row)
    return raw_data_by_symbol
