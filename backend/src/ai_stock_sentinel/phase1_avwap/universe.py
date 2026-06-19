from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.repository import get_latest_daily_radar_run
from ai_stock_sentinel.db.models import DailyRadarCandidate, UserPortfolio, UserWatchlist


@dataclass
class ManagedUniverseSymbol:
    symbol: str
    sources: list[str] = field(default_factory=list)
    holding_entry_date: date | None = None
    holding_avg_cost: float | None = None


def resolve_phase1_managed_universe(
    session: Session,
    *,
    user_id: int,
    market: str = "TW",
) -> list[ManagedUniverseSymbol]:
    by_symbol: dict[str, ManagedUniverseSymbol] = {}
    for portfolio in session.scalars(
        select(UserPortfolio)
        .where(UserPortfolio.user_id == user_id, UserPortfolio.is_active.is_(True))
        .order_by(UserPortfolio.created_at.desc(), UserPortfolio.id.desc())
    ).all():
        item = _ensure_item(by_symbol, portfolio.symbol)
        _append_source(item, "active_holding")
        item.holding_entry_date = portfolio.entry_date
        item.holding_avg_cost = _float_or_none(portfolio.entry_price)

    for watchlist_item in session.scalars(
        select(UserWatchlist)
        .where(UserWatchlist.user_id == user_id)
        .order_by(UserWatchlist.sort_order.asc(), UserWatchlist.created_at.desc(), UserWatchlist.id.desc())
    ).all():
        item = _ensure_item(by_symbol, watchlist_item.symbol)
        _append_source(item, "watchlist")

    latest_run = get_latest_daily_radar_run(session, market=market)
    if latest_run is not None:
        for candidate in sorted(latest_run.candidates, key=_candidate_sort_key):
            item = _ensure_item(by_symbol, candidate.symbol)
            _append_source(item, "daily_radar_candidate")

    return list(by_symbol.values())


def _ensure_item(items: dict[str, ManagedUniverseSymbol], symbol: str) -> ManagedUniverseSymbol:
    normalized = str(symbol).strip().upper()
    if normalized not in items:
        items[normalized] = ManagedUniverseSymbol(symbol=normalized)
    return items[normalized]


def _append_source(item: ManagedUniverseSymbol, source: str) -> None:
    if source not in item.sources:
        item.sources.append(source)


def _float_or_none(value: Decimal | float | int | None) -> float | None:
    return None if value is None else float(value)


def _candidate_sort_key(candidate: DailyRadarCandidate) -> tuple[int, str]:
    return (-candidate.observation_score, candidate.symbol)


__all__ = [
    "ManagedUniverseSymbol",
    "resolve_phase1_managed_universe",
]
