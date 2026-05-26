"""純 rule-based 信心分數計算器。不呼叫 LLM。"""
from __future__ import annotations

from ai_stock_sentinel.analysis.metrics import ma as calc_ma

BASE_CONFIDENCE = 50


def derive_technical_score(
    closes: list[float],
    rsi: float | None,
    bias: float | None,
    macd_data: dict | None = None,
    bb: dict | None = None,
    kd_data: dict | None = None,
    adx_data: dict | None = None,
    obv_data: dict | None = None,
    atr_data: dict | None = None,
    mfi_data: dict | None = None,
    donchian_data: dict | None = None,
) -> int:
    """依據 RSI、BIAS、MA、MACD、布林通道、KD、ADX、OBV、MFI、Donchian 計算技術面信心分數。

    - 資料不足（< 20 根）→ 回傳 50
    - score 範圍仍夾在 -5 ~ +5，映射至 33 ~ 67（避免技術面單維過度放大）
    - 映射公式：round(50 + score * (17 / 5))
    """
    if len(closes) < 20:
        return 50

    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    close = closes[-1]

    score = 0

    # ── RSI 訊號 ──────────────────────────────────────────────
    if rsi is not None:
        if rsi >= 50:
            score += 1
        elif rsi <= 30:
            score -= 1

    # ── BIAS 訊號 ─────────────────────────────────────────────
    # >10 → 乖離過大（超買），偏空；<-10 → 乖離過深（超賣極端），偏空
    # 0 < bias <= 5 → 小幅正乖離，偏多；其餘中性
    if bias is not None:
        if bias > 10:
            score -= 1
        elif bias < -10:
            score -= 1
        elif 0 < bias <= 5:
            score += 1

    # ── MA 排列訊號 ───────────────────────────────────────────
    if ma5 is not None and ma20 is not None:
        if close > ma5 > ma20:
            score += 1
        elif ma5 < ma20:
            score -= 1

    # ── MACD 訊號 ─────────────────────────────────────────────
    if macd_data is not None:
        macd_line = macd_data.get("macd_line")
        macd_bias = macd_data.get("macd_bias")
        # 黃金交叉且零軸上方：偏多加分
        if macd_bias == "bullish" and macd_line is not None and macd_line > 0:
            score += 1
        # 死亡交叉且零軸下方：偏空扣分
        elif macd_bias == "bearish" and macd_line is not None and macd_line < 0:
            score -= 1

    # ── 布林通道訊號 ──────────────────────────────────────────
    if bb is not None:
        upper = bb.get("bollinger_upper")
        lower = bb.get("bollinger_lower")
        mid = bb.get("bollinger_mid")
        if upper is not None and lower is not None and mid is not None:
            band_range = upper - lower
            if band_range > 0:
                # 價格沿上軌且非極端爆離（95%-99% 區間）：小幅偏多
                if upper * 0.95 <= close < upper * 0.99:
                    score += 1
                # 價格有效跌破中軌並靠近下軌（低於 40% 分位）：偏空
                elif close < lower + band_range * 0.4:
                    score -= 1

                # 風險扣分：價格觸上軌且 RSI 過熱
                if close >= upper * 0.99 and rsi is not None and rsi > 70:
                    score -= 1
                # 反彈候選加分：價格觸下軌且 RSI 超賣（小幅，不直接翻多）
                elif close <= lower * 1.01 and rsi is not None and rsi < 30:
                    score += 1

    # ── KD 反轉訊號 ───────────────────────────────────────────
    if kd_data is not None:
        kd_signal = kd_data.get("kd_signal")
        kd_zone = kd_data.get("kd_zone")
        if kd_signal == "bullish_cross" and kd_zone == "oversold":
            score += 1
        elif kd_signal == "bearish_cross" and kd_zone == "overbought":
            score -= 1

    # ── OBV 量價確認 / 背離 ───────────────────────────────────
    if obv_data is not None:
        obv_signal = obv_data.get("obv_signal")
        if obv_signal == "price_volume_confirm":
            score += 1
        elif obv_signal in {"bearish_divergence", "price_volume_weak"}:
            score -= 1
        elif obv_signal == "bullish_divergence":
            score += 1

    # ── MFI 資金流 ───────────────────────────────────────────
    if mfi_data is not None:
        mfi_signal = mfi_data.get("mfi_signal")
        if mfi_signal in {"oversold", "bullish_flow"}:
            score += 1
        elif mfi_signal in {"overbought", "bearish_flow"}:
            score -= 1

    # ── Donchian 突破 / 跌破 ─────────────────────────────────
    if donchian_data is not None:
        donchian_position = donchian_data.get("donchian_position")
        if donchian_position == "breakout_up":
            score += 1
        elif donchian_position == "breakdown_down":
            score -= 1

    # ── ADX 趨勢強度濾網 ─────────────────────────────────────
    if adx_data is not None:
        trend_strength = adx_data.get("trend_strength")
        trend_direction = adx_data.get("trend_direction")
        if trend_strength == "strong":
            if trend_direction == "bullish":
                score += 1
            elif trend_direction == "bearish":
                score -= 1
        elif trend_strength == "weak":
            score = round(score * 0.7)

    # ── ATR 波動濾網 ─────────────────────────────────────────
    if atr_data is not None and atr_data.get("volatility_level") == "high":
        score = round(score * 0.85)

    clamped = max(-5, min(5, score))
    return round(50 + clamped * (17 / 5))


# Sentiment 分數對照
_SENTIMENT_SCORES = {
    "positive": +5,
    "negative": -5,
    "neutral": 0,
}

# Inst flow 分數對照
_INST_FLOW_SCORES = {
    "institutional_accumulation": +7,
    "distribution": -10,
    "retail_chasing": -8,
    "neutral": 0,
    "unknown": 0,  # 資料缺失時不貢獻分數，亦不觸發特殊情境
}

# Technical signal 分數對照
_TECH_SIGNAL_SCORES = {
    "bullish": +5,
    "bearish": -5,
    "sideways": 0,
}

# 三維共振 bonus
_THREE_RESONANCE_BONUS = +3

# 利多出貨 penalty（positive + distribution 的額外懲罰）
_BULLISH_DISTRIBUTION_PENALTY = -7


def adjust_confidence_by_divergence(
    base_score: int,
    news_sentiment: str,    # "positive" | "negative" | "neutral"
    inst_flow: str,         # "institutional_accumulation" | "distribution" | "retail_chasing" | "neutral"
    technical_signal: str,  # "bullish" | "bearish" | "sideways"
    sentiment_strength: float = 1.0,
) -> tuple[int, str]:
    """多維加權模型。回傳 (adjusted_score, cross_validation_note)。

    各維度獨立評分，加總後套用特殊情境加成/懲罰：
    - 三維共振（positive + institutional_accumulation + bullish）→ 額外 +3
    - 利多出貨（positive + distribution）→ 額外 -7
    """
    s = round(_SENTIMENT_SCORES.get(news_sentiment, 0) * sentiment_strength)
    i = _INST_FLOW_SCORES.get(inst_flow, 0)
    t = _TECH_SIGNAL_SCORES.get(technical_signal, 0)
    adjustment = s + i + t

    note = ""

    # 特殊情境：三維共振
    if (
        news_sentiment == "positive"
        and inst_flow == "institutional_accumulation"
        and technical_signal == "bullish"
    ):
        adjustment += _THREE_RESONANCE_BONUS
        note = "三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高"

    # 特殊情境：利多出貨（加額外懲罰）
    elif news_sentiment == "positive" and inst_flow == "distribution":
        adjustment += _BULLISH_DISTRIBUTION_PENALTY
        note = "警示：基本面利多但法人同步出貨，疑似趁消息出貨，建議保守觀察"

    # 散戶追高提示
    elif inst_flow == "retail_chasing":
        note = "散戶追高風險：融資餘額異常激增，法人同步減碼，籌碼結構偏不健康"

    # 利空不跌提示
    elif news_sentiment == "negative" and technical_signal == "bullish":
        note = "利空不跌訊號：股價守穩支撐且技術偏強，逆勢佈局機會，需觀察持續性"

    score = max(0, min(100, base_score + adjustment))
    return score, note


def compute_confidence(
    base_score: int,
    news_sentiment: str,   # "positive" | "negative" | "neutral" | "unknown"
    inst_flow: str,        # "institutional_accumulation" | "distribution" | "retail_chasing" | "neutral" | "unknown"
    technical_signal: str, # "bullish" | "bearish" | "sideways" | "unknown"
    date_unknown: bool = False,  # DATE_UNKNOWN 旗標：日期未知時額外扣 -3 分
    sentiment_strength: float = 1.0,
) -> dict[str, int | str]:
    """計算 data_confidence、signal_confidence 與 cross_validation_note。

    data_confidence 代表資料成功取得的維度比例（0 / 33 / 67 / 100）。
    只有 unknown 才視為未取得資料；neutral（新聞/籌碼）與 sideways（技術）均算有取得。

    注意：架構規格（v2.4）定義 technical_signal 不輸出 "unknown"（不足時降級為 "sideways"），
    因此 technical_signal != "unknown" 在現行架構永遠為 True，
    但保留此判斷作為防禦性設計，避免未來若新增 unknown 值時靜默影響計算。

    Returns:
        {
            "data_confidence": int,      # 資料完整度 0-100
            "signal_confidence": int,    # 訊號強度 0-100（= 舊 confidence_score）
            "cross_validation_note": str,
        }
    """
    # 資料完整度：只判斷「資料是否成功取得」，neutral/sideways 均算有取得
    data_available = sum([
        news_sentiment != "unknown",           # neutral 也算有取得
        inst_flow != "unknown",               # neutral 也算有取得
        technical_signal != "unknown",        # sideways 也算有取得；現行架構不會出現 unknown
    ])
    data_confidence = round(data_available / 3 * 100)

    signal_confidence, note = adjust_confidence_by_divergence(
        base_score,
        news_sentiment=news_sentiment,
        inst_flow=inst_flow,
        technical_signal=technical_signal,
        sentiment_strength=sentiment_strength,
    )

    # DATE_UNKNOWN 懲罰：在各維度加總後、clamp 前扣 -3（僅影響 signal_confidence）
    if date_unknown:
        signal_confidence = max(0, min(100, signal_confidence - 3))

    return {
        "data_confidence": data_confidence,
        "signal_confidence": signal_confidence,
        "cross_validation_note": note,
    }
