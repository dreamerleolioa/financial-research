// frontend/src/lib/historyApi.ts

import { authHeaders } from "./auth";

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

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export async function fetchSymbolHistory(
  symbol: string,
  days: number = 30,
): Promise<HistoryEntry[]> {
  const resp = await fetch(`${API_BASE}/history/${encodeURIComponent(symbol)}?days=${days}`, {
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error(`History fetch failed: ${resp.status}`);
  return resp.json();
}
