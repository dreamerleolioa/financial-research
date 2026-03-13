# backend/src/ai_stock_sentinel/services/history_loader.py
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from sqlalchemy import select, text
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


def _compute_indicators_from_history(history) -> dict:
    """從 yfinance history DataFrame 計算技術指標摘要。"""
    if history.empty or "Close" not in history.columns:
        return {}
    closes = history["Close"].dropna().tolist()
    if not closes:
        return {}

    def _ma(n: int) -> float | None:
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    last_close = float(closes[-1])
    ma5  = _ma(5)
    ma20 = _ma(20)
    ma60 = _ma(60)

    # RSI-14
    rsi14: float | None = None
    if len(closes) >= 15:
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        avg_gain = sum(gains[-14:]) / 14
        avg_loss = sum(losses[-14:]) / 14
        if avg_loss == 0:
            rsi14 = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi14 = round(100 - 100 / (1 + rs), 2)

    return {
        "ma5":         ma5,
        "ma20":        ma20,
        "ma60":        ma60,
        "rsi_14":      rsi14,
        "close_price": last_close,
    }


def backfill_yesterday_indicators(db: Session, symbol: str) -> None:
    """若昨日快取為盤中未定稿（is_final=False），補抓收盤指標並更新。

    只更新 indicators 與 is_final，不動 action_tag/final_verdict。
    """
    yesterday = date.today() - timedelta(days=1)
    row = db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == yesterday,
        )
    ).scalar_one_or_none()

    if row is None or row.is_final:
        return

    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="5d", interval="1d")
        # 只取昨日那列
        history.index = history.index.normalize()
        yesterday_ts = pd.Timestamp(yesterday)
        if yesterday_ts in history.index:
            history = history.loc[:yesterday_ts]
        indicators = _compute_indicators_from_history(history)
    except Exception:
        return  # yfinance 失敗時靜默跳過，不影響主流程

    if not indicators:
        return

    db.execute(
        text("""
            UPDATE stock_analysis_cache
            SET indicators = CAST(:indicators AS jsonb),
                is_final   = TRUE,
                updated_at = NOW()
            WHERE symbol      = :symbol
              AND record_date = :record_date
        """),
        {
            "indicators":  json.dumps(indicators),
            "symbol":      symbol,
            "record_date": yesterday.isoformat(),
        },
    )
    db.commit()
