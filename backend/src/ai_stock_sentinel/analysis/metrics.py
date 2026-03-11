"""純數學計算工具，不依賴任何專案內模組（無循環引用風險）。"""
from __future__ import annotations


def ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def calc_bias(close: float, ma: float) -> float | None:
    """BIAS = (close - MA) / MA * 100"""
    if ma == 0:
        return None
    return (close - ma) / ma * 100


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI 標準公式（Wilder 平均法）。資料不足時回傳 None。"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
