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

export interface DailyRadarRunResponse {
  run_date: string;
  status: DailyRadarRunStatus;
  data_dates: DailyRadarDateMap;
  market_context: DailyRadarTracePayload;
  candidates: DailyRadarCandidate[];
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
