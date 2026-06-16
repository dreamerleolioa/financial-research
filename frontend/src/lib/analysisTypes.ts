import type { SharedContextReadPayload } from "./sharedContextTypes";

export interface AnalysisErrorDetail {
  code: string;
  message: string;
}

export interface AnalysisDetail {
  summary: string;
  risks: string[];
  technical_signal: "bullish" | "bearish" | "sideways";
  institutional_flow: string | null;
  sentiment_label: string | null;
  tech_insight: string | null;
  inst_insight: string | null;
  news_insight: string | null;
  final_verdict: string | null;
  fundamental_insight?: string | null;
  thought_process?: string | null;
}

export interface CleanedNewsQuality {
  quality_score: number;
  quality_flags: string[];
}

export interface NewsDisplayItem {
  title: string;
  date?: string | null;
  source_url?: string | null;
}

export interface TechnicalIndicators {
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  high_20d: number | null;
  low_20d: number | null;
  high_60d: number | null;
  low_60d: number | null;
  bollinger_upper: number | null;
  bollinger_mid: number | null;
  bollinger_lower: number | null;
  bollinger_bandwidth: number | null;
  bollinger_position: string | null;
  macd_line: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  macd_bias: string | null;
  kd_k: number | null;
  kd_d: number | null;
  kd_signal: string | null;
  kd_zone: string | null;
  adx: number | null;
  adx_trend_strength: string | null;
  adx_trend_direction: string | null;
  obv: number | null;
  obv_signal: string | null;
  obv_trend_20d: string | null;
  obv_trend_mid_long: string | null;
  obv_trend_mid_long_window: string | null;
  atr: number | null;
  atr_pct: number | null;
  volatility_level: string | null;
  mfi: number | null;
  mfi_signal: string | null;
  donchian_upper: number | null;
  donchian_lower: number | null;
  donchian_mid: number | null;
  donchian_width_pct: number | null;
  donchian_position: string | null;
}

export interface AnalyzeResponse {
  snapshot: Record<string, unknown>;
  symbol_name?: string | null;
  analysis: string;
  analysis_detail: AnalysisDetail | null;
  cleaned_news: Record<string, unknown> | null;
  cleaned_news_quality: CleanedNewsQuality | null;
  news_display_items: NewsDisplayItem[];
  confidence_score: number | null;
  cross_validation_note: string | null;
  strategy_type: "short_term" | "mid_term" | "defensive_wait" | null;
  entry_zone: string | null;
  stop_loss: string | null;
  holding_period: string | null;
  action_plan_tag: "opportunity" | "overheated" | "neutral" | null;
  technical_indicators?: TechnicalIndicators | null;
  action_plan: {
    action?: string | null;
    target_zone?: string | null;
    defense_line?: string | null;
    breakeven_note?: string | null;
    momentum_expectation?: string | null;
    conviction_level?: "low" | "medium" | "high" | null;
    thesis_points?: string[];
    invalidation_conditions?: string[];
    suggested_position_size?: string | null;
    upgrade_triggers?: string[];
    downgrade_triggers?: string[];
  } | null;
  risk_state?: string | null;
  risk_state_label?: string | null;
  discipline_triggers?: string[];
  observation_conditions?: string[];
  risk_control_reference?: {
    reference?: string | null;
    reference_type?: string | null;
  } | null;
  command_language_deprecated?: Record<string, unknown>;
  institutional_flow_label: string | null;
  data_confidence: number | null;
  is_final: boolean;
  intraday_disclaimer: string | null;
  errors: AnalysisErrorDetail[];
  fundamental_data?: {
    ttm_eps?: number | null;
    pe_current?: number | null;
    pe_band?: string | null;
    pe_percentile?: number | null;
    dividend_yield?: number | null;
    yield_signal?: string | null;
  } | null;
  shared_context?: SharedContextReadPayload | null;
}

export interface PositionAnalysis {
  entry_price: number;
  profit_loss_pct: number | null;
  position_status: "profitable_safe" | "at_risk" | "under_water" | null;
  position_narrative: string | null;
  risk_state?: "stable" | "watch" | "elevated" | "critical" | null;
  risk_state_label?: string | null;
  discipline_triggers?: string[];
  observation_conditions?: string[];
  risk_control_reference?: {
    reference_price?: number | null;
    reference_type?: string | null;
    reason?: string | null;
  } | null;
  command_language_deprecated?: Record<string, unknown>;
  recommended_action: "Hold" | "Trim" | "Exit" | null;
  trailing_stop: number | null;
  trailing_stop_reason: string | null;
  exit_reason: string | null;
}

export interface PositionResult {
  snapshot: { current_price?: number; [key: string]: unknown };
  position_analysis: PositionAnalysis | null;
  confidence_score: number | null;
  shared_context?: SharedContextReadPayload | null;
  analysis_detail: {
    technical_signal: string;
    institutional_flow: string | null;
    tech_insight: string | null;
    inst_insight: string | null;
    news_insight: string | null;
    final_verdict: string | null;
    summary: string;
  } | null;
}
