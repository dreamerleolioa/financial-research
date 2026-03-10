from __future__ import annotations

from typing import Any, TypedDict

from ai_stock_sentinel.models import AnalysisDetail


class GraphState(TypedDict):
    symbol: str
    news_content: str | None
    snapshot: dict[str, Any] | None
    analysis: str | None
    cleaned_news: dict[str, Any] | None
    raw_news_items: list[dict[str, Any]] | None
    data_sufficient: bool
    retry_count: int
    errors: list[dict[str, str]]
    requires_news_refresh: bool
    requires_fundamental_update: bool
    technical_context: str | None
    institutional_context: str | None
    institutional_flow: dict[str, Any] | None
    strategy_type: str | None
    entry_zone: str | None
    stop_loss: str | None
    holding_period: str | None
    confidence_score: int | None
    cross_validation_note: str | None
    analysis_detail: AnalysisDetail | None
    cleaned_news_quality: dict[str, Any] | None
    news_display: dict[str, Any] | None
    news_display_items: list[dict[str, Any]]
    data_confidence: int | None
    signal_confidence: int | None
    high_20d: float | None
    low_20d: float | None
    support_20d: float | None
    resistance_20d: float | None
    rsi14: float | None
    action_plan_tag: str | None
    action_plan: dict[str, Any] | None
    fundamental_data: dict[str, Any] | None
    fundamental_context: str | None

    # --- Position Diagnosis (POST /analyze/position) ---
    entry_price: float | None
    entry_date: str | None
    quantity: int | None

    # preprocess_node calculates these (rule-based, not LLM)
    profit_loss_pct: float | None
    cost_buffer_to_support: float | None
    position_status: str | None          # profitable_safe | at_risk | under_water
    position_narrative: str | None

    # strategy_node populates these for position mode
    trailing_stop: float | None
    trailing_stop_reason: str | None
    recommended_action: str | None       # Hold | Trim | Exit
    exit_reason: str | None
