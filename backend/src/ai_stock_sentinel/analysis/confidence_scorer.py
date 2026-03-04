"""純 rule-based 信心分數計算器。不呼叫 LLM。"""
from __future__ import annotations

BASE_CONFIDENCE = 50


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
