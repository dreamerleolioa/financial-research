export interface PortfolioItem {
  id: number;
  symbol: string;
  entry_price: number;
  quantity: number;
  entry_date: string;
  notes: string | null;
}

export interface ClosedPortfolioItem {
  id: number;
  position_group_id: string;
  symbol: string;
  entry_price: number;
  quantity: number;
  entry_date: string;
  is_active: boolean;
  exit_date: string;
  exit_price: number;
  exit_quantity: number;
  exit_fees: number;
  exit_taxes: number;
  realized_pnl: number;
  realized_return_pct: number;
  holding_days: number;
  notes: string | null;
}

export interface TradeReviewDataQuality {
  status?: string;
  notes?: string[];
  insufficient_data?: string[];
  [key: string]: unknown;
}

export interface TradeReviewResultMetrics {
  entry_date?: string | null;
  exit_date?: string | null;
  entry_price?: number | null;
  exit_price?: number | null;
  holding_days?: number | null;
  realized_pnl?: number | null;
  realized_return_pct?: number | null;
  max_profit_pct?: number | null;
  max_drawdown_pct?: number | null;
  profit_giveback_pct?: number | null;
  entry_indicators?: Record<string, unknown>;
  exit_indicators?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TradeReviewSection {
  classification?: string;
  confidence?: string;
  market_regime?: string;
  supporting_signals?: string[];
  conflicting_signals?: string[];
  caveats?: string[];
  summary?: string;
  [key: string]: unknown;
}

export interface TradeReviewHoldingSection extends TradeReviewSection {
  detected_events?: Record<string, unknown>[];
  event_count?: number;
  risk_event_count?: number;
}

export interface TradeReviewUserReadableConclusion {
  overall_verdict: string;
  overall_verdict_label: string;
  one_sentence_reason: string;
  evidence: string[];
  next_time_rules: string[];
}

export interface TradeReviewResult {
  data_quality?: TradeReviewDataQuality;
  trade_result?: TradeReviewResultMetrics;
  user_readable_conclusion?: TradeReviewUserReadableConclusion;
  entry_review?: TradeReviewSection;
  holding_review?: TradeReviewHoldingSection;
  exit_review?: TradeReviewSection;
  operation_review?: TradeReviewSection;
  [key: string]: unknown;
}

export interface TradeReviewEvidencePayload {
  trade?: Record<string, unknown>;
  position_group_id?: string;
  path_metrics?: Record<string, unknown>;
  entry_indicators?: Record<string, unknown>;
  exit_indicators?: Record<string, unknown>;
  detected_events?: Record<string, unknown>[];
  data_quality?: TradeReviewDataQuality;
  source_data?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TradeReviewResponse {
  id: number;
  portfolio_id: number;
  user_id: number;
  position_group_id: string;
  symbol: string;
  review_version: string;
  review_result: TradeReviewResult;
  evidence_payload: TradeReviewEvidencePayload;
  llm_summary: string | null;
  created_at: string;
  updated_at: string;
}

export type ReasonCategory =
  | 'technical'
  | 'institutional_flow'
  | 'fundamental'
  | 'news'
  | 'risk_control'
  | 'plan_execution'
  | 'emotional'
  | 'record_correction'
  | 'not_recorded';

export type EntryReasonCode =
  | 'breakout_confirmation'
  | 'pullback_held_support'
  | 'pullback_held_ma20'
  | 'institutional_flow_strengthened'
  | 'fundamental_thesis_improved'
  | 'planned_scale_in'
  | 'averaging_down'
  | 'chasing_momentum'
  | 'manual_record_correction';

export type ExitReasonCode =
  | 'target_reached'
  | 'trailing_stop_hit'
  | 'support_broken'
  | 'ma20_lost'
  | 'institutional_flow_weakened'
  | 'fundamental_thesis_broken'
  | 'news_risk_increased'
  | 'risk_reduction'
  | 'profit_protection'
  | 'planned_scale_out'
  | 'stop_loss'
  | 'emotional_exit'
  | 'manual_record_correction';

export type PlanAdherence = 'yes' | 'partial' | 'no' | 'not_recorded';

export type DecisionConfidenceLevel = 'high' | 'medium' | 'low' | 'not_recorded';

export type PositionEventType =
  | 'initial_entry'
  | 'add_entry'
  | 'partial_exit'
  | 'full_exit'
  | 'manual_adjustment';

export type PositionEventSource =
  | 'synthetic_from_portfolio_row'
  | 'user_backfilled'
  | 'user_recorded_at_event_time'
  | 'manual_record_correction'
  | 'not_recorded';

export type PositionEventReasonCode = EntryReasonCode | ExitReasonCode | 'not_recorded';

export interface PositionEvent {
  id: number;
  position_group_id: string;
  symbol: string;
  event_type: PositionEventType;
  event_date: string;
  price: number;
  quantity: number;
  fees: number;
  taxes: number;
  source_portfolio_id: number | null;
  note: string | null;
  reason_category: ReasonCategory | null;
  reason_code: PositionEventReasonCode | null;
  plan_adherence: PlanAdherence | null;
  confidence_level: DecisionConfidenceLevel | null;
  source: PositionEventSource;
  data_quality_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface PositionGroupEventsResponse {
  position_group_id: string;
  symbol: string;
  events: PositionEvent[];
}

export interface LifecycleTextItem {
  text: string;
  source_refs: string[];
  [key: string]: unknown;
}

export interface PositionLifecycleDataQuality {
  status?: string;
  notes?: string[];
  insufficient_data?: string[];
  [key: string]: unknown;
}

export interface PositionLifecycleAverageEntryPoint {
  event_id?: number | null;
  date?: string | null;
  position_size?: number | null;
  average_entry_price?: number | null;
  [key: string]: unknown;
}

export interface PositionLifecycleRiskPoint {
  event_id?: number | null;
  date?: string | null;
  event_type?: PositionEventType | string | null;
  position_size?: number | null;
  capital_at_risk?: number | null;
  cost_basis?: number | null;
  [key: string]: unknown;
}

export interface PositionLifecycleMetrics {
  total_realized_pnl?: number | null;
  total_return_pct_on_weighted_cost?: number | null;
  max_position_size?: number | null;
  max_capital_at_risk?: number | null;
  average_entry_price_over_time?: PositionLifecycleAverageEntryPoint[];
  weighted_average_entry_price?: number | null;
  final_exit_date?: string | null;
  total_holding_days_from_first_entry?: number | null;
  active_exposure_days?: number | null;
  max_unrealized_profit_pct?: number | null;
  max_unrealized_drawdown_pct?: number | null;
  profit_giveback_pct?: number | null;
  [key: string]: unknown;
}

export interface PositionLifecycleEntrySequence {
  entry_count?: number | null;
  add_entry_count?: number | null;
  initial_entry_vs_ma20_pct?: number | null;
  each_add_entry_vs_ma20_pct?: (number | null)[];
  average_up_count?: number | null;
  average_down_count?: number | null;
  add_after_breakdown_count?: number | null;
  add_after_confirmation_count?: number | null;
  time_between_entries?: (number | null)[];
  price_distance_between_entries?: (number | null)[];
  [key: string]: unknown;
}

export interface PositionLifecycleExitSequence {
  exit_count?: number | null;
  partial_exit_count?: number | null;
  first_exit_return_pct?: number | null;
  final_exit_return_pct?: number | null;
  percentage_sold_before_peak?: number | null;
  percentage_sold_after_breakdown?: number | null;
  profit_protected_by_partial_exits?: number | null;
  residual_position_giveback_pct?: number | null;
  [key: string]: unknown;
}

export interface PositionLifecycleAdvancedInternal {
  planned_1r_amount?: number | null;
  realized_r_multiple?: number | null;
  mae_pct?: number | null;
  mae_r_multiple?: number | null;
  mfe_pct?: number | null;
  mfe_r_multiple?: number | null;
  mfe_capture_rate?: number | null;
  plan_adherence_score?: number | null;
  decision_quality_score?: number | null;
  capital_at_risk_by_event?: PositionLifecycleRiskPoint[];
  exposure_curve?: PositionLifecycleRiskPoint[];
  benchmark_relative_return_pct?: number | null;
  sector_relative_return_pct?: number | null;
  [key: string]: unknown;
}

export interface PositionLifecycleEventIndicatorSnapshot {
  event_key: string;
  event_id?: number | null;
  event_type?: PositionEventType | string | null;
  event_date?: string | null;
  ma20?: number | null;
  ma60?: number | null;
  rsi14?: number | null;
  volume_ratio?: number | null;
  event_price_vs_ma20_pct?: number | null;
  event_price_vs_ma60_pct?: number | null;
  market_regime?: string | null;
  [key: string]: unknown;
}

export interface PositionLifecycleEventFact {
  event_key: string;
  id?: number | null;
  event_type?: PositionEventType | string | null;
  event_date?: string | null;
  price?: number | null;
  quantity?: number | null;
  fees?: number | null;
  taxes?: number | null;
  reason_category?: ReasonCategory | string | null;
  reason_code?: PositionEventReasonCode | string | null;
  plan_adherence?: PlanAdherence | string | null;
  confidence_level?: DecisionConfidenceLevel | string | null;
  source?: PositionEventSource | string | null;
  data_quality_note?: string | null;
  [key: string]: unknown;
}

export interface PositionLifecycleDecisionContext {
  status?: DecisionContextStatus | string;
  has_plan?: boolean;
  source?: string | null;
  created_after_entry?: boolean | null;
  [key: string]: unknown;
}

export interface PositionLifecycleClassification {
  primary_label?: string;
  labels?: string[];
  tier?: string;
  reasons?: LifecycleTextItem[];
  caveats?: LifecycleTextItem[];
  source_refs?: string[];
  [key: string]: unknown;
}

export interface PositionLifecycleReview {
  classification?: PositionLifecycleClassification;
  overall_conclusion?: LifecycleTextItem;
  what_worked?: LifecycleTextItem[];
  what_needs_review?: LifecycleTextItem[];
  event_level_evidence?: LifecycleTextItem[];
  next_operation_rules?: LifecycleTextItem[];
  data_quality_notes?: LifecycleTextItem[];
  [key: string]: unknown;
}

export interface PositionLifecycleReviewResult {
  position_group_id?: string;
  symbol?: string;
  lifecycle_metrics?: PositionLifecycleMetrics;
  entry_sequence?: PositionLifecycleEntrySequence;
  exit_sequence?: PositionLifecycleExitSequence;
  advanced_internal?: PositionLifecycleAdvancedInternal;
  event_indicator_snapshots?: PositionLifecycleEventIndicatorSnapshot[];
  event_facts?: PositionLifecycleEventFact[];
  decision_context?: PositionLifecycleDecisionContext;
  data_quality?: PositionLifecycleDataQuality;
  lifecycle_review?: PositionLifecycleReview;
  [key: string]: unknown;
}

export interface PositionLifecycleReviewEvidencePayload {
  position_group_id?: string;
  symbol?: string;
  metrics?: {
    lifecycle?: PositionLifecycleMetrics;
    entry_sequence?: PositionLifecycleEntrySequence;
    exit_sequence?: PositionLifecycleExitSequence;
    advanced_internal?: PositionLifecycleAdvancedInternal;
    [key: string]: unknown;
  };
  events?: PositionLifecycleEventFact[];
  indicator_snapshots?: PositionLifecycleEventIndicatorSnapshot[];
  detected_events?: Record<string, unknown>[];
  market_regime_snapshots?: Record<string, unknown>[];
  source_data?: Record<string, unknown>;
  data_quality?: PositionLifecycleDataQuality;
  [key: string]: unknown;
}

export interface PositionLifecycleReviewResponse {
  id: number;
  user_id: number;
  position_group_id: string;
  symbol: string;
  review_version: string;
  review_result: PositionLifecycleReviewResult;
  evidence_payload: PositionLifecycleReviewEvidencePayload;
  llm_summary: string | null;
  created_at: string;
  updated_at: string;
}

export type LifecycleSetupType =
  | 'breakout'
  | 'pullback'
  | 'mean_reversion'
  | 'value_revaluation'
  | 'earnings_or_event'
  | 'momentum_continuation'
  | 'long_term_accumulation'
  | 'defensive_rebalance'
  | 'other';

export type PlannedHoldingPeriod = 'short_term' | 'swing' | 'medium_term' | 'long_term';

export type OperationPlanStatus = 'missing' | 'present';

export type DecisionContextStatus = 'insufficient' | 'present';

export interface PortfolioDecisionContextStatus {
  portfolio_id: number;
  position_group_id: string;
  symbol: string;
  has_operation_plan: boolean;
  operation_plan_status: OperationPlanStatus;
  missing_operation_plan: boolean;
  decision_context: DecisionContextStatus;
  source: string | null;
  created_after_entry: boolean | null;
  planned_invalidation_present: boolean;
}

export type PortfolioDecisionContextStatusMap = Record<string, PortfolioDecisionContextStatus>;
