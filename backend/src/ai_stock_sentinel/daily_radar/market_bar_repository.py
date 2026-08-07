from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.market_bar_provider import MarketDailyBar
from ai_stock_sentinel.db.models import TaiwanDailyBar


DEFAULT_MARKET_BAR_DATASET = "taiwan_market_daily_ohlcv"
DEFAULT_ADJUSTMENT_MODE = "unadjusted"


def upsert_taiwan_daily_bars(
    session: Session,
    bars: Iterable[MarketDailyBar],
    *,
    dataset: str = DEFAULT_MARKET_BAR_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
) -> int:
    materialized = list(bars)
    if not materialized:
        return 0
    symbols = sorted({bar.symbol for bar in materialized})
    dates = sorted({bar.trade_date for bar in materialized})
    existing = session.scalars(
        select(TaiwanDailyBar).where(
            TaiwanDailyBar.symbol.in_(symbols),
            TaiwanDailyBar.trade_date.in_(dates),
            TaiwanDailyBar.dataset == dataset,
            TaiwanDailyBar.adjustment_mode == adjustment_mode,
        )
    ).all()
    by_key = {(row.symbol, row.trade_date): row for row in existing}
    for bar in materialized:
        row = by_key.get((bar.symbol, bar.trade_date))
        if row is None:
            row = TaiwanDailyBar(
                symbol=bar.symbol,
                trade_date=bar.trade_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
            )
            session.add(row)
            by_key[(bar.symbol, bar.trade_date)] = row
        row.market = bar.market
        row.name = bar.name
        row.open = bar.open
        row.high = bar.high
        row.low = bar.low
        row.close = bar.close
        row.volume = bar.volume
        row.amount = bar.amount
        row.source_provider = bar.source_provider
        row.source_dataset = bar.source_dataset
        row.is_final = bar.is_final
    return len(materialized)


def get_taiwan_daily_bars(
    session: Session,
    *,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
    dataset: str = DEFAULT_MARKET_BAR_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
    final_only: bool = True,
) -> list[TaiwanDailyBar]:
    if not symbols:
        return []
    statement = select(TaiwanDailyBar).where(
        TaiwanDailyBar.symbol.in_(list(symbols)),
        TaiwanDailyBar.trade_date >= start_date,
        TaiwanDailyBar.trade_date <= end_date,
        TaiwanDailyBar.dataset == dataset,
        TaiwanDailyBar.adjustment_mode == adjustment_mode,
    )
    if final_only:
        statement = statement.where(TaiwanDailyBar.is_final.is_(True))
    return list(
        session.scalars(
            statement.order_by(TaiwanDailyBar.symbol.asc(), TaiwanDailyBar.trade_date.asc())
        ).all()
    )


def market_bar_count_for_date(
    session: Session,
    *,
    trade_date: date,
    dataset: str = DEFAULT_MARKET_BAR_DATASET,
) -> int:
    return len(
        session.scalars(
            select(TaiwanDailyBar.id).where(
                TaiwanDailyBar.trade_date == trade_date,
                TaiwanDailyBar.dataset == dataset,
                TaiwanDailyBar.is_final.is_(True),
            )
        ).all()
    )


__all__ = [
    "DEFAULT_ADJUSTMENT_MODE",
    "DEFAULT_MARKET_BAR_DATASET",
    "get_taiwan_daily_bars",
    "market_bar_count_for_date",
    "upsert_taiwan_daily_bars",
]
