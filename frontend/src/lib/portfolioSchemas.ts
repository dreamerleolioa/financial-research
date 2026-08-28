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

const portfolioPriceContextSchema = z
  .object({
    refresh_status: z.enum(["not_requested", "refreshed", "failed"]),
    source: z.string().nullable(),
    as_of: z.string().nullable(),
    data_date: z.string().nullable(),
    market_session: z.enum(["intraday", "closed", "unknown"]),
    is_final: z.boolean().nullable(),
  })
  .passthrough();

const portfolioPriceRefreshSchema = z
  .object({
    status: z.enum(["complete", "partial", "failed"]),
    requested_count: z.number(),
    refreshed_count: z.number(),
    failed_count: z.number(),
    refreshed_symbols: z.array(z.string()),
    failed_symbols: z.array(z.string()),
    refreshed_at: z.string(),
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
        snapshot_close: z.number().nullable().optional(),
        distance_to_avwap_pct: z.number().nullable().optional(),
        distance_basis: z.string().nullable().optional(),
        distance_price: z.number().nullable().optional(),
        distance_price_data_date: z.string().nullable().optional(),
        distance_price_as_of: z.string().nullable().optional(),
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

const chipStabilityContextSchema = z
  .object({
    source: z.string(),
    status: z.string(),
    as_of_date: z.string().nullable().optional(),
    previous_as_of_date: z.string().nullable().optional(),
    thousand_lot_holder_ratio: z.number().nullable().optional(),
    thousand_lot_holder_ratio_delta_pp: z.number().nullable().optional(),
    state: z.string().optional(),
    trend: z.string().optional(),
    summary: z.string().nullable().optional(),
    caveats: z.array(
      z
        .object({
          code: z.string(),
          message: z.string().optional(),
        })
        .passthrough(),
    ),
  })
  .passthrough();

const portfolioPositionRiskSchema = z
  .object({
    symbol: z.string(),
    name: z.string().nullable().optional(),
    industry: z.string().nullable().optional(),
    quantity: z.number().nullable(),
    current_price: z.number().nullable(),
    price_context: portfolioPriceContextSchema.optional(),
    entry_price: z.number().nullable(),
    market_value: z.number().nullable(),
    unrealized_pnl: z.number().nullable(),
    defense_reference: z
      .object({
        price: z.number().nullable(),
        source: z.string().nullable(),
      })
      .passthrough(),
    auto_defense_prices: z
      .object({
        break_20d_low: z.number().nullable().optional(),
        break_ma20: z.number().nullable().optional(),
        break_ma60: z.number().nullable().optional(),
      })
      .passthrough()
      .optional(),
    estimated_risk_amount: z.number().nullable(),
    estimated_risk_pct_of_portfolio: z.number().nullable(),
    portfolio_weight_pct: z.number().nullable(),
    invested_weight_pct: z.number().nullable().optional(),
    account_equity_weight_pct: z.number().nullable().optional(),
    risk_state: z.enum(["contained", "watch", "elevated", "defense_reference_touched", "data_incomplete"]),
    discipline_triggers: z.array(z.string()),
    phase1_position_state: phase1PositionStateSchema.nullable().optional(),
    weekly_major_holders: z.record(z.string(), z.unknown()).optional(),
    chip_stability_context: chipStabilityContextSchema.nullable().optional(),
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
    price_context: portfolioPriceContextSchema.optional(),
    holding_avg_cost: z.number().nullable().optional(),
    avwap_data_date: z.string().nullable().optional(),
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
    version: z.literal("phase1-current-day-lists-v1"),
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
    version: z.literal("portfolio-risk-summary-v2"),
    portfolio_revision: z.string().optional(),
    as_of_date: z.string(),
    portfolio_value: z.number(),
    account_capital: z
      .object({
        status: z.enum(["recorded", "cash_not_recorded"]),
        cash_balance: z.number().nullable(),
        invested_market_value: z.number(),
        account_equity: z.number().nullable(),
        cash_pct_of_account_equity: z.number().nullable(),
        invested_pct_of_account_equity: z.number().nullable(),
        risk_percentage_denominator: z.enum(["account_equity", "invested_market_value_fallback"]),
      })
      .passthrough(),
    total_unrealized_pnl: z.number(),
    total_at_risk: z.number(),
    total_at_risk_pct: z.number().nullable(),
    position_risks: z.array(portfolioPositionRiskSchema),
    price_refresh: portfolioPriceRefreshSchema.optional(),
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
        by_industry: z.array(
          z.object({
            type: z.literal("industry"),
            key: z.string(),
            symbols: z.array(z.string()),
            market_value: z.number(),
            pct_of_invested: z.number().nullable(),
            pct_of_capital_base: z.number().nullable(),
            status: z.enum(["ok", "watch", "elevated", "partial"]),
          }).passthrough(),
        ),
        industry_coverage: z.object({
          status: z.enum(["available", "partial", "unavailable"]),
          classified_market_value: z.number(),
          pct_of_invested: z.number().nullable(),
          eligible_position_count: z.number(),
          valued_position_count: z.number(),
          classified_position_count: z.number(),
          unvalued_position_count: z.number(),
          unclassified_valued_position_count: z.number(),
        }).passthrough(),
        industry_watch_threshold_pct: z.number(),
        industry_elevated_threshold_pct: z.number(),
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
    correlation_risk: z.object({
      status: z.enum(["available", "partial", "insufficient_data"]),
      minimum_overlapping_return_count: z.number(),
      eligible_position_count: z.number(),
      valued_position_count: z.number(),
      possible_pair_count: z.number(),
      eligible_pair_count: z.number(),
      pair_coverage_pct: z.number().nullable(),
      weighted_average_correlation: z.number().nullable(),
      watch_threshold: z.number(),
      elevated_threshold: z.number(),
      pairs: z.array(z.object({
        symbols: z.tuple([z.string(), z.string()]),
        correlation: z.number(),
        overlapping_return_count: z.number(),
        combined_invested_weight_pct: z.number(),
        status: z.enum(["contained", "watch", "elevated"]),
      }).passthrough()),
      interpretation: z.string(),
    }).passthrough(),
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
