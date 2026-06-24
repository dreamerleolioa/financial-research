from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    symbol: str = Field(default="2330.TW", min_length=1)
    news_text: str | None = None
    skip_ai: bool = False


class PositionAnalyzeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    entry_price: float = Field(gt=0)
    entry_date: str | None = None
    quantity: int | None = None


class PositionAnalysis(BaseModel):
    entry_price: float
    profit_loss_pct: float | None = None
    position_status: str | None = None
    position_narrative: str | None = None
    risk_state: str | None = None
    risk_state_label: str | None = None
    discipline_triggers: list[str] = Field(default_factory=list)
    observation_conditions: list[str] = Field(default_factory=list)
    risk_control_reference: dict[str, Any] | None = None
    command_language_deprecated: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str | None = None
    trailing_stop: float | None = None
    trailing_stop_reason: str | None = None
    exit_reason: str | None = None
    distance_to_trailing_stop_pct: float | None = None
    distance_to_support_pct: float | None = None
    unrealized_pnl: float | None = None
    holding_days: int | None = None


class TechnicalIndicators(BaseModel):
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    high_20d: float | None = None
    low_20d: float | None = None
    high_60d: float | None = None
    low_60d: float | None = None
    bollinger_upper: float | None = None
    bollinger_mid: float | None = None
    bollinger_lower: float | None = None
    bollinger_bandwidth: float | None = None
    bollinger_position: str | None = None
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    macd_bias: str | None = None
    kd_k: float | None = None
    kd_d: float | None = None
    kd_signal: str | None = None
    kd_zone: str | None = None
    adx: float | None = None
    adx_trend_strength: str | None = None
    adx_trend_direction: str | None = None
    obv: float | None = None
    obv_signal: str | None = None
    obv_trend_20d: str | None = None
    obv_trend_mid_long: str | None = None
    obv_trend_mid_long_window: str | None = None
    atr: float | None = None
    atr_pct: float | None = None
    volatility_level: str | None = None
    mfi: float | None = None
    mfi_signal: str | None = None
    donchian_upper: float | None = None
    donchian_lower: float | None = None
    donchian_mid: float | None = None
    donchian_width_pct: float | None = None
    donchian_position: str | None = None


class AnalyzeResponse(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)
    symbol_name: str | None = None
    analysis: str = ""
    analysis_detail: dict[str, Any] | None = None
    cleaned_news: dict[str, Any] | None = None
    confidence_score: int | None = None
    cross_validation_note: str | None = None
    strategy_type: str | None = None
    entry_zone: str | None = None
    stop_loss: str | None = None
    holding_period: str | None = None
    cleaned_news_quality: dict[str, Any] | None = None
    news_display: dict[str, Any] | None = None
    news_display_items: list[dict[str, Any]] = Field(default_factory=list)
    data_confidence: int | None = None
    signal_confidence: int | None = None
    action_plan_tag: str | None = None
    institutional_flow_label: str | None = None
    sentiment_label: str | None = None
    action_plan: dict[str, Any] | None = None
    risk_state: str | None = None
    risk_state_label: str | None = None
    discipline_triggers: list[str] = Field(default_factory=list)
    observation_conditions: list[str] = Field(default_factory=list)
    risk_control_reference: dict[str, Any] | None = None
    command_language_deprecated: dict[str, Any] = Field(default_factory=dict)
    data_sources: list[str] = Field(default_factory=list)
    fundamental_data: dict[str, Any] | None = None
    position_analysis: PositionAnalysis | None = None
    shared_context: dict[str, Any] | None = None
    chip_stability_context: dict[str, Any] | None = None
    phase1_observation: dict[str, Any] | None = None
    technical_indicators: TechnicalIndicators | None = None
    technical_profile: dict[str, Any] | None = None
    is_final: bool = True
    intraday_disclaimer: str | None = None
    strategy_version: str | None = None

    class ErrorDetail(BaseModel):
        code: str
        message: str

    errors: list[ErrorDetail] = Field(default_factory=list)


class CachedAnalyzeResponse(BaseModel):
    symbol: str
    signal_confidence: float | None
    action_tag: str | None
    recommended_action: str | None
    final_verdict: str | None
    is_final: bool
    intraday_disclaimer: Optional[str] = None
    strategy_version: str | None = None


class FetchRawDataRequest(BaseModel):
    symbol: str
    date: str = "today"


class HistoryEntry(BaseModel):
    record_date: str
    signal_confidence: float | None
    action_tag: str | None
    prev_action_tag: str | None
    prev_confidence: float | None
    analysis_is_final: bool
    indicators: dict[str, Any] | None
    final_verdict: str | None
