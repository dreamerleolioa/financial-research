export type ActiveEtfAction = "added" | "increased" | "decreased" | "removed";
export type ActiveEtfFundStatus = "ready" | "no_baseline" | "missing" | "single_source" | "source_conflict";
export type ActiveEtfVerificationStatus = "verified" | "single_source" | "conflict";

export interface ActiveEtfSourceEvidence {
  source_provider: string;
  source_url: string;
  data_date: string;
  fetched_at: string;
  payload_hash: string;
}

export interface ActiveEtfCoverageFund {
  fund_code: string;
  name: string;
  category: string | null;
  source_provider: string;
  source_url: string;
  status: ActiveEtfFundStatus;
  verification_status: ActiveEtfVerificationStatus | null;
  source_count: number;
  verification_reason: string | null;
  sources: ActiveEtfSourceEvidence[];
  data_date: string | null;
  previous_date: string | null;
  latest_data_date: string | null;
  fetched_at: string | null;
  change_count: number;
  common_scale_ratio: number | null;
}

export interface ActiveEtfChange {
  action: ActiveEtfAction;
  fund_code: string;
  fund_name: string;
  symbol: string;
  name: string;
  source_provider: string;
  source_url: string;
  verification_status: Exclude<ActiveEtfVerificationStatus, "conflict">;
  source_count: number;
  fetched_at: string;
  data_date: string;
  previous_date: string;
  current_shares: number;
  previous_shares: number;
  share_delta: number;
  share_delta_pct: number | null;
  current_weight_pct: number;
  previous_weight_pct: number;
  weight_delta_pct_points: number;
  relative_share_change_pct: number | null;
  likely_fund_scale_change: boolean;
}

export interface ActiveEtfConsensus {
  symbol: string;
  name: string;
  direction: "increase" | "decrease" | "mixed";
  fund_count: number;
  added_count: number;
  increased_count: number;
  decreased_count: number;
  removed_count: number;
}

export interface ActiveEtfDailyResponse {
  data_date: string;
  available_dates: string[];
  generated_at: string;
  expected_funds: number;
  covered_funds: number;
  summary: {
    changed_funds: number;
    changed_stocks: number;
    changed_rows: number;
    additions: number;
    increases: number;
    decreases: number;
    removals: number;
  };
  funds: ActiveEtfCoverageFund[];
  changes: ActiveEtfChange[];
  consensus: ActiveEtfConsensus[];
}
