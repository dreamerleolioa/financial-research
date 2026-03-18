from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import yfinance as yf

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
            history = ticker.history(period="3mo", interval="1d")
        except Exception as exc:
            logger.warning(json.dumps({
                "event": "provider_failure",
                "provider": "yfinance",
                "symbol": symbol,
                "error_code": type(exc).__name__,
            }))
            raise

        recent_closes = []
        if not history.empty and "Close" in history.columns:
            recent_closes = [float(value) for value in history["Close"].dropna().tolist()]

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
        )
        logger.info(json.dumps({
            "event": "provider_success",
            "provider": "yfinance",
            "symbol": symbol,
            "is_fallback": False,
        }))
        return snapshot
