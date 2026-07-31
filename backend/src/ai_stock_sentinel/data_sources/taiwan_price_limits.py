from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from threading import BoundedSemaphore
from typing import Callable, Literal
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PriceLimitStatus = Literal["limit_up", "limit_down", "normal", "unknown"]

logger = logging.getLogger(__name__)

_MIS_STOCK_INFO_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_TAIWAN_SYMBOL_PATTERN = re.compile(r"^(?P<code>[0-9A-Z]+)\.(?P<suffix>TW|TWO)$")
_MAX_RESPONSE_BYTES = 64 * 1024
_PRICE_LIMIT_MAX_IN_FLIGHT = 4
_PRICE_LIMIT_RESOLVE_TIMEOUT_SECONDS = 0.25
_PRICE_LIMIT_EXECUTOR = ThreadPoolExecutor(
    max_workers=_PRICE_LIMIT_MAX_IN_FLIGHT,
    thread_name_prefix="price-limit",
)
_PRICE_LIMIT_CAPACITY = BoundedSemaphore(_PRICE_LIMIT_MAX_IN_FLIGHT)


@dataclass(slots=True, frozen=True)
class TaiwanPriceLimitSnapshot:
    status: PriceLimitStatus
    limit_up_price: float | None = None
    limit_down_price: float | None = None

    @classmethod
    def unknown(cls) -> TaiwanPriceLimitSnapshot:
        return cls(status="unknown")


def fetch_taiwan_price_limits(
    symbol: str,
    *,
    current_price: float,
    timeout: float = 3.0,
    opener: Callable[..., object] | None = None,
) -> TaiwanPriceLimitSnapshot:
    channel = _market_channel(symbol)
    if channel is None:
        return TaiwanPriceLimitSnapshot.unknown()

    market, stock_code = channel
    query = urlencode({
        "ex_ch": f"{market}_{stock_code}.tw",
        "json": "1",
        "delay": "0",
    })
    request = Request(
        f"{_MIS_STOCK_INFO_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "financial-research/price-limit-status",
        },
    )
    open_request = opener or urlopen
    with open_request(request, timeout=timeout) as response:  # type: ignore[attr-defined]
        response_body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(response_body) > _MAX_RESPONSE_BYTES:
        raise ValueError("TWSE MIS response exceeds the allowed size")
    payload = json.loads(response_body.decode("utf-8"))

    quote = _matching_quote(payload, stock_code=stock_code)
    limit_up_price = _positive_float(quote.get("u"))
    limit_down_price = _positive_float(quote.get("w"))
    return TaiwanPriceLimitSnapshot(
        status=_classify_price_limit_status(
            current_price=current_price,
            limit_up_price=limit_up_price,
            limit_down_price=limit_down_price,
        ),
        limit_up_price=limit_up_price,
        limit_down_price=limit_down_price,
    )


def supports_taiwan_price_limits(symbol: str) -> bool:
    return _market_channel(symbol) is not None


def fetch_taiwan_price_limits_with_deadline(
    symbol: str,
    *,
    current_price: float,
    resolve_timeout: float = _PRICE_LIMIT_RESOLVE_TIMEOUT_SECONDS,
    fetcher: Callable[..., TaiwanPriceLimitSnapshot] = fetch_taiwan_price_limits,
) -> TaiwanPriceLimitSnapshot:
    if (
        not supports_taiwan_price_limits(symbol)
        or not math.isfinite(current_price)
        or current_price <= 0
    ):
        return TaiwanPriceLimitSnapshot.unknown()
    future = _submit_price_limit_fetch(
        symbol=symbol,
        current_price=current_price,
        fetcher=fetcher,
    )
    if future is None:
        return TaiwanPriceLimitSnapshot.unknown()
    try:
        return future.result(timeout=resolve_timeout)
    except FutureTimeoutError:
        future.cancel()
        logger.warning(json.dumps({
            "event": "provider_price_limit_deadline_exceeded",
            "provider": "twse-mis",
            "symbol": symbol,
        }))
        return TaiwanPriceLimitSnapshot.unknown()


def _submit_price_limit_fetch(
    *,
    symbol: str,
    current_price: float,
    fetcher: Callable[..., TaiwanPriceLimitSnapshot],
) -> Future[TaiwanPriceLimitSnapshot] | None:
    if not _PRICE_LIMIT_CAPACITY.acquire(blocking=False):
        logger.warning(json.dumps({
            "event": "provider_price_limit_capacity_exhausted",
            "provider": "twse-mis",
            "symbol": symbol,
        }))
        return None
    try:
        future = _PRICE_LIMIT_EXECUTOR.submit(
            _fetch_price_limit_snapshot,
            symbol=symbol,
            current_price=current_price,
            fetcher=fetcher,
        )
    except Exception:
        _PRICE_LIMIT_CAPACITY.release()
        raise
    future.add_done_callback(lambda _future: _PRICE_LIMIT_CAPACITY.release())
    return future


def _fetch_price_limit_snapshot(
    *,
    symbol: str,
    current_price: float,
    fetcher: Callable[..., TaiwanPriceLimitSnapshot],
) -> TaiwanPriceLimitSnapshot:
    try:
        return fetcher(symbol, current_price=current_price)
    except Exception as exc:
        logger.warning(json.dumps({
            "event": "provider_price_limit_failure",
            "provider": "twse-mis",
            "symbol": symbol,
            "error_code": type(exc).__name__,
        }))
        return TaiwanPriceLimitSnapshot.unknown()


def _market_channel(symbol: str) -> tuple[str, str] | None:
    match = _TAIWAN_SYMBOL_PATTERN.fullmatch(symbol.strip().upper())
    if match is None:
        return None
    market = "tse" if match.group("suffix") == "TW" else "otc"
    return market, match.group("code")


def _matching_quote(payload: object, *, stock_code: str) -> dict:
    if not isinstance(payload, dict) or payload.get("rtcode") != "0000":
        raise ValueError("TWSE MIS returned an unsuccessful response")
    quotes = payload.get("msgArray")
    if not isinstance(quotes, list):
        raise ValueError("TWSE MIS response is missing msgArray")
    for quote in quotes:
        if isinstance(quote, dict) and str(quote.get("c", "")).strip().upper() == stock_code:
            return quote
    raise ValueError(f"TWSE MIS response does not contain symbol {stock_code}")


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _classify_price_limit_status(
    *,
    current_price: float,
    limit_up_price: float | None,
    limit_down_price: float | None,
) -> PriceLimitStatus:
    if not math.isfinite(current_price) or current_price <= 0:
        return "unknown"
    absolute_tolerance = max(1e-6, current_price * 1e-8)
    if limit_up_price is not None and math.isclose(
        current_price,
        limit_up_price,
        rel_tol=0,
        abs_tol=absolute_tolerance,
    ):
        return "limit_up"
    if limit_down_price is not None and math.isclose(
        current_price,
        limit_down_price,
        rel_tol=0,
        abs_tol=absolute_tolerance,
    ):
        return "limit_down"
    if limit_up_price is not None and limit_down_price is not None:
        return "normal"
    return "unknown"
