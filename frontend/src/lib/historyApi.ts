import { requestJson } from "./apiClient";

export interface HistoryEntry {
  record_date: string;
  signal_confidence: number | null;
  action_tag: string | null;
  prev_action_tag: string | null;
  prev_confidence: number | null;
  analysis_is_final: boolean;
  indicators: Record<string, unknown> | null;
  final_verdict: string | null;
}

export async function fetchSymbolHistory(
  symbol: string,
  days: number = 30,
): Promise<HistoryEntry[]> {
  return requestJson<HistoryEntry[]>(`/history/${encodeURIComponent(symbol)}`, {
    query: { days },
  });
}
