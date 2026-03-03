from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

from ai_stock_sentinel.models import StockSnapshot


class YFinanceCrawler:
    def fetch_basic_snapshot(self, symbol: str = "2330.TW") -> StockSnapshot:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        history = ticker.history(period="5d", interval="1d")

        recent_closes = []
        if not history.empty and "Close" in history.columns:
            recent_closes = [float(value) for value in history["Close"].dropna().tolist()[-5:]]

        return StockSnapshot(
            symbol=symbol,
            currency=str(getattr(info, "currency", "TWD") or "TWD"),
            current_price=float(getattr(info, "last_price", 0.0) or 0.0),
            previous_close=float(getattr(info, "previous_close", 0.0) or 0.0),
            day_open=float(getattr(info, "open", 0.0) or 0.0),
            day_high=float(getattr(info, "day_high", 0.0) or 0.0),
            day_low=float(getattr(info, "day_low", 0.0) or 0.0),
            volume=int(getattr(info, "last_volume", 0) or 0),
            recent_closes=recent_closes,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
