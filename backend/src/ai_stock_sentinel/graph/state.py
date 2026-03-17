from __future__ import annotations

from typing import Any, TypedDict

from ai_stock_sentinel.models import AnalysisDetail


# ── 欄位分組（類型說明用，非 LangGraph 節點狀態） ──────────────────────────

class _NewsStateFields(TypedDict, total=False):
    """新聞相關欄位"""
    news_content: str | None
    cleaned_news: dict[str, Any] | None
    raw_news_items: list[dict[str, Any]] | None
    cleaned_news_quality: dict[str, Any] | None
    news_display: dict[str, Any] | None
    news_display_items: list[dict[str, Any]]


class _MarketDataStateFields(TypedDict, total=False):
    """市場數據與技術分析欄位"""
    snapshot: dict[str, Any] | None
    institutional_flow: dict[str, Any] | None
    fundamental_data: dict[str, Any] | None
    technical_context: str | None
    institutional_context: str | None
    fundamental_context: str | None
    high_20d: float | None
    low_20d: float | None
    support_20d: float | None
    resistance_20d: float | None
    rsi14: float | None
    ma5: float | None
    ma20: float | None
    ma60: float | None


class _AnalysisStateFields(TypedDict, total=False):
    """LLM 分析結果與信心分數欄位"""
    analysis: str | None
    analysis_detail: AnalysisDetail | None
    confidence_score: int | None
    cross_validation_note: str | None
    signal_confidence: int | None
    data_confidence: int | None
    strategy_type: str | None
    entry_zone: str | None
    stop_loss: str | None
    holding_period: str | None
    action_plan_tag: str | None
    action_plan: dict[str, Any] | None


class _PositionStateFields(TypedDict, total=False):
    """持倉診斷欄位（POST /analyze/position 使用）"""
    entry_price: float | None
    entry_date: str | None
    quantity: int | None
    profit_loss_pct: float | None
    cost_buffer_to_support: float | None
    position_status: str | None
    position_narrative: str | None
    trailing_stop: float | None
    trailing_stop_reason: str | None
    recommended_action: str | None
    exit_reason: str | None


# ── 實際 LangGraph 使用的狀態（平鋪，維持向後相容） ─────────────────────

class GraphState(TypedDict):
    # 基本控制欄位
    symbol: str
    data_sufficient: bool
    retry_count: int
    errors: list[dict[str, str]]
    requires_news_refresh: bool
    requires_fundamental_update: bool

    # 新聞相關（參見 _NewsStateFields）
    news_content: str | None
    cleaned_news: dict[str, Any] | None
    raw_news_items: list[dict[str, Any]] | None
    cleaned_news_quality: dict[str, Any] | None
    news_display: dict[str, Any] | None
    news_display_items: list[dict[str, Any]]

    # 市場數據（參見 _MarketDataStateFields）
    snapshot: dict[str, Any] | None
    institutional_flow: dict[str, Any] | None
    fundamental_data: dict[str, Any] | None
    technical_context: str | None
    institutional_context: str | None
    fundamental_context: str | None
    high_20d: float | None
    low_20d: float | None
    support_20d: float | None
    resistance_20d: float | None
    rsi14: float | None
    ma5: float | None
    ma20: float | None
    ma60: float | None

    # 分析結果（參見 _AnalysisStateFields）
    analysis: str | None
    analysis_detail: AnalysisDetail | None
    confidence_score: int | None
    cross_validation_note: str | None
    signal_confidence: int | None
    data_confidence: int | None
    strategy_type: str | None
    entry_zone: str | None
    stop_loss: str | None
    holding_period: str | None
    action_plan_tag: str | None
    action_plan: dict[str, Any] | None

    # 持倉診斷（參見 _PositionStateFields）
    entry_price: float | None
    entry_date: str | None
    quantity: int | None
    profit_loss_pct: float | None
    cost_buffer_to_support: float | None
    position_status: str | None
    position_narrative: str | None
    trailing_stop: float | None
    trailing_stop_reason: str | None
    recommended_action: str | None
    exit_reason: str | None

    # --- History Context (from stock_analysis_cache, injected before LLM call) ---
    prev_context: dict[str, Any] | None   # load_yesterday_context() 的回傳值

    # 分析時間戳記 — 由 API 層注入，strategy_node 讀取以套用盤中 guardrail
    is_final: bool
