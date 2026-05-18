"""純數學計算工具，不依賴任何專案內模組（無循環引用風險）。"""
from __future__ import annotations


def ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def calc_bias(close: float, ma_val: float) -> float | None:
    """BIAS = (close - MA) / MA * 100"""
    if ma_val == 0:
        return None
    return (close - ma_val) / ma_val * 100


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


def ema(closes: list[float], period: int) -> list[float] | None:
    """指數移動平均（EMA）序列。資料不足時回傳 None。"""
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    result: list[float] = [sum(closes[:period]) / period]
    for price in closes[period:]:
        result.append(price * k + result[-1] * (1 - k))
    return result


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> dict[str, float | None] | None:
    """布林通道（20 日，±2σ）。資料不足時回傳 None。"""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = mid + num_std * std
    lower = mid - num_std * std
    bandwidth = (upper - lower) / mid if mid != 0 else None
    return {
        "bollinger_mid": mid,
        "bollinger_upper": upper,
        "bollinger_lower": lower,
        "bollinger_bandwidth": bandwidth,
    }


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, float | str | None] | None:
    """MACD (12/26 EMA，訊號線 9 EMA)。資料不足時回傳 None。"""
    if len(closes) < slow + signal_period:
        return None
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    if fast_ema is None or slow_ema is None:
        return None

    # 對齊長度：slow_ema 較短，取對應的 fast_ema 尾段
    len_diff = len(fast_ema) - len(slow_ema)
    aligned_fast = fast_ema[len_diff:]
    macd_line_series = [f - s for f, s in zip(aligned_fast, slow_ema)]

    if len(macd_line_series) < signal_period:
        return None

    signal_ema = ema(macd_line_series, signal_period)
    if signal_ema is None:
        return None

    macd_val = macd_line_series[-1]
    signal_val = signal_ema[-1]
    hist_val = macd_val - signal_val

    epsilon = 1e-10
    if hist_val > epsilon:
        bias = "bullish"
    elif hist_val < -epsilon:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "macd_line": macd_val,
        "macd_signal": signal_val,
        "macd_hist": hist_val,
        "macd_bias": bias,
    }
