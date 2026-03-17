"""generate_strategy：純 rule-based 策略建議生成器。

不呼叫 LLM，依據技術指標數值與籌碼標籤，以 evidence-based scoring 輸出策略建議，
供 strategy_node 使用。
"""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class EvidenceScores:
    """四組證據分數。"""
    technical: int = 0
    flow: int = 0
    sentiment: int = 0
    risk_penalty: int = 0
    signal_conflict: bool = False

    @property
    def total(self) -> int:
        return self.technical + self.flow + self.sentiment + self.risk_penalty


def _compute_evidence_scores(
    *,
    bias: float | None,
    rsi: float | None,
    close: float | None,
    ma5: float | None,
    ma20: float | None,
    sentiment_label: str | None,
    flow_label: str | None,
) -> EvidenceScores:
    """計算四組 evidence score，供策略分類使用。"""
    scores = EvidenceScores()

    # ── 1. technical_evidence ──────────────────────────────────
    if _is_bullish_ma_alignment(close=close, ma5=ma5, ma20=ma20):
        scores.technical += 2
    if rsi is not None:
        if 45 <= rsi <= 65:
            scores.technical += 1
        elif rsi < 30:
            # 超賣且非明顯強弱趨勢 → 反彈候選
            scores.technical += 1
    if bias is not None and bias > 10:
        scores.technical -= 2

    # ── 2. flow_evidence ──────────────────────────────────────
    if flow_label == "institutional_accumulation":
        scores.flow += 2
    elif flow_label == "distribution":
        scores.flow -= 2
    # neutral → 0

    # ── 3. sentiment_evidence ─────────────────────────────────
    if sentiment_label == "positive":
        scores.sentiment += 1
    elif sentiment_label == "negative":
        scores.sentiment -= 1

    # ── 4. risk_penalty ───────────────────────────────────────
    # 訊號衝突：正面情緒 + 法人出貨
    if sentiment_label == "positive" and flow_label == "distribution":
        scores.risk_penalty -= 2
        scores.signal_conflict = True

    # 價格過熱：bias 超高且 rsi 偏熱
    if bias is not None and bias > 15:
        scores.risk_penalty -= 1
    if rsi is not None and rsi > 70 and bias is not None and bias > 10:
        scores.risk_penalty -= 1

    return scores


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
            - evidence_scores: dict — 各組分數（供除錯 / 前端顯示）
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

    scores = _compute_evidence_scores(
        bias=bias,
        rsi=rsi,
        close=close,
        ma5=ma5,
        ma20=ma20,
        sentiment_label=sentiment_label,
        flow_label=flow_label,
    )

    strategy_type = _determine_strategy_from_scores(
        scores=scores,
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
        "evidence_scores": {
            "technical": scores.technical,
            "flow": scores.flow,
            "sentiment": scores.sentiment,
            "risk_penalty": scores.risk_penalty,
            "total": scores.total,
            "signal_conflict": scores.signal_conflict,
        },
    }


def _determine_strategy_from_scores(
    *,
    scores: EvidenceScores,
    bias: float | None,
    rsi: float | None,
    close: float | None,
    ma5: float | None,
    ma20: float | None,
    sentiment_label: str | None,
    flow_label: str | None,
) -> str:
    """依 evidence scores 與 hard rules 決定策略類型。

    強制防守（hard rules）優先，再依分數分層。
    """
    # ── Hard rules：強制 defensive_wait ──────────────────────
    # 訊號衝突時直接觀望
    if scores.signal_conflict:
        return STRATEGY_DEFENSIVE_WAIT

    # 嚴重過熱（bias > 10）直接觀望
    if bias is not None and bias > 10:
        return STRATEGY_DEFENSIVE_WAIT

    # ── Score-based classification ────────────────────────────
    total = scores.total

    # mid_term：分數高 + 無嚴重風險
    # 要求 flow 分數為正（法人積極）且均線多頭排列
    if (
        total >= 4
        and scores.flow > 0
        and _is_bullish_ma_alignment(close=close, ma5=ma5, ma20=ma20)
    ):
        return STRATEGY_MID_TERM

    # short_term：中等分數 或 超賣反彈機會
    # 原有條件：positive sentiment + RSI 超賣
    if sentiment_label == "positive" and rsi is not None and rsi < 30:
        return STRATEGY_SHORT_TERM

    # 分數中等（2-3）且非衝突 → 短線試單
    if total >= 2 and not scores.signal_conflict:
        return STRATEGY_SHORT_TERM

    # 預設觀望
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


def generate_action_plan(
    strategy_type: str,
    entry_zone: str,
    stop_loss: str,
    flow_label: str | None,
    confidence_score: int | None,
    resistance_20d: float | None = None,
    support_20d: float | None = None,
    data_confidence: int | None = None,
    is_final: bool = True,
    rsi: float | None = None,
    sentiment_label: str | None = None,
    bias: float | None = None,
    close: float | None = None,
    ma5: float | None = None,
    ma20: float | None = None,
) -> dict:
    """由 strategy_type / flow_label / confidence_score 推導 action_plan 各欄位。

    純 rule-based Python，不呼叫 LLM。
    包含 evidence-based 說明欄位：thesis_points、upgrade_triggers、
    downgrade_triggers、invalidation_conditions、conviction_level、suggested_position_size。
    """
    # ── conviction_level（初步，guardrails 後可降級）─────────
    conviction_level = _determine_conviction_level(
        strategy_type=strategy_type,
        confidence_score=confidence_score,
        data_confidence=data_confidence,
        is_final=is_final,
    )

    # ── suggested_position_size ───────────────────────────────
    suggested_position_size = _determine_position_size(
        strategy_type=strategy_type,
        conviction_level=conviction_level,
        data_confidence=data_confidence,
        is_final=is_final,
    )

    # ── action text ───────────────────────────────────────────
    if strategy_type == "defensive_wait":
        action = "觀望（待訊號明確再試單）"
    elif strategy_type == "mid_term":
        action = f"分批佈局（首筆 {suggested_position_size}）"
    else:  # short_term
        action = f"短線試單（首筆 {suggested_position_size}，確認站穩再加碼）"

    # ── breakeven_note ────────────────────────────────────────
    breakeven_note = (
        "當帳面獲利達 5% 時，建議停損位上移至入場成本價" if strategy_type == "mid_term" else None
    )

    # ── momentum_expectation ──────────────────────────────────
    if flow_label == "institutional_accumulation":
        base = "強（法人集結中）"
        if resistance_20d is not None:
            momentum = f"{base}；若突破 {resistance_20d:.1f} 壓力則動能轉強"
        else:
            momentum = base
    elif flow_label == "distribution":
        base = "弱（法人出貨中）"
        if support_20d is not None:
            momentum = f"{base}；若跌破 {support_20d:.1f} 支撐則轉向 Bearish"
        else:
            momentum = base
    else:
        base = "中性"
        if resistance_20d is not None and support_20d is not None:
            momentum = f"{base}；若突破 {resistance_20d:.1f} 則動能轉強，若跌破 {support_20d:.1f} 則轉弱"
        else:
            momentum = base

    # ── thesis_points ─────────────────────────────────────────
    thesis_points = _build_thesis_points(
        flow_label=flow_label,
        sentiment_label=sentiment_label,
        rsi=rsi,
        bias=bias,
        close=close,
        ma5=ma5,
        ma20=ma20,
    )

    # ── upgrade / downgrade / invalidation triggers ───────────
    upgrade_triggers = _build_upgrade_triggers(
        strategy_type=strategy_type,
        flow_label=flow_label,
        resistance_20d=resistance_20d,
    )
    downgrade_triggers = _build_downgrade_triggers(
        strategy_type=strategy_type,
        flow_label=flow_label,
        support_20d=support_20d,
        ma20=ma20,
    )
    invalidation_conditions = _build_invalidation_conditions(
        support_20d=support_20d,
        ma20=ma20,
        flow_label=flow_label,
    )

    return {
        "action": action,
        "target_zone": entry_zone,
        "defense_line": stop_loss,
        "momentum_expectation": momentum,
        "breakeven_note": breakeven_note,
        "conviction_level": conviction_level,
        "thesis_points": thesis_points,
        "upgrade_triggers": upgrade_triggers,
        "downgrade_triggers": downgrade_triggers,
        "invalidation_conditions": invalidation_conditions,
        "suggested_position_size": suggested_position_size,
    }


def _determine_conviction_level(
    *,
    strategy_type: str,
    confidence_score: int | None,
    data_confidence: int | None,
    is_final: bool,
) -> str:
    """依策略類型 + 信心分數 + 資料品質 + 盤中/收盤 決定 conviction_level。

    Guardrails：
    - confidence_score < 60 → 不得高於 low
    - data_confidence < 60 → 不得高於 low
    - is_final = False（盤中）→ 最積極只能到 medium
    - defensive_wait → 固定 low
    """
    if strategy_type == "defensive_wait":
        return "low"

    # 基礎 conviction
    if strategy_type == "mid_term":
        base = "high"
    else:  # short_term
        base = "medium"

    # Guardrail：信心不足 → 降到 low
    if confidence_score is not None and confidence_score < 60:
        base = "low"
    if data_confidence is not None and data_confidence < 60:
        base = "low"

    # Guardrail：盤中 → 最高 medium
    if not is_final and base == "high":
        base = "medium"

    return base


def _determine_position_size(
    *,
    strategy_type: str,
    conviction_level: str,
    data_confidence: int | None,  # noqa: ARG001 — guardrail 已在 conviction 降級時體現
    is_final: bool,  # noqa: ARG001 — guardrail 已在 conviction 降級時體現
) -> str:
    """依 strategy_type + conviction_level + guardrails 決定建議首筆部位大小。

    data_confidence < 60 與 is_final=False 的 guardrail 均已在
    _determine_conviction_level 的降級邏輯中體現，此處不需重複判斷。
    """
    if strategy_type == "defensive_wait":
        return "0%"

    if strategy_type == "mid_term":
        if conviction_level == "high":
            return "20-30%"
        elif conviction_level == "medium":
            return "10-20%"
        else:
            return "10%"
    else:  # short_term
        if conviction_level == "high":
            return "20%"
        elif conviction_level == "medium":
            return "10-20%"
        else:
            return "10%"


def _build_thesis_points(
    *,
    flow_label: str | None,
    sentiment_label: str | None,
    rsi: float | None,
    bias: float | None,
    close: float | None,
    ma5: float | None,
    ma20: float | None,
) -> list[str]:
    """從已知訊號建立 2-4 條支持理由。"""
    points: list[str] = []

    if flow_label == "institutional_accumulation":
        points.append("法人籌碼偏多（持續吸籌）")
    elif flow_label == "distribution":
        points.append("法人籌碼偏空（出貨中）")

    if _is_bullish_ma_alignment(close=close, ma5=ma5, ma20=ma20):
        points.append("均線維持多頭排列（close > MA5 > MA20）")
    elif close is not None and ma20 is not None and close < ma20:
        points.append("價格跌破 MA20，短線趨勢偏弱")

    if sentiment_label == "positive":
        points.append("新聞情緒偏正向")
    elif sentiment_label == "negative":
        points.append("新聞情緒偏負向")

    if rsi is not None:
        if rsi < 30:
            points.append(f"RSI 處於超賣區間（{rsi:.1f}），具反彈潛力")
        elif 45 <= rsi <= 65:
            points.append(f"RSI 處於健康動能區（{rsi:.1f}）")
        elif rsi > 70:
            points.append(f"RSI 偏高（{rsi:.1f}），追價風險較大")

    if bias is not None and bias > 10:
        points.append(f"乖離率偏高（{bias:.1f}%），建議等待拉回")

    if not points:
        return ["資料不足，無法列出具體支持理由"]

    return points[:4]  # 最多 4 條


def _build_upgrade_triggers(
    *,
    strategy_type: str,
    flow_label: str | None,
    resistance_20d: float | None,
) -> list[str]:
    """建立升級觸發條件。"""
    triggers: list[str] = []

    if resistance_20d is not None:
        triggers.append(f"突破近 20 日壓力（{resistance_20d:.1f}）且量能同步放大")
    else:
        triggers.append("突破近期壓力且量能同步放大")

    if flow_label != "institutional_accumulation":
        triggers.append("法人轉為持續買超（連續 3 日以上）")

    if strategy_type == "defensive_wait":
        triggers.append("訊號衝突解除，技術指標回歸健康區間")

    return triggers


def _build_downgrade_triggers(
    *,
    strategy_type: str,
    flow_label: str | None,
    support_20d: float | None,
    ma20: float | None,
) -> list[str]:
    """建立降級觸發條件。"""
    triggers: list[str] = []

    if ma20 is not None:
        triggers.append(f"跌破 MA20（{ma20:.1f}）")
    else:
        triggers.append("跌破 MA20")

    if flow_label != "distribution":
        triggers.append("法人轉賣超（出貨訊號出現）")

    if support_20d is not None:
        triggers.append(f"跌破近 20 日支撐（{support_20d:.1f}）")

    return triggers


def _build_invalidation_conditions(
    *,
    support_20d: float | None,
    ma20: float | None,
    flow_label: str | None,
) -> list[str]:
    """建立失效條件：出現任一條件代表原判斷失效，需重新評估。"""
    conditions: list[str] = []

    if support_20d is not None:
        conditions.append(f"跌破近 20 日支撐（{support_20d:.1f}）")
    else:
        conditions.append("跌破近期重要支撐")

    if ma20 is not None:
        conditions.append(f"RSI 快速轉弱且價格失守 MA20（{ma20:.1f}）")
    else:
        conditions.append("RSI 快速轉弱且價格失守 MA20")

    if flow_label == "institutional_accumulation":
        conditions.append("法人由買超轉為持續賣超")

    return conditions


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
