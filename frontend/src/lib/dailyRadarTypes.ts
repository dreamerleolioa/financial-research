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
  bucket_scores: DailyRadarBucketScores;
  score_breakdown: DailyRadarTracePayload;
  input_snapshot: DailyRadarTracePayload;
  data_dates: DailyRadarDateMap;
  matched_rules: DailyRadarMatchedRule[];
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
  bucket_scores: DailyRadarBucketScores;
  matched_rules: DailyRadarMatchedRule[];
  score_breakdown: DailyRadarTracePayload;
  input_snapshot: DailyRadarTracePayload;
  data_dates: DailyRadarDateMap;
}
