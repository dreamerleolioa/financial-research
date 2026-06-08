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

export interface TradeReviewResult {
  data_quality?: TradeReviewDataQuality;
  trade_result?: TradeReviewResultMetrics;
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
