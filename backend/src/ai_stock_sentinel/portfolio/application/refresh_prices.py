from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from ai_stock_sentinel.clock import TAIPEI_TZ
from ai_stock_sentinel.models import StockSnapshot
from ai_stock_sentinel.portfolio.application.get_risk_summary import build_user_portfolio_risk_summary
from ai_stock_sentinel.portfolio.repository import list_active_portfolios


MAX_PRICE_REFRESH_WORKERS = 4


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
    request_time = now or datetime.now(timezone.utc)
    if request_time.tzinfo is None:
        request_time = request_time.replace(tzinfo=TAIPEI_TZ)
    refreshed_at = request_time.astimezone(TAIPEI_TZ)
    price_quotes_by_symbol = _fetch_quotes(
        symbols,
        quote_fetcher=quote_fetcher,
        now=request_time,
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
    market_session, is_final = _market_session(snapshot, data_date=data_date, now=now)
    return {
        "status": "refreshed",
        "current_price": float(snapshot.current_price),
        "source": "yfinance_fast_info",
        "fetched_at": snapshot.fetched_at,
        "data_date": data_date,
        "market_session": market_session,
        "is_final": is_final,
    }


def _market_session(
    snapshot: StockSnapshot,
    *,
    data_date: str | None,
    now: datetime,
) -> tuple[str, bool | None]:
    timezone_name = (snapshot.exchange_timezone or "").strip()
    if (
        not snapshot.exchange
        or not timezone_name
        or not snapshot.regular_market_open
        or not snapshot.regular_market_close
        or data_date is None
        or now.tzinfo is None
    ):
        return "unknown", None

    try:
        exchange_timezone = ZoneInfo(timezone_name)
        exchange_now = now.astimezone(exchange_timezone)
        quote_date = date.fromisoformat(data_date)
        market_open = _parse_market_boundary(snapshot.regular_market_open, exchange_timezone)
        market_close = _parse_market_boundary(snapshot.regular_market_close, exchange_timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return "unknown", None

    exchange_date = exchange_now.date()
    if quote_date < exchange_date:
        return "closed", True
    if quote_date != exchange_date or exchange_now.weekday() >= 5:
        return "unknown", None

    if market_open.date() != quote_date or market_close.date() != quote_date:
        return "unknown", None
    if market_open <= exchange_now < market_close:
        return "intraday", False
    if exchange_now >= market_close:
        return "closed", True
    return "unknown", None


def _parse_market_boundary(value: str, exchange_timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=exchange_timezone)
    return parsed.astimezone(exchange_timezone)
