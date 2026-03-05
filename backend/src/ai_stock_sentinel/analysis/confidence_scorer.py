"""純 rule-based 信心分數計算器。不呼叫 LLM。"""
from __future__ import annotations

BASE_CONFIDENCE = 50


def derive_technical_score(
    closes: list[float],
    rsi: float | None,
    bias: float | None,
) -> int:
    """依據 RSI、BIAS、MA 排列三個獨立訊號計算技術面信心分數。

    - 資料不足（< 20 根）→ 回傳 50
    - score 範圍 -3 ~ +3，映射至 30 ~ 70
    - 映射公式：round(50 + score * (20 / 3))
    """
    if len(closes) < 20:
        return 50

    # 在函式內 import，避免循環引用
    from ai_stock_sentinel.analysis.context_generator import ma as calc_ma

    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    close = closes[-1]

    score = 0

    # RSI 訊號
    if rsi is not None:
        if rsi >= 50:
            score += 1
        elif rsi <= 30:
            score -= 1

    # BIAS 訊號：
    # >10 → 乖離過大（超買），偏空；<-10 → 乖離過深（超賣極端），偏空
    # 0 < bias <= 5 → 小幅正乖離，偏多；其餘中性
    if bias is not None:
        if bias > 10:
            score -= 1
        elif bias < -10:
            score -= 1
        elif 0 < bias <= 5:
            score += 1

    # MA 排列訊號
    if ma5 is not None and ma20 is not None:
        if close > ma5 > ma20:
            score += 1
        elif ma5 < ma20:
            score -= 1

    clamped = max(-3, min(3, score))
    return round(50 + clamped * (20 / 3))


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
) -> tuple[int, str]:
    """多維加權模型。回傳 (adjusted_score, cross_validation_note)。

    各維度獨立評分，加總後套用特殊情境加成/懲罰：
    - 三維共振（positive + institutional_accumulation + bullish）→ 額外 +3
    - 利多出貨（positive + distribution）→ 額外 -7
    """
    s = _SENTIMENT_SCORES.get(news_sentiment, 0)
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
