import { z } from "zod";
import type { PortfolioRiskSummary } from "./portfolioTypes";

const riskCaveatSchema = z.object({
  code: z.string(),
  message: z.string().optional(),
  count: z.number().optional(),
}).passthrough();

const dataQualitySchema = z.object({
  status: z.enum(["ok", "caution", "insufficient"]),
  caveats: z.array(riskCaveatSchema),
}).passthrough();

const portfolioPositionRiskSchema = z.object({
  symbol: z.string(),
  quantity: z.number().nullable(),
  current_price: z.number().nullable(),
  entry_price: z.number().nullable(),
  market_value: z.number().nullable(),
  unrealized_pnl: z.number().nullable(),
  defense_reference: z.object({
    price: z.number().nullable(),
    source: z.string().nullable(),
  }).passthrough(),
  estimated_risk_amount: z.number().nullable(),
  estimated_risk_pct_of_portfolio: z.number().nullable(),
  portfolio_weight_pct: z.number().nullable(),
  risk_state: z.enum(["contained", "watch", "elevated", "defense_reference_touched", "data_incomplete"]),
  discipline_triggers: z.array(z.string()),
  data_quality: dataQualitySchema,
}).passthrough();

export const portfolioRiskSummarySchema = z.object({
  version: z.string(),
  as_of_date: z.string(),
  portfolio_value: z.number(),
  total_unrealized_pnl: z.number(),
  total_at_risk: z.number(),
  total_at_risk_pct: z.number().nullable(),
  position_risks: z.array(portfolioPositionRiskSchema),
  concentration: z.object({
    by_symbol: z.array(z.object({
      type: z.literal("symbol"),
      key: z.string(),
      market_value: z.number().nullable(),
      pct_of_portfolio: z.number().nullable(),
      status: z.enum(["ok", "watch", "elevated"]),
    }).passthrough()),
  }).passthrough(),
  shared_exposures: z.array(z.object({
    type: z.string(),
    key: z.string(),
    symbols: z.array(z.string()),
    count: z.number(),
    market_value: z.number(),
    pct_of_portfolio: z.number().nullable(),
  }).passthrough()),
  risk_budget_status: z.object({
    status: z.enum(["available", "watch", "constrained", "unknown"]),
    total_at_risk_pct: z.number().nullable(),
    watch_threshold_pct: z.number(),
    constrained_threshold_pct: z.number(),
    notes: z.array(z.string()),
  }).passthrough(),
  data_quality: dataQualitySchema.extend({
    price_stale_after_days: z.number(),
  }).passthrough(),
}).passthrough();

export function parsePortfolioRiskSummary(data: unknown): PortfolioRiskSummary {
  return portfolioRiskSummarySchema.parse(data) as PortfolioRiskSummary;
}
