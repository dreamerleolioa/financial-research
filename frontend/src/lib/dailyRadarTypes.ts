export const DAILY_RADAR_BUCKETS = [
  "institutional_accumulation",
  "price_volume_strengthening",
  "bottoming_reversal",
  "support_retest",
] as const;

export const DAILY_RADAR_RISK_LABELS = [
  "overextended",
  "flow_conflict",
  "margin_crowding",
  "market_weakness",
  "data_gap",
] as const;

export const DAILY_RADAR_REPEAT_STATUSES = ["new", "repeat", "upgraded", "cooled_down"] as const;

export type DailyRadarBucket = (typeof DAILY_RADAR_BUCKETS)[number];
export type DailyRadarRiskLabel = (typeof DAILY_RADAR_RISK_LABELS)[number];
export type DailyRadarRepeatStatus = (typeof DAILY_RADAR_REPEAT_STATUSES)[number];
export type DailyRadarResearchStatus = "trend_forming" | "waiting_for_consolidation" | "structure_watch" | "data_pending";
export type DailyRadarRunStatus = "completed" | "running" | "failed" | "stale_data";
export type DailyRadarDateMap = Record<string, string>;
export type DailyRadarTracePayload = Record<string, unknown>;
export type DailyRadarBucketScores = Partial<Record<DailyRadarBucket, number>>;

export interface DailyRadarPhase1AvwapAnchor {
  available?: boolean;
  anchor_date?: string | null;
  anchor_reason?: string | null;
  avwap?: number | null;
  snapshot_close?: number | null;
  distance_to_avwap_pct?: number | null;
  distance_basis?: string | null;
  source_granularity?: string;
  estimated?: boolean;
  [key: string]: unknown;
}

export interface DailyRadarPhase1AvwapContext {
  symbol: string;
  data_date: string;
  dataset: string;
  adjustment_mode: string;
  freshness: string;
  missing_reason?: string | null;
  source?: DailyRadarTracePayload;
  source_granularity?: string;
  anchors: Record<string, DailyRadarPhase1AvwapAnchor>;
  applicable_consumers?: string[];
  data_quality?: DailyRadarTracePayload;
  [key: string]: unknown;
}

export interface DailyRadarSignalEvidence {
  evidence_type: string;
  source: DailyRadarTracePayload;
  as_of_date?: string | null;
  freshness: string;
  missing_reason?: string | null;
  replay_key: string;
  applicable_consumers: string[];
  details: DailyRadarTracePayload;
}

export interface DailyRadarRelativeStrengthTrace {
  benchmark_symbol: string;
  lookback_days: number;
  candidate_return?: number | null;
  benchmark_return?: number | null;
  relative_value?: number | null;
  score: number;
  weight: number;
  freshness: string;
  missing_reason?: string | null;
  data_dates: DailyRadarDateMap;
  aligned_dates: string[];
  window_start?: string;
  window_end?: string;
  replay_key?: string;
}

export interface DailyRadarBackgroundContextLabel {
  context_type: string;
  label: string;
  source: DailyRadarTracePayload;
  as_of_date?: string | null;
  freshness: string;
  missing_reason?: string | null;
  replay_key: string;
  applicable_consumers: string[];
}

export interface DailyRadarMatchedRule {
  rule_id: string;
  label: string;
  details: DailyRadarTracePayload;
}

export interface DailyRadarCandidate {
  research_status?: DailyRadarResearchStatus;
  symbol: string;
  name: string;
  primary_bucket: DailyRadarBucket;
  secondary_buckets: DailyRadarBucket[];
  observation_score: number;
  risk_labels: DailyRadarRiskLabel[];
  repeat_status: DailyRadarRepeatStatus;
  explanation: string;
  scoring_version?: string | null;
  rule_version?: string | null;
  bucket_scores: DailyRadarBucketScores;
  score_breakdown: DailyRadarTracePayload;
  input_snapshot: DailyRadarTracePayload;
  data_dates: DailyRadarDateMap;
  matched_rules: DailyRadarMatchedRule[];
  background_context_labels: DailyRadarBackgroundContextLabel[];
}

export interface DailyRadarObservationStats {
  signal_date_count: number;
  selected_count: number;
  evaluated_observation_count: number;
  evaluated_signal_date_count: number;
  evaluated_distinct_symbol_count: number;
  missing_outcome_count: number;
  missing_diagnostic_count: number;
  immature_observation_count: number;
  excluded_repeat_count: number;
  ranking_pool_complete: boolean;
  skipped_validation_count: number;
  status_counts: Record<string, number>;
  missing_reasons: Record<string, number>;
  coverage_complete: boolean;
  confirmation_rate: number | null;
  invalidation_rate: number | null;
  mean_lead_trading_days: number | null;
  mean_waiting_max_adverse_excursion_pct: number | null;
  means_scope: string;
}

export interface DailyRadarValidationCohort {
  id: string;
  strategy: Record<string, string>;
  signal_start_date: string;
  signal_end_date: string;
  windows: Record<string, Record<string, DailyRadarObservationStats>>;
  pool_comparison?: Record<string, DailyRadarPoolComparison>;
  research_pool_comparison?: Record<string, DailyRadarResearchComparison>;
}

export interface DailyRadarPoolQualityStats {
  sample_count: number;
  evaluated_count: number;
  signal_date_count: number;
  distinct_symbol_count: number;
  missing_outcome_count: number;
  skipped_count: number;
  immature_count: number;
  missing_metric_count: number;
  benchmark_symbols: string[];
  coverage_complete: boolean;
  positive_excess_count: number;
  positive_excess_rate: number | null;
  median_excess_return_pct: number | null;
}

export interface DailyRadarPoolComparison {
  selected: DailyRadarPoolQualityStats;
  top_3: DailyRadarPoolQualityStats;
  top_5: DailyRadarPoolQualityStats;
  comparable_shadow: DailyRadarPoolQualityStats;
  population_scope: "observed_daily_comparable_pool";
  observed_positive_capture_share: number | null;
}

export interface DailyRadarValidationResponse {
  diagnostic_version: string;
  as_of_date: string;
  sample_start_date: string;
  sample_end_date: string;
  lookback_days: number;
  calendar_through_date: string | null;
  last_evaluated_date: string | null;
  default_cohort_id: string | null;
  cohorts: DailyRadarValidationCohort[];
}

export interface DailyRadarResearchQualityStats extends DailyRadarPoolQualityStats {
  median_return_pct: number | null;
  worst_decile_mean_return_pct: number | null;
  worst_adverse_excursion_pct: number | null;
  median_adverse_excursion_pct: number | null;
  risk_sample_count: number;
  missing_risk_count: number;
  missing_reasons: Record<string, number>;
}

export interface DailyRadarResearchConfidence {
  status: "estimated" | "insufficient_blocks" | "incomplete_coverage" | "calendar_missing" | "sparse_comparable_dates" | "strategy_unknown";
  method: "paired_daily_moving_block_bootstrap";
  level: number;
  block_trading_days: number;
  minimum_blocks: number;
  paired_date_count: number;
  effective_block_count: number | null;
  mean_difference_pct: number | null;
  lower_pct: number | null;
  upper_pct: number | null;
}

export interface DailyRadarResearchPoolComparison extends DailyRadarPoolComparison {
  selected: DailyRadarResearchQualityStats;
  top_3: DailyRadarResearchQualityStats;
  top_5: DailyRadarResearchQualityStats;
  comparable_shadow: DailyRadarResearchQualityStats;
  confidence: DailyRadarResearchConfidence;
}

export interface DailyRadarResearchComparison {
  validation_version: string;
  return_basis: "next_open";
  price_basis: "unadjusted_price";
  dividends_included: false;
  cost_model: "assumed_total_cost_percentage_points";
  last_evaluated_date: string | null;
  calendar_through_date?: string | null;
  cost_scenarios: Record<string, DailyRadarResearchPoolComparison>;
}

export interface DailyRadarRunResponse {
  run_date: string;
  status: DailyRadarRunStatus;
  data_dates: DailyRadarDateMap;
  market_context: DailyRadarTracePayload;
  pool_summary?: DailyRadarPoolSummary | null;
  candidates: DailyRadarCandidate[];
}

export interface DailyRadarPoolSummary {
  version: string;
  population_scope: "scored_raw_records";
  input_record_count: number;
  state_counts: Record<string, number>;
  duplicate_record_count: number;
  unclassified_record_count: number;
  comparable_shadow_count: number;
  eligibility_audit_shadow_count: number;
  reason_counts: Record<string, number>;
  discovery_summary?: {
    version: string;
    run_date: string;
    scanned_symbol_count: number;
    eligible_symbol_count: number;
    discovered_symbol_count: number;
    track_counts: Record<string, number>;
    excluded_reason_counts: Record<string, number>;
  } | null;
}

export interface DailyRadarSymbolHistoryItem {
  symbol: string;
  name: string;
  record_date: string;
  primary_bucket: DailyRadarBucket;
  secondary_buckets: DailyRadarBucket[];
  observation_score: number;
  risk_labels: DailyRadarRiskLabel[];
  repeat_status: DailyRadarRepeatStatus;
  scoring_version?: string | null;
  rule_version?: string | null;
  bucket_scores: DailyRadarBucketScores;
  matched_rules: DailyRadarMatchedRule[];
  score_breakdown: DailyRadarTracePayload;
  input_snapshot: DailyRadarTracePayload;
  data_dates: DailyRadarDateMap;
  background_context_labels: DailyRadarBackgroundContextLabel[];
}
