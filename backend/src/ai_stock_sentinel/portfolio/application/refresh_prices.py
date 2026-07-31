from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
import math
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.clock import TAIPEI_TZ
from ai_stock_sentinel.models import StockSnapshot
from ai_stock_sentinel.portfolio.application.get_risk_summary import build_user_portfolio_risk_summary
from ai_stock_sentinel.portfolio.repository import list_active_portfolios


MAX_PRICE_REFRESH_WORKERS = 4
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(13, 30)


class PortfolioPriceRefreshTargetNotFound(Exception):
    pass


def refresh_user_portfolio_prices(
    db: Session,
    *,
    user_id: int,
    portfolio_ids: Sequence[int] | None,
    quote_fetcher: Callable[[str], StockSnapshot],
    symbol_name_resolver: Callable[[str], str | None],
    now: datetime | None = None,
) -> dict[str, Any]:
    active_positions = list_active_portfolios(db, user_id=user_id)
    active_by_id = {int(position.id): position for position in active_positions}

    if portfolio_ids is None:
        selected_positions = active_positions
    else:
        requested_ids = set(portfolio_ids)
        missing_ids = sorted(requested_ids - active_by_id.keys())
        if missing_ids:
            raise PortfolioPriceRefreshTargetNotFound
        selected_positions = [active_by_id[portfolio_id] for portfolio_id in requested_ids]

    symbols = sorted({str(position.symbol) for position in selected_positions})
    refreshed_at = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    price_quotes_by_symbol = _fetch_quotes(
        symbols,
        quote_fetcher=quote_fetcher,
        now=refreshed_at,
    )

    summary = build_user_portfolio_risk_summary(
        db,
        user_id=user_id,
        symbol_name_resolver=symbol_name_resolver,
        price_quotes_by_symbol=price_quotes_by_symbol,
    )

    refreshed_symbols = sorted(
        symbol
        for symbol, quote in price_quotes_by_symbol.items()
        if quote["status"] == "refreshed"
    )
    failed_symbols = sorted(set(symbols) - set(refreshed_symbols))
    if failed_symbols and refreshed_symbols:
        refresh_status = "partial"
    elif failed_symbols:
        refresh_status = "failed"
    else:
        refresh_status = "complete"

    summary["price_refresh"] = {
        "status": refresh_status,
        "requested_count": len(symbols),
        "refreshed_count": len(refreshed_symbols),
        "failed_count": len(failed_symbols),
        "refreshed_symbols": refreshed_symbols,
        "failed_symbols": failed_symbols,
        "refreshed_at": refreshed_at.isoformat(),
    }
    return summary


def _fetch_quotes(
    symbols: list[str],
    *,
    quote_fetcher: Callable[[str], StockSnapshot],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}

    quotes: dict[str, dict[str, Any]] = {}
    worker_count = min(MAX_PRICE_REFRESH_WORKERS, len(symbols))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_by_symbol = {
            executor.submit(quote_fetcher, symbol): symbol
            for symbol in symbols
        }
        for future in as_completed(future_by_symbol):
            symbol = future_by_symbol[future]
            try:
                snapshot = future.result()
                current_price = float(snapshot.current_price)
                if not math.isfinite(current_price) or current_price <= 0:
                    raise ValueError("latest quote is unavailable")
                quotes[symbol] = _quote_payload(snapshot, now=now)
            except Exception as exc:
                quotes[symbol] = {
                    "status": "failed",
                    "error_code": exc.__class__.__name__,
                }
    return quotes


def _quote_payload(snapshot: StockSnapshot, *, now: datetime) -> dict[str, Any]:
    data_date = snapshot.recent_volume_dates[-1] if snapshot.recent_volume_dates else None
    if data_date is None:
        market_session = "unknown"
        is_final = None
    else:
        is_intraday = (
            data_date == now.date().isoformat()
            and now.weekday() < 5
            and MARKET_OPEN <= now.time().replace(tzinfo=None) < MARKET_CLOSE
        )
        market_session = "intraday" if is_intraday else "closed"
        is_final = not is_intraday
    return {
        "status": "refreshed",
        "current_price": float(snapshot.current_price),
        "source": "yfinance_fast_info",
        "fetched_at": snapshot.fetched_at,
        "data_date": data_date,
        "market_session": market_session,
        "is_final": is_final,
    }
