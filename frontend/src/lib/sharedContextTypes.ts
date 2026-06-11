export interface SharedContextTrace {
  context_type: string;
  source: Record<string, unknown>;
  as_of_date: string | null;
  freshness: "fresh" | "stale" | "missing" | "unknown" | string;
  missing_reason: string | null;
  replay_key: string;
  applicable_consumers: string[];
  payload: Record<string, unknown>;
}

export interface SharedContextCaveat {
  context_type: string;
  label: string;
  source: Record<string, unknown>;
  as_of_date: string | null;
  freshness: "fresh" | "stale" | "missing" | "unknown" | string;
  missing_reason: string | null;
  replay_key: string;
  applicable_consumers: string[];
}

export interface SharedContextDataQuality {
  status: "fresh" | "partial" | "missing" | "unknown" | string;
  freshness_counts: Record<string, number>;
  missing_reasons: string[];
  blocking: boolean;
}

export interface SharedContextReadPayload {
  version: string;
  symbol: string;
  consumer: "analyze" | "position_analysis" | string;
  reference_date: string | null;
  point_in_time: boolean;
  contexts: SharedContextTrace[];
  caveats: SharedContextCaveat[];
  data_quality: SharedContextDataQuality;
}
