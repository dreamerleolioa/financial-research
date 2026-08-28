from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import math
from threading import BoundedSemaphore
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from ai_stock_sentinel.clock import TAIPEI_TZ
from ai_stock_sentinel.models import StockSnapshot
from ai_stock_sentinel.portfolio.application.get_risk_summary import build_user_portfolio_risk_summary
from ai_stock_sentinel.portfolio.repository import list_active_portfolios
from ai_stock_sentinel.portfolio.storage_limits import PORTFOLIO_PRICE_MAX
from ai_stock_sentinel.taiwan_symbols import is_supported_taiwan_symbol


MAX_PRICE_REFRESH_WORKERS = 4
PRICE_REFRESH_RESPONSE_DEADLINE_SECONDS = 5.0
_PRICE_REFRESH_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_PRICE_REFRESH_WORKERS,
    thread_name_prefix="portfolio-price",
)
_PRICE_REFRESH_CAPACITY = BoundedSemaphore(MAX_PRICE_REFRESH_WORKERS)


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
    response_deadline: float = PRICE_REFRESH_RESPONSE_DEADLINE_SECONDS,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}

    quotes: dict[str, dict[str, Any]] = {}
    future_by_symbol: dict[Future[StockSnapshot], str] = {}
    pending_symbols: deque[str] = deque()
    for symbol in symbols:
        if not is_supported_taiwan_symbol(symbol):
            quotes[symbol] = _failed_quote("UnsupportedMarket")
            continue
        pending_symbols.append(symbol)

    deadline_at = monotonic() + max(0.0, response_deadline)
    while pending_symbols or future_by_symbol:
        while pending_symbols:
            if monotonic() >= deadline_at:
                for pending_symbol in pending_symbols:
                    quotes[pending_symbol] = _failed_quote("TimeoutError")
                pending_symbols.clear()
                break
            symbol = pending_symbols[0]
            try:
                future = _submit_quote_fetch(symbol, quote_fetcher=quote_fetcher)
            except Exception as exc:
                quotes[symbol] = _failed_quote(exc.__class__.__name__)
                pending_symbols.popleft()
                continue
            if future is None:
                break
            pending_symbols.popleft()
            future_by_symbol[future] = symbol

        if not future_by_symbol:
            for symbol in pending_symbols:
                quotes[symbol] = _failed_quote("ProviderCapacityExhausted")
            break

        remaining_time = max(0.0, deadline_at - monotonic())
        done, _not_done = wait(
            set(future_by_symbol),
            timeout=remaining_time,
            return_when=FIRST_COMPLETED,
        )
        if not done:
            for future, symbol in future_by_symbol.items():
                future.cancel()
                quotes[symbol] = _failed_quote("TimeoutError")
            for symbol in pending_symbols:
                quotes[symbol] = _failed_quote("TimeoutError")
            break

        for future in done:
            symbol = future_by_symbol.pop(future)
            try:
                snapshot = future.result()
                quotes[symbol] = _quote_payload(snapshot)
            except Exception as exc:
                quotes[symbol] = _failed_quote(exc.__class__.__name__)
    return quotes


def _submit_quote_fetch(
    symbol: str,
    *,
    quote_fetcher: Callable[[str], StockSnapshot],
) -> Future[StockSnapshot] | None:
    capacity = _PRICE_REFRESH_CAPACITY
    if not capacity.acquire(blocking=False):
        return None
    try:
        future = _PRICE_REFRESH_EXECUTOR.submit(
            _run_quote_fetch,
            symbol=symbol,
            quote_fetcher=quote_fetcher,
            capacity=capacity,
        )
    except Exception:
        capacity.release()
        raise
    future.add_done_callback(lambda completed: capacity.release() if completed.cancelled() else None)
    return future


def _run_quote_fetch(
    *,
    symbol: str,
    quote_fetcher: Callable[[str], StockSnapshot],
    capacity: BoundedSemaphore,
) -> StockSnapshot:
    try:
        return quote_fetcher(symbol)
    finally:
        capacity.release()


def _failed_quote(error_code: str) -> dict[str, str]:
    return {
        "status": "failed",
        "error_code": error_code,
    }


def _quote_payload(snapshot: StockSnapshot) -> dict[str, Any]:
    current_price = float(snapshot.current_price)
    if (
        not math.isfinite(current_price)
        or current_price <= 0
        or current_price > float(PORTFOLIO_PRICE_MAX)
    ):
        raise ValueError("latest quote is unavailable")
    data_date = snapshot.recent_volume_dates[-1] if snapshot.recent_volume_dates else None
    observed_at = _snapshot_observation_time(snapshot)
    market_session, is_final = _market_session(snapshot, now=observed_at)
    return {
        "status": "refreshed",
        "current_price": current_price,
        "source": "yfinance_fast_info",
        "fetched_at": snapshot.fetched_at,
        "data_date": data_date,
        "market_session": market_session,
        "is_final": is_final,
    }


def _snapshot_observation_time(snapshot: StockSnapshot) -> datetime | None:
    try:
        observed_at = datetime.fromisoformat(snapshot.fetched_at.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if observed_at.tzinfo is None:
        return None
    return observed_at


def _market_session(
    snapshot: StockSnapshot,
    *,
    now: datetime | None,
) -> tuple[str, bool | None]:
    timezone_name = (snapshot.exchange_timezone or "").strip()
    if (
        not snapshot.exchange
        or not timezone_name
        or not snapshot.regular_market_open
        or not snapshot.regular_market_close
        or now is None
        or now.tzinfo is None
    ):
        return "unknown", None

    try:
        exchange_timezone = ZoneInfo(timezone_name)
        exchange_now = now.astimezone(exchange_timezone)
        market_open = _parse_market_boundary(snapshot.regular_market_open, exchange_timezone)
        market_close = _parse_market_boundary(snapshot.regular_market_close, exchange_timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return "unknown", None

    exchange_date = exchange_now.date()
    if exchange_now.weekday() >= 5:
        return "unknown", None

    if market_open.date() != exchange_date or market_close.date() != exchange_date:
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
