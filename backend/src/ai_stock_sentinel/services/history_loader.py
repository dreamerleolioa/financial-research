# backend/src/ai_stock_sentinel/services/history_loader.py
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.metrics import adx, atr, bollinger_bands, donchian_channel, macd, mfi, obv, stochastic_kd
from ai_stock_sentinel.db.models import StockAnalysisCache


def _derive_ma_alignment(indicators: dict) -> str:
    """從技術指標判斷均線排列方向。"""
    ma5  = indicators.get("ma5")
    ma20 = indicators.get("ma20")
    ma60 = indicators.get("ma60")
    if (
        isinstance(ma5, (int, float))
        and isinstance(ma20, (int, float))
        and isinstance(ma60, (int, float))
    ):
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
        "prev_action_tag":        row.action_tag,
        "prev_confidence":        float(row.signal_confidence) if row.signal_confidence is not None else None,
        "prev_rsi":               indicators.get("rsi_14"),
        "prev_ma_alignment":      _derive_ma_alignment(indicators),
        "prev_macd_bias":         indicators.get("macd_bias"),
        "prev_bollinger_position": indicators.get("bollinger_position"),
        "prev_kd_signal":         indicators.get("kd_signal"),
        "prev_adx":               indicators.get("adx"),
        "prev_obv_signal":        indicators.get("obv_signal"),
        "prev_atr_pct":           indicators.get("atr_pct"),
        "prev_mfi_signal":        indicators.get("mfi_signal"),
        "prev_donchian_position": indicators.get("donchian_position"),
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

    # Bollinger Bands
    bb = bollinger_bands(closes)
    bollinger_position: str | None = None
    if bb is not None:
        upper = bb["bollinger_upper"]
        lower = bb["bollinger_lower"]
        mid = bb["bollinger_mid"]
        if upper is not None and lower is not None and mid is not None:
            band_range = upper - lower
            if band_range > 0:
                pct = (last_close - lower) / band_range
                if last_close >= upper * 0.99:
                    bollinger_position = "near_upper"
                elif last_close <= lower * 1.01:
                    bollinger_position = "near_lower"
                elif pct >= 0.5:
                    bollinger_position = "above_mid"
                else:
                    bollinger_position = "below_mid"
            else:
                bollinger_position = "flat"

    # MACD
    macd_data = macd(closes)
    macd_bias_val = macd_data["macd_bias"] if macd_data else None

    highs = history["High"].dropna().tolist() if "High" in history.columns else []
    lows = history["Low"].dropna().tolist() if "Low" in history.columns else []
    volumes = history["Volume"].dropna().tolist() if "Volume" in history.columns else []
    aligned_hilo = len(highs) == len(closes) and len(lows) == len(closes)
    aligned_volume = len(volumes) == len(closes)
    kd_data = stochastic_kd(closes, highs, lows) if aligned_hilo else None
    adx_data = adx(closes, highs, lows) if aligned_hilo else None
    atr_data = atr(closes, highs, lows) if aligned_hilo else None
    mfi_data = mfi(closes, highs, lows, volumes) if aligned_hilo and aligned_volume else None
    donchian_data = donchian_channel(closes, highs, lows) if aligned_hilo else None
    obv_data = obv(closes, volumes) if aligned_volume else None

    return {
        "ma5":                ma5,
        "ma20":               ma20,
        "ma60":               ma60,
        "rsi_14":             rsi14,
        "close_price":        last_close,
        "bollinger_mid":      bb["bollinger_mid"] if bb else None,
        "bollinger_upper":    bb["bollinger_upper"] if bb else None,
        "bollinger_lower":    bb["bollinger_lower"] if bb else None,
        "bollinger_position": bollinger_position,
        "macd_line":          macd_data["macd_line"] if macd_data else None,
        "macd_signal":        macd_data["macd_signal"] if macd_data else None,
        "macd_hist":          macd_data["macd_hist"] if macd_data else None,
        "macd_bias":          macd_bias_val,
        "kd_k":               kd_data["k"] if kd_data else None,
        "kd_d":               kd_data["d"] if kd_data else None,
        "kd_signal":          kd_data["kd_signal"] if kd_data else None,
        "kd_zone":            kd_data["kd_zone"] if kd_data else None,
        "adx":                adx_data["adx"] if adx_data else None,
        "adx_trend_strength": adx_data["trend_strength"] if adx_data else None,
        "adx_trend_direction": adx_data["trend_direction"] if adx_data else None,
        "obv":                obv_data["obv"] if obv_data else None,
        "obv_signal":         obv_data["obv_signal"] if obv_data else None,
        "obv_trend_20d":      obv_data["obv_trend_20d"] if obv_data else None,
        "obv_trend_mid_long": obv_data["obv_trend_mid_long"] if obv_data else None,
        "obv_trend_mid_long_window": obv_data["obv_trend_mid_long_window"] if obv_data else None,
        "atr":                atr_data["atr"] if atr_data else None,
        "atr_pct":            atr_data["atr_pct"] if atr_data else None,
        "volatility_level":   atr_data["volatility_level"] if atr_data else None,
        "mfi":                mfi_data["mfi"] if mfi_data else None,
        "mfi_signal":         mfi_data["mfi_signal"] if mfi_data else None,
        "donchian_upper":     donchian_data["donchian_upper"] if donchian_data else None,
        "donchian_lower":     donchian_data["donchian_lower"] if donchian_data else None,
        "donchian_mid":       donchian_data["donchian_mid"] if donchian_data else None,
        "donchian_width_pct": donchian_data["donchian_width_pct"] if donchian_data else None,
        "donchian_position":  donchian_data["donchian_position"] if donchian_data else None,
    }


def backfill_yesterday_indicators(db: Session, symbol: str) -> None:
    """若昨日快取為盤中未定稿（analysis_is_final=False），補抓收盤指標並更新。

    只更新 indicators 與 analysis_is_final，不動 action_tag/final_verdict。
    """
    yesterday = date.today() - timedelta(days=1)
    row = db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == yesterday,
        )
    ).scalar_one_or_none()

    if row is None or row.analysis_is_final:
        return

    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="3mo", interval="1d")
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
            SET indicators        = CAST(:indicators AS jsonb),
                analysis_is_final = TRUE,
                updated_at        = NOW()
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
