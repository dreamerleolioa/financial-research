# backend/src/ai_stock_sentinel/services/history_loader.py
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import StockAnalysisCache


def _derive_ma_alignment(indicators: dict) -> str:
    """從技術指標判斷均線排列方向。"""
    ma5  = indicators.get("ma5")
    ma20 = indicators.get("ma20")
    ma60 = indicators.get("ma60")
    if ma5 is not None and ma20 is not None and ma60 is not None:
        if ma5 > ma20 > ma60:
            return "bullish"
        if ma5 < ma20 < ma60:
            return "bearish"
    return "neutral"


def load_yesterday_context(symbol: str, db: Session) -> dict | None:
    """從 DB 讀取昨日分析結果，作為 LLM 的歷史上下文。

    回傳的數值必須從 DB 讀取，嚴禁由呼叫方或 LLM 推斷。
    """
    yesterday = date.today() - timedelta(days=1)
    result = db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == yesterday,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    indicators = row.indicators or {}
    return {
        "prev_action_tag":   row.action_tag,
        "prev_confidence":   float(row.signal_confidence) if row.signal_confidence is not None else None,
        "prev_rsi":          indicators.get("rsi_14"),
        "prev_ma_alignment": _derive_ma_alignment(indicators),
    }
