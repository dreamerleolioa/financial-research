import { requestJson } from "./apiClient";
import type { PositionResult } from "./analysisTypes";
import { parsePortfolioRiskSummary } from "./portfolioSchemas";
import type {
  AddEntryReasonCode,
  BackfillLifecyclePlanRequest,
  BackfillLifecyclePlanResponse,
  ClosedPortfolioItem,
  DecisionConfidenceLevel,
  LifecyclePlanResponse,
  PlanAdherence,
  PortfolioDecisionContextStatusMap,
  PortfolioItem,
  PortfolioRiskSummary,
  PositionEvent,
} from "./portfolioTypes";

export interface PortfolioHistoryEntry {
  record_date: string;
  action_tag: string | null;
  signal_confidence: number | null;
  recommended_action: string | null;
  indicators: { close_price?: number | null } | null;
  risk_state?: string | null;
  risk_state_label?: string | null;
  discipline_triggers?: string[];
  risk_control_reference?: Record<string, unknown> | null;
  compatibility_source?: string | null;
}

export interface CreatePortfolioRequest {
  symbol: string;
  entry_price: number;
  quantity: number;
  entry_date: string;
  notes: string | null;
  entry_record?: {
    entry_reason?: string | null;
    planned_holding_period?: string | null;
    default_stop_rule?: string | null;
    add_entry_condition?: string | null;
    note?: string | null;
  };
}

export interface UpdatePortfolioRequest {
  entry_price: number;
  quantity: number;
  entry_date: string;
  notes: string | null;
}

export interface ClosePortfolioRequest {
  exit_date: string;
  exit_price: number;
  exit_quantity: number;
  fees?: number;
  taxes?: number;
}

export interface AddEntryRequest {
  event_date: string;
  price: number;
  quantity: number;
  fees?: number;
  taxes?: number;
  reason_code: AddEntryReasonCode;
  plan_adherence: PlanAdherence;
  confidence_level: DecisionConfidenceLevel;
  note?: string;
}

export interface AddEntryResponse {
  portfolio: PortfolioItem;
  event: PositionEvent;
}

export interface PortfolioHistoryResponse {
  records: PortfolioHistoryEntry[];
}

export function fetchPortfolioItems(): Promise<PortfolioItem[]> {
  return requestJson<PortfolioItem[]>("/portfolio");
}

export function createPortfolioItem(body: CreatePortfolioRequest): Promise<PortfolioItem> {
  return requestJson<PortfolioItem>("/portfolio", {
    method: "POST",
    body,
  });
}

export function updatePortfolioItem(id: number, body: UpdatePortfolioRequest): Promise<PortfolioItem> {
  return requestJson<PortfolioItem>(`/portfolio/${id}`, {
    method: "PUT",
    body,
  });
}

export function deletePortfolioItem(id: number): Promise<void> {
  return requestJson<void>(`/portfolio/${id}`, {
    method: "DELETE",
  });
}

export function closePortfolioItem(id: number, body: ClosePortfolioRequest): Promise<ClosedPortfolioItem> {
  return requestJson<ClosedPortfolioItem>(`/portfolio/${id}/close`, {
    method: "POST",
    body,
  });
}

export function fetchDecisionContextStatus(): Promise<PortfolioDecisionContextStatusMap> {
  return requestJson<PortfolioDecisionContextStatusMap>("/portfolio/decision-context-status");
}

export async function fetchPortfolioRiskSummary(): Promise<PortfolioRiskSummary> {
  const data = await requestJson<unknown>("/portfolio/risk-summary");
  return parsePortfolioRiskSummary(data);
}

export function fetchLatestPortfolioHistory(): Promise<Record<string, PortfolioHistoryEntry | null>> {
  return requestJson<Record<string, PortfolioHistoryEntry | null>>("/portfolio/latest-history");
}

export function fetchPortfolioHistory(id: number, limit = 20): Promise<PortfolioHistoryResponse> {
  return requestJson<PortfolioHistoryResponse>(`/portfolio/${id}/history`, {
    query: { limit },
  });
}

export function runPortfolioPositionAnalysis(body: Record<string, unknown>): Promise<PositionResult> {
  return requestJson<PositionResult>("/analyze/position", {
    method: "POST",
    body,
  });
}

export function fetchLifecyclePlan(id: number): Promise<LifecyclePlanResponse> {
  return requestJson<LifecyclePlanResponse>(`/portfolio/${id}/lifecycle-plan`);
}

export function backfillLifecyclePlan(
  id: number,
  body: BackfillLifecyclePlanRequest,
): Promise<BackfillLifecyclePlanResponse> {
  return requestJson<BackfillLifecyclePlanResponse>(`/portfolio/${id}/lifecycle-plan/backfill`, {
    method: "PUT",
    body,
  });
}

export function addPortfolioEntry(id: number, body: AddEntryRequest): Promise<AddEntryResponse> {
  return requestJson<AddEntryResponse>(`/portfolio/${id}/add-entry`, {
    method: "POST",
    body,
  });
}
