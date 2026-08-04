from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from datetime import datetime, timezone
from threading import local
from time import monotonic
from typing import Iterator

from curl_cffi import requests as curl_requests
import yfinance as yf

from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.models import StockSnapshot

logger = logging.getLogger(__name__)
DEFAULT_YFINANCE_TIMEOUT_SECONDS = 10.0
PORTFOLIO_YFINANCE_TIMEOUT_SECONDS = 4.0


class _DeadlineSession(curl_requests.Session):
    def __init__(self) -> None:
        super().__init__(impersonate="chrome")
        self._deadline_state = local()

    @contextmanager
    def deadline(self, timeout: float) -> Iterator[None]:
        previous_deadline = getattr(self._deadline_state, "deadline_at", None)
        self._deadline_state.deadline_at = monotonic() + max(0.0, timeout)
        try:
            yield
        finally:
            if previous_deadline is None:
                del self._deadline_state.deadline_at
            else:
                self._deadline_state.deadline_at = previous_deadline

    def request(self, method, url, *args, **kwargs):
        deadline_at = getattr(self._deadline_state, "deadline_at", None)
        if deadline_at is not None:
            remaining = deadline_at - monotonic()
            if remaining <= 0:
                raise TimeoutError("yfinance provider deadline exceeded")
            requested_timeout = kwargs.get("timeout")
            if isinstance(requested_timeout, int | float) and not isinstance(requested_timeout, bool):
                remaining = min(remaining, float(requested_timeout))
            kwargs["timeout"] = max(0.001, remaining)
        return super().request(method, url, *args, **kwargs)


_YFINANCE_SESSION = _DeadlineSession()


def check_symbol_exists(symbol: str) -> bool:
    """yfinance 輕量驗證：代號有效回傳 True，否則回傳 False。"""
    hist = yf.Ticker(symbol).history(period="5d", interval="1d")
    return not (hist.empty or hist["Close"].dropna().empty)


class YFinanceCrawler:
    def fetch_basic_snapshot(
        self,
        symbol: str = "2330.TW",
        *,
        provider_timeout: float = DEFAULT_YFINANCE_TIMEOUT_SECONDS,
        resolve_name: bool = True,
    ) -> StockSnapshot:
        try:
            with _YFINANCE_SESSION.deadline(provider_timeout):
                ticker = yf.Ticker(symbol, session=_YFINANCE_SESSION)
                info = ticker.fast_info
                currency = str(getattr(info, "currency", "TWD") or "TWD")
                current_price = float(getattr(info, "last_price", 0.0) or 0.0)
                quote_observed_at = datetime.now(timezone.utc).isoformat()
                previous_close = float(getattr(info, "previous_close", 0.0) or 0.0)
                day_open = float(getattr(info, "open", 0.0) or 0.0)
                day_high = float(getattr(info, "day_high", 0.0) or 0.0)
                day_low = float(getattr(info, "day_low", 0.0) or 0.0)
                volume = int(getattr(info, "last_volume", 0) or 0)
                history = ticker.history(
                    period="1y",
                    interval="1d",
                    timeout=provider_timeout,
                )
                history_metadata = _history_metadata(ticker, symbol=symbol)
                exchange = _optional_text(history_metadata.get("exchangeName")) or _safe_info_text(
                    info,
                    "exchange",
                )
                exchange_timezone = _optional_text(
                    history_metadata.get("exchangeTimezoneName")
                ) or _safe_info_text(
                    info,
                    "timezone",
                )
        except Exception as exc:
            logger.warning(json.dumps({
                "event": "provider_failure",
                "provider": "yfinance",
                "symbol": symbol,
                "error_code": type(exc).__name__,
            }))
            raise

        recent_closes = []
        recent_highs = []
        recent_lows = []
        recent_volumes = []
        recent_volume_dates = []
        data_dates = {}
        if not history.empty and "Close" in history.columns:
            close_series = history["Close"].dropna()
            recent_closes = [float(value) for value in close_series.tolist()]
            close_dates = _history_dates(close_series.index)
            if close_dates:
                data_dates["ohlcv"] = close_dates[-1]
        if not history.empty and "High" in history.columns:
            recent_highs = [float(value) for value in history["High"].dropna().tolist()]
        if not history.empty and "Low" in history.columns:
            recent_lows = [float(value) for value in history["Low"].dropna().tolist()]
        if not history.empty and "Volume" in history.columns:
            volume_series = history["Volume"].dropna()
            recent_volumes = [float(value) for value in volume_series.tolist()]
            recent_volume_dates = _history_dates(volume_series.index)

        volume_source = "realtime"
        if volume <= 0 and not history.empty and "Volume" in history.columns:
            volume_series = history["Volume"].dropna()
            if not volume_series.empty:
                volume = int(float(volume_series.iloc[-1]) or 0)
                volume_source = "history_fallback"
        if volume <= 0:
            volume_source = "unavailable"

        regular_market_period = _regular_market_period(history_metadata)
        snapshot = StockSnapshot(
            symbol=symbol,
            name=resolve_symbol_name(symbol) if resolve_name else None,
            currency=currency,
            current_price=current_price,
            previous_close=previous_close,
            day_open=day_open,
            day_high=day_high,
            day_low=day_low,
            volume=volume,
            recent_closes=recent_closes,
            fetched_at=quote_observed_at,
            volume_source=volume_source,
            recent_highs=recent_highs,
            recent_lows=recent_lows,
            recent_volumes=recent_volumes,
            recent_volume_dates=recent_volume_dates,
            data_dates=data_dates,
            exchange=exchange,
            exchange_timezone=exchange_timezone,
            regular_market_open=_market_boundary_iso(regular_market_period.get("start")),
            regular_market_close=_market_boundary_iso(regular_market_period.get("end")),
        )
        logger.info(json.dumps({
            "event": "provider_success",
            "provider": "yfinance",
            "symbol": symbol,
            "is_fallback": False,
        }))
        return snapshot

    def fetch_portfolio_snapshot(self, symbol: str = "2330.TW") -> StockSnapshot:
        return self.fetch_basic_snapshot(
            symbol,
            provider_timeout=PORTFOLIO_YFINANCE_TIMEOUT_SECONDS,
            resolve_name=False,
        )


def _history_metadata(ticker: yf.Ticker, *, symbol: str) -> dict:
    try:
        metadata = ticker.history_metadata
    except Exception as exc:
        logger.warning(json.dumps({
            "event": "provider_market_metadata_failure",
            "provider": "yfinance",
            "symbol": symbol,
            "error_code": type(exc).__name__,
        }))
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _regular_market_period(metadata: dict) -> dict:
    current_period = metadata.get("currentTradingPeriod")
    if not isinstance(current_period, dict):
        return {}
    regular_period = current_period.get("regular")
    return regular_period if isinstance(regular_period, dict) else {}


def _market_boundary_iso(value: object) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    isoformat = getattr(value, "isoformat", None)
    if not callable(isoformat):
        return None
    normalized = isoformat()
    return normalized if isinstance(normalized, str) and normalized else None


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _safe_info_text(info: object, name: str) -> str | None:
    try:
        value = getattr(info, name, None)
    except Exception as exc:
        logger.warning(json.dumps({
            "event": "provider_optional_market_metadata_failure",
            "provider": "yfinance",
            "field": name,
            "error_code": type(exc).__name__,
        }))
        return None
    return _optional_text(value)


def _history_dates(index: object) -> list[str]:
    dates: list[str] = []
    try:
        values = list(index)  # type: ignore[arg-type]
    except TypeError:
        return dates
    for value in values:
        date_method = getattr(value, "date", None)
        if not callable(date_method):
            return []
        dates.append(date_method().isoformat())
    return dates
