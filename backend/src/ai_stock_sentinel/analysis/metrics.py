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


def stochastic_kd(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    k_period: int = 9,
    d_period: int = 3,
) -> dict[str, float | str | None] | None:
    """KD 隨機指標。資料不足或高低收序列未對齊時回傳 None。"""
    if len(closes) < k_period + d_period or len(highs) != len(closes) or len(lows) != len(closes):
        return None

    rsv_values: list[float] = []
    for idx in range(k_period - 1, len(closes)):
        high_window = highs[idx - k_period + 1 : idx + 1]
        low_window = lows[idx - k_period + 1 : idx + 1]
        highest = max(high_window)
        lowest = min(low_window)
        if highest == lowest:
            rsv_values.append(50.0)
        else:
            rsv_values.append((closes[idx] - lowest) / (highest - lowest) * 100)

    if len(rsv_values) < d_period:
        return None

    k_values: list[float] = []
    k_prev = 50.0
    for rsv in rsv_values:
        k_prev = (2 * k_prev + rsv) / 3
        k_values.append(k_prev)

    d_values: list[float] = []
    d_prev = 50.0
    for k_value in k_values:
        d_prev = (2 * d_prev + k_value) / 3
        d_values.append(d_prev)

    if len(k_values) < 2 or len(d_values) < 2:
        return None

    k_value = k_values[-1]
    d_value = d_values[-1]
    prev_k = k_values[-2]
    prev_d = d_values[-2]

    if prev_k <= prev_d and k_value > d_value:
        signal = "bullish_cross"
    elif prev_k >= prev_d and k_value < d_value:
        signal = "bearish_cross"
    else:
        signal = "neutral"

    if k_value <= 20 and d_value <= 25:
        zone = "oversold"
    elif k_value >= 80 and d_value >= 75:
        zone = "overbought"
    else:
        zone = "neutral"

    return {
        "k": k_value,
        "d": d_value,
        "prev_k": prev_k,
        "prev_d": prev_d,
        "kd_signal": signal,
        "kd_zone": zone,
    }


def adx(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    period: int = 14,
) -> dict[str, float | str | None] | None:
    """ADX 趨勢強度指標，回傳 ADX、+DI、-DI 與趨勢方向。"""
    if len(closes) < period * 2 or len(highs) != len(closes) or len(lows) != len(closes):
        return None

    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []

    for idx in range(1, len(closes)):
        high_diff = highs[idx] - highs[idx - 1]
        low_diff = lows[idx - 1] - lows[idx]
        plus_dm.append(high_diff if high_diff > low_diff and high_diff > 0 else 0.0)
        minus_dm.append(low_diff if low_diff > high_diff and low_diff > 0 else 0.0)
        true_ranges.append(max(
            highs[idx] - lows[idx],
            abs(highs[idx] - closes[idx - 1]),
            abs(lows[idx] - closes[idx - 1]),
        ))

    if len(true_ranges) < period:
        return None

    tr14 = sum(true_ranges[:period])
    plus14 = sum(plus_dm[:period])
    minus14 = sum(minus_dm[:period])
    dx_values: list[float] = []

    for idx in range(period, len(true_ranges)):
        tr14 = tr14 - tr14 / period + true_ranges[idx]
        plus14 = plus14 - plus14 / period + plus_dm[idx]
        minus14 = minus14 - minus14 / period + minus_dm[idx]

        if tr14 == 0:
            plus_di = 0.0
            minus_di = 0.0
        else:
            plus_di = 100 * plus14 / tr14
            minus_di = 100 * minus14 / tr14

        di_sum = plus_di + minus_di
        dx_values.append(0.0 if di_sum == 0 else abs(plus_di - minus_di) / di_sum * 100)

    if not dx_values:
        return None

    adx_value = sum(dx_values[:period]) / min(period, len(dx_values))
    for dx in dx_values[period:]:
        adx_value = (adx_value * (period - 1) + dx) / period

    trend_strength = "strong" if adx_value >= 25 else "weak" if adx_value < 20 else "neutral"
    direction = "bullish" if plus_di > minus_di else "bearish" if minus_di > plus_di else "neutral"
    return {
        "adx": adx_value,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "trend_strength": trend_strength,
        "trend_direction": direction,
    }


def obv(
    closes: list[float],
    volumes: list[float],
    lookback: int = 5,
) -> dict[str, float | str | None] | None:
    """OBV 能量潮，用來觀察量價是否確認或背離。"""
    if len(closes) < 2 or len(volumes) != len(closes):
        return None

    values = [0.0]
    for idx in range(1, len(closes)):
        if closes[idx] > closes[idx - 1]:
            values.append(values[-1] + volumes[idx])
        elif closes[idx] < closes[idx - 1]:
            values.append(values[-1] - volumes[idx])
        else:
            values.append(values[-1])

    compare_idx = max(0, len(values) - 1 - lookback)
    obv_delta = values[-1] - values[compare_idx]
    price_delta = closes[-1] - closes[compare_idx]

    if price_delta > 0 and obv_delta > 0:
        signal = "price_volume_confirm"
    elif price_delta > 0 and obv_delta <= 0:
        signal = "bearish_divergence"
    elif price_delta < 0 and obv_delta >= 0:
        signal = "bullish_divergence"
    elif price_delta < 0 and obv_delta < 0:
        signal = "price_volume_weak"
    else:
        signal = "neutral"

    return {
        "obv": values[-1],
        "obv_delta": obv_delta,
        "price_delta": price_delta,
        "obv_signal": signal,
    }
