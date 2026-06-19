import { z } from "zod";
import type { AnalyzeResponse } from "./analysisTypes";

const nullableString = z.string().nullable();
const nullableNumber = z.number().nullable();
const recordSchema = z.record(z.string(), z.unknown());

const analysisDetailSchema = z
  .object({
    summary: z.string(),
    risks: z.array(z.string()),
    technical_signal: z.enum(["bullish", "bearish", "sideways"]),
    institutional_flow: nullableString,
    sentiment_label: nullableString,
    tech_insight: nullableString,
    inst_insight: nullableString,
    news_insight: nullableString,
    final_verdict: nullableString,
    fundamental_insight: nullableString.optional(),
    thought_process: nullableString.optional(),
  })
  .passthrough();

const cleanedNewsQualitySchema = z
  .object({
    quality_score: z.number(),
    quality_flags: z.array(z.string()),
  })
  .passthrough();

const newsDisplayItemSchema = z
  .object({
    title: z.string(),
    date: nullableString.optional(),
    source_url: nullableString.optional(),
  })
  .passthrough();

const actionPlanSchema = z
  .object({
    action: nullableString.optional(),
    target_zone: nullableString.optional(),
    defense_line: nullableString.optional(),
    breakeven_note: nullableString.optional(),
    momentum_expectation: nullableString.optional(),
    conviction_level: z.enum(["low", "medium", "high"]).nullable().optional(),
    thesis_points: z.array(z.string()).optional(),
    invalidation_conditions: z.array(z.string()).optional(),
    suggested_position_size: nullableString.optional(),
    upgrade_triggers: z.array(z.string()).optional(),
    downgrade_triggers: z.array(z.string()).optional(),
  })
  .passthrough();

const analysisErrorDetailSchema = z
  .object({
    code: z.string(),
    message: z.string(),
  })
  .passthrough();

const fundamentalDataSchema = z
  .object({
    ttm_eps: nullableNumber.optional(),
    pe_current: nullableNumber.optional(),
    pe_band: nullableString.optional(),
    pe_percentile: nullableNumber.optional(),
    dividend_yield: nullableNumber.optional(),
    yield_signal: nullableString.optional(),
  })
  .passthrough();

const riskControlReferenceSchema = z
  .object({
    reference: nullableString.optional(),
    reference_type: nullableString.optional(),
  })
  .passthrough();

const phase1AnchorSchema = z
  .object({
    available: z.boolean().optional(),
    anchor_date: nullableString.optional(),
    anchor_reason: nullableString.optional(),
    avwap: nullableNumber.optional(),
    distance_to_avwap_pct: nullableNumber.optional(),
    source_granularity: z.string().optional(),
    estimated: z.boolean().optional(),
  })
  .passthrough();

const phase1ObservationSchema = z
  .object({
    symbol: z.string(),
    data_date: z.string(),
    dataset: z.string(),
    adjustment_mode: z.string(),
    freshness: z.string(),
    missing_reason: nullableString,
    source: z
      .object({
        provider: z.string(),
        dataset: z.string(),
        adjustment_mode: z.string(),
      })
      .passthrough(),
    source_granularity: z.string(),
    anchors: z.record(z.string(), phase1AnchorSchema),
    data_quality: z.record(z.string(), z.unknown()),
  })
  .passthrough();

export const analyzeResponseSchema = z
  .object({
    snapshot: recordSchema,
    symbol_name: nullableString.optional(),
    analysis: z.string(),
    analysis_detail: analysisDetailSchema.nullable(),
    cleaned_news: recordSchema.nullable(),
    cleaned_news_quality: cleanedNewsQualitySchema.nullable(),
    news_display_items: z.array(newsDisplayItemSchema),
    confidence_score: nullableNumber,
    cross_validation_note: nullableString,
    strategy_type: z.enum(["short_term", "mid_term", "defensive_wait"]).nullable(),
    entry_zone: nullableString,
    stop_loss: nullableString,
    holding_period: nullableString,
    action_plan_tag: z.enum(["opportunity", "overheated", "neutral"]).nullable(),
    technical_indicators: recordSchema.nullable().optional(),
    action_plan: actionPlanSchema.nullable(),
    risk_state: nullableString.optional(),
    risk_state_label: nullableString.optional(),
    discipline_triggers: z.array(z.string()).optional(),
    observation_conditions: z.array(z.string()).optional(),
    risk_control_reference: riskControlReferenceSchema.nullable().optional(),
    command_language_deprecated: recordSchema.optional(),
    institutional_flow_label: nullableString,
    data_confidence: nullableNumber,
    is_final: z.boolean(),
    intraday_disclaimer: nullableString,
    errors: z.array(analysisErrorDetailSchema),
    fundamental_data: fundamentalDataSchema.nullable().optional(),
    shared_context: z.unknown().nullable().optional(),
    phase1_observation: phase1ObservationSchema.nullable().optional(),
  })
  .passthrough();

export function parseAnalyzeResponse(data: unknown): AnalyzeResponse {
  return analyzeResponseSchema.parse(data) as AnalyzeResponse;
}
