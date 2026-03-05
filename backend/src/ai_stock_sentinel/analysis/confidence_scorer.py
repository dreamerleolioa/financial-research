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


def adjust_confidence_by_divergence(
    base_score: int,
    news_sentiment: str,    # "positive" | "negative" | "neutral"
    inst_flow: str,         # "institutional_accumulation" | "distribution" | "retail_chasing" | "neutral"
    technical_signal: str,  # "bullish" | "bearish" | "sideways"
) -> tuple[int, str]:
    """純 rule-based，不呼叫 LLM。回傳 (adjusted_score, cross_validation_note)。

    規則優先序（第一個符合的勝出）：
    1. 三維共振：sentiment=positive + inst_flow=institutional_accumulation + technical=bullish → +15
    2. 利多出貨：sentiment=positive + inst_flow=distribution → -20
    3. 散戶追高：inst_flow=retail_chasing → -15
    4. 利空不跌：sentiment=negative + technical=bullish → +10
    5. 預設：adjustment=0，note=""
    """
    adjustment = 0
    note = ""

    # 規則 1：三維共振
    if (
        news_sentiment == "positive"
        and inst_flow == "institutional_accumulation"
        and technical_signal == "bullish"
    ):
        adjustment = 15
        note = "三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高"

    # 規則 2：利多出貨
    elif news_sentiment == "positive" and inst_flow == "distribution":
        adjustment = -20
        note = "警示：基本面利多但法人同步出貨，疑似趁消息出貨，建議保守觀察"

    # 規則 3：散戶追高
    elif inst_flow == "retail_chasing":
        adjustment = -15
        note = "散戶追高風險：融資餘額異常激增，法人同步減碼，籌碼結構偏不健康"

    # 規則 4：利空不跌
    elif news_sentiment == "negative" and technical_signal == "bullish":
        adjustment = 10
        note = "利空不跌訊號：股價守穩支撐且技術偏強，逆勢佈局機會，需觀察持續性"

    score = max(0, min(100, base_score + adjustment))
    return score, note
