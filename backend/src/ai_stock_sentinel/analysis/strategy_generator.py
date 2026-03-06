"""generate_strategy：純 rule-based 策略建議生成器。

不呼叫 LLM，依據技術指標數值與籌碼標籤，以固定規則輸出策略建議，
供 strategy_node 使用。
"""
from __future__ import annotations

from typing import Any

# 策略類型常數
STRATEGY_SHORT_TERM = "short_term"
STRATEGY_MID_TERM = "mid_term"
STRATEGY_DEFENSIVE_WAIT = "defensive_wait"

# 持倉期間對應
_HOLDING_PERIOD_MAP: dict[str, str] = {
    STRATEGY_SHORT_TERM: "1-2 週",
    STRATEGY_MID_TERM: "1-3 個月",
    STRATEGY_DEFENSIVE_WAIT: "觀望",
}

def generate_strategy(
    technical_context_data: dict[str, Any],
    inst_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """純 rule-based 策略建議生成。

    Args:
        technical_context_data: 含技術指標的 dict，key 包含：
            - bias: float | None — BIAS %（乖離率）from MA20
            - rsi: float | None — RSI 值
            - close: float | None — 最新收盤價
            - ma5: float | None
            - ma20: float | None
            - ma60: float | None
            - support_20d: float | None — 近20日支撐位
            - low_20d: float | None — 近20日最低收盤價
            - sentiment_label: str | None — "positive"/"negative"/"neutral"/None
        inst_data: 籌碼 dict，key 包含 flow_label 等，或 None。

    Returns:
        含以下 key 的 dict：
            - strategy_type: "short_term" | "mid_term" | "defensive_wait"
            - entry_zone: str
            - stop_loss: str
            - holding_period: "1-2 週" | "1-3 個月" | "觀望"
    """
    bias: float | None = technical_context_data.get("bias")
    rsi: float | None = technical_context_data.get("rsi")
    close: float | None = technical_context_data.get("close")
    ma5: float | None = technical_context_data.get("ma5")
    ma20: float | None = technical_context_data.get("ma20")
    ma60: float | None = technical_context_data.get("ma60")
    support_20d: float | None = technical_context_data.get("support_20d")
    low_20d: float | None = technical_context_data.get("low_20d")
    sentiment_label: str | None = technical_context_data.get("sentiment_label")

    flow_label: str | None = inst_data.get("flow_label") if inst_data else None

    strategy_type = _determine_strategy(
        bias=bias,
        rsi=rsi,
        close=close,
        ma5=ma5,
        ma20=ma20,
        sentiment_label=sentiment_label,
        flow_label=flow_label,
    )

    entry_zone = _determine_entry_zone(bias=bias, support_20d=support_20d, ma20=ma20)
    stop_loss = _determine_stop_loss(low_20d=low_20d, ma60=ma60)
    holding_period = _HOLDING_PERIOD_MAP[strategy_type]

    return {
        "strategy_type": strategy_type,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "holding_period": holding_period,
    }


def _determine_strategy(
    *,
    bias: float | None,
    rsi: float | None,
    close: float | None,
    ma5: float | None,
    ma20: float | None,
    sentiment_label: str | None,
    flow_label: str | None,
) -> str:
    """依優先順序判斷策略類型，第一個符合的規則勝出。"""

    # 優先規則 1：defensive_wait 條件
    if bias is not None and bias > 10:
        return STRATEGY_DEFENSIVE_WAIT

    if sentiment_label == "positive" and flow_label == "distribution":
        return STRATEGY_DEFENSIVE_WAIT

    # 規則 2：short_term 條件
    if sentiment_label == "positive" and rsi is not None and rsi < 30:
        return STRATEGY_SHORT_TERM

    # 規則 3：mid_term 條件
    if flow_label == "institutional_accumulation":
        if _is_bullish_ma_alignment(close=close, ma5=ma5, ma20=ma20):
            return STRATEGY_MID_TERM

    # 預設
    return STRATEGY_DEFENSIVE_WAIT


def _is_bullish_ma_alignment(
    *,
    close: float | None,
    ma5: float | None,
    ma20: float | None,
) -> bool:
    """判斷是否滿足多頭均線排列：close > ma5 > ma20。

    只有三者均不為 None 時才進行比較；任一為 None 則回傳 False。
    """
    if close is None or ma5 is None or ma20 is None:
        return False
    return close > ma5 > ma20


def _determine_entry_zone(
    *,
    bias: float | None,
    support_20d: float | None = None,
    ma20: float | None = None,
) -> str:
    """依 support_20d / ma20 / bias 決定進場區間，優先使用實際價格。"""
    if support_20d is not None and ma20 is not None:
        if bias is not None and bias > 5:
            return f"拉回 MA20（{ma20:.1f}）附近分批佈局"
        return f"{support_20d:.1f}–{ma20:.1f}（support_20d ~ MA20）"
    if ma20 is not None and bias is not None and bias > 5:
        return f"拉回 MA20（{ma20:.1f}）附近分批佈局"
    if bias is not None and bias > 5:
        return "拉回 MA20 分批佈局"
    if support_20d is None and ma20 is None:
        return "資料不足，建議參考現價 +/- 5%"
    return "現價附近分批買進"


def _determine_stop_loss(
    *,
    low_20d: float | None,
    ma60: float | None,
) -> str:
    """依 low_20d / ma60 決定停損位，優先使用實際價格。"""
    if low_20d is not None and ma60 is not None:
        return f"{low_20d * 0.97:.1f}（近20日低點×0.97）或跌破 MA60（{ma60:.1f}），取較寬者"
    if low_20d is not None:
        return f"{low_20d * 0.97:.1f}（近20日低點×0.97）"
    return "近20日低點 - 3%（位階資料不足，以描述性規則代替）"


def calculate_action_plan_tag(
    rsi14: float | None,
    flow_label: str | None,
    confidence_score: int | None,
) -> str:
    """純 rule-based，依固定優先序判斷行動建議燈號。

    任一輸入為 None → 降級為 "neutral"。

    opportunity：rsi14 < 30 AND flow_label = "institutional_accumulation" AND confidence_score > 70
    overheated ：rsi14 > 70 AND flow_label = "distribution"
    neutral    ：其餘（含部分命中）
    """
    if rsi14 is None or flow_label is None or confidence_score is None:
        return "neutral"
    if rsi14 < 30 and flow_label == "institutional_accumulation" and confidence_score > 70:
        return "opportunity"
    if rsi14 > 70 and flow_label == "distribution":
        return "overheated"
    return "neutral"
