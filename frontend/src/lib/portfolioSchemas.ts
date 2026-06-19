import { z } from "zod";
import type { PortfolioRiskSummary } from "./portfolioTypes";

const riskCaveatSchema = z
  .object({
    code: z.string(),
    message: z.string().optional(),
    count: z.number().optional(),
  })
  .passthrough();

const dataQualitySchema = z
  .object({
    status: z.enum(["ok", "caution", "insufficient"]),
    caveats: z.array(riskCaveatSchema),
  })
  .passthrough();

const phase1PositionStateSchema = z
  .object({
    symbol: z.string(),
    data_date: z.string(),
    dataset: z.string(),
    adjustment_mode: z.string(),
    state: z.enum(["hold", "add_watch", "profit_take_watch", "warning", "exit_risk", "data_unavailable"]),
    label: z.enum(["加碼", "建倉", "續抱", "停損警戒", "資料不足"]),
    freshness: z.string(),
    missing_reason: z.string().nullable(),
    display_anchor: z
      .object({
        type: z.string(),
        anchor_date: z.string().nullable().optional(),
        anchor_reason: z.string().nullable().optional(),
        avwap: z.number().nullable().optional(),
        distance_to_avwap_pct: z.number().nullable().optional(),
        source_granularity: z.string().optional(),
        estimated: z.boolean().optional(),
      })
      .passthrough()
      .nullable(),
    matched_rules: z.array(z.string()),
    source: z
      .object({
        provider: z.string(),
        dataset: z.string(),
        adjustment_mode: z.string(),
      })
      .passthrough(),
    source_granularity: z.string(),
    data_quality: z.record(z.string(), z.unknown()),
  })
  .passthrough();

const portfolioPositionRiskSchema = z
  .object({
    symbol: z.string(),
    name: z.string().nullable().optional(),
    quantity: z.number().nullable(),
    current_price: z.number().nullable(),
    entry_price: z.number().nullable(),
    market_value: z.number().nullable(),
    unrealized_pnl: z.number().nullable(),
    defense_reference: z
      .object({
        price: z.number().nullable(),
        source: z.string().nullable(),
      })
      .passthrough(),
    estimated_risk_amount: z.number().nullable(),
    estimated_risk_pct_of_portfolio: z.number().nullable(),
    portfolio_weight_pct: z.number().nullable(),
    risk_state: z.enum(["contained", "watch", "elevated", "defense_reference_touched", "data_incomplete"]),
    discipline_triggers: z.array(z.string()),
    phase1_position_state: phase1PositionStateSchema.nullable().optional(),
    data_quality: dataQualitySchema,
  })
  .passthrough();

const phase1ObservationItemSchema = z
  .object({
    symbol: z.string(),
    name: z.string().nullable().optional(),
    label: z.enum(["加碼", "建倉", "續抱", "停損警戒", "資料不足"]).nullable().optional(),
    position_state: z.string().optional(),
    close: z.number().nullable().optional(),
    holding_avg_cost: z.number().nullable().optional(),
    display_anchor: phase1PositionStateSchema.shape.display_anchor.optional(),
    matched_rules: z.array(z.string()),
    current_day_observation: z.string(),
    data_quality: z.record(z.string(), z.unknown()),
  })
  .passthrough();

const phase1CurrentDayListKeySchema = z.enum([
  "pullback_observation_candidates",
  "breakout_confirmation_candidates",
  "holding_management_candidates",
  "holding_risk_alerts",
  "overheated_do_not_chase_candidates",
]);

const phase1CurrentDayListsSchema = z
  .object({
    version: z.string(),
    implemented_lists: z.array(phase1CurrentDayListKeySchema),
    pending_lists: z.array(phase1CurrentDayListKeySchema),
    pullback_observation_candidates: z.array(phase1ObservationItemSchema),
    breakout_confirmation_candidates: z.array(phase1ObservationItemSchema),
    holding_management_candidates: z.array(phase1ObservationItemSchema),
    holding_risk_alerts: z.array(phase1ObservationItemSchema),
    overheated_do_not_chase_candidates: z.array(phase1ObservationItemSchema),
  })
  .passthrough();

export const portfolioRiskSummarySchema = z
  .object({
    version: z.string(),
    as_of_date: z.string(),
    portfolio_value: z.number(),
    total_unrealized_pnl: z.number(),
    total_at_risk: z.number(),
    total_at_risk_pct: z.number().nullable(),
    position_risks: z.array(portfolioPositionRiskSchema),
    phase1_current_day_lists: phase1CurrentDayListsSchema.optional(),
    concentration: z
      .object({
        by_symbol: z.array(
          z
            .object({
              type: z.literal("symbol"),
              key: z.string(),
              market_value: z.number().nullable(),
              pct_of_portfolio: z.number().nullable(),
              status: z.enum(["ok", "watch", "elevated"]),
            })
            .passthrough(),
        ),
      })
      .passthrough(),
    shared_exposures: z.array(
      z
        .object({
          type: z.string(),
          key: z.string(),
          symbols: z.array(z.string()),
          count: z.number(),
          market_value: z.number(),
          pct_of_portfolio: z.number().nullable(),
        })
        .passthrough(),
    ),
    risk_budget_status: z
      .object({
        status: z.enum(["available", "watch", "constrained", "unknown"]),
        total_at_risk_pct: z.number().nullable(),
        watch_threshold_pct: z.number(),
        constrained_threshold_pct: z.number(),
        notes: z.array(z.string()),
      })
      .passthrough(),
    data_quality: dataQualitySchema
      .extend({
        price_stale_after_days: z.number(),
      })
      .passthrough(),
  })
  .passthrough();

export function parsePortfolioRiskSummary(data: unknown): PortfolioRiskSummary {
  return portfolioRiskSummarySchema.parse(data) as PortfolioRiskSummary;
}
