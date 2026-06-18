from __future__ import annotations

from collections.abc import Callable
from datetime import date

from sqlalchemy.orm import Session

from ai_stock_sentinel.portfolio.repository import (
    latest_final_raw_data_by_symbol,
    list_active_portfolios,
    list_lifecycle_plans_for_groups,
)
from ai_stock_sentinel.portfolio.risk_summary import build_portfolio_risk_summary


def build_user_portfolio_risk_summary(
    db: Session,
    *,
    user_id: int,
    symbol_name_resolver: Callable[[str], str | None],
    as_of_date: date | None = None,
) -> dict:
    rows = list_active_portfolios(db, user_id=user_id)
    group_ids = [row.position_group_id for row in rows]
    plans = list_lifecycle_plans_for_groups(db, user_id=user_id, position_group_ids=group_ids)
    plans_by_group = {plan.position_group_id: plan for plan in plans}

    symbols = sorted({row.symbol for row in rows})
    raw_data_by_symbol = latest_final_raw_data_by_symbol(db, symbols=symbols)

    return build_portfolio_risk_summary(
        rows,
        plans_by_group=plans_by_group,
        raw_data_by_symbol=raw_data_by_symbol,
        symbol_names_by_symbol={symbol: symbol_name_resolver(symbol) for symbol in symbols},
        as_of_date=as_of_date or date.today(),
    )
