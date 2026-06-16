from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import yfinance as yf

from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.models import StockSnapshot

logger = logging.getLogger(__name__)


def check_symbol_exists(symbol: str) -> bool:
    """yfinance 輕量驗證：代號有效回傳 True，否則回傳 False。"""
    hist = yf.Ticker(symbol).history(period="5d", interval="1d")
    return not (hist.empty or hist["Close"].dropna().empty)


class YFinanceCrawler:
    def fetch_basic_snapshot(self, symbol: str = "2330.TW") -> StockSnapshot:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            history = ticker.history(period="1y", interval="1d")
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
        if not history.empty and "Close" in history.columns:
            recent_closes = [float(value) for value in history["Close"].dropna().tolist()]
        if not history.empty and "High" in history.columns:
            recent_highs = [float(value) for value in history["High"].dropna().tolist()]
        if not history.empty and "Low" in history.columns:
            recent_lows = [float(value) for value in history["Low"].dropna().tolist()]
        if not history.empty and "Volume" in history.columns:
            recent_volumes = [float(value) for value in history["Volume"].dropna().tolist()]

        volume = int(getattr(info, "last_volume", 0) or 0)
        volume_source = "realtime"
        if volume <= 0 and not history.empty and "Volume" in history.columns:
            volume_series = history["Volume"].dropna()
            if not volume_series.empty:
                volume = int(float(volume_series.iloc[-1]) or 0)
                volume_source = "history_fallback"
        if volume <= 0:
            volume_source = "unavailable"

        snapshot = StockSnapshot(
            symbol=symbol,
            name=resolve_symbol_name(symbol),
            currency=str(getattr(info, "currency", "TWD") or "TWD"),
            current_price=float(getattr(info, "last_price", 0.0) or 0.0),
            previous_close=float(getattr(info, "previous_close", 0.0) or 0.0),
            day_open=float(getattr(info, "open", 0.0) or 0.0),
            day_high=float(getattr(info, "day_high", 0.0) or 0.0),
            day_low=float(getattr(info, "day_low", 0.0) or 0.0),
            volume=volume,
            recent_closes=recent_closes,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            volume_source=volume_source,
            recent_highs=recent_highs,
            recent_lows=recent_lows,
            recent_volumes=recent_volumes,
        )
        logger.info(json.dumps({
            "event": "provider_success",
            "provider": "yfinance",
            "symbol": symbol,
            "is_fallback": False,
        }))
        return snapshot
