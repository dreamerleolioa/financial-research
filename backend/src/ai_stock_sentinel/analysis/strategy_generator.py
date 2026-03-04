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

# 停損描述（固定字串）
_STOP_LOSS = "近20日低點 - 3% 或跌破 MA60（取較寬者）"


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

    entry_zone = _determine_entry_zone(bias=bias)
    holding_period = _HOLDING_PERIOD_MAP[strategy_type]

    return {
        "strategy_type": strategy_type,
        "entry_zone": entry_zone,
        "stop_loss": _STOP_LOSS,
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


def _determine_entry_zone(*, bias: float | None) -> str:
    """依 bias 決定進場區間描述。"""
    if bias is not None and bias > 5:
        return "拉回 MA20 分批佈局"
    return "現價附近分批買進"
