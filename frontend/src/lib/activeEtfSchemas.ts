import { z } from "zod";
import type { ActiveEtfDailyResponse } from "./activeEtfTypes";

const decimalNumber = z
  .union([z.number(), z.string()])
  .transform((value) => Number(value))
  .pipe(z.number().finite());
const nullableDecimalNumber = decimalNumber.nullable();
const dataDate = z.iso.date();
const sourceTimestamp = z.iso.datetime({ offset: true });
const publicHttpUrl = z
  .string()
  .url()
  .refine((value) => {
    const protocol = new URL(value).protocol;
    return protocol === "https:" || protocol === "http:";
  }, "Expected an HTTP(S) URL");

const coverageFundSchema = z
  .object({
    fund_code: z.string(),
    name: z.string(),
    category: z.string().nullable().optional().default(null),
    source_provider: z.string(),
    source_url: publicHttpUrl,
    status: z.enum(["ready", "no_baseline", "missing"]),
    data_date: dataDate.nullable().optional().default(null),
    previous_date: dataDate.nullable().optional().default(null),
    latest_data_date: dataDate.nullable().optional().default(null),
    fetched_at: sourceTimestamp.nullable().optional().default(null),
    change_count: z.number().int().nonnegative().default(0),
    common_scale_ratio: nullableDecimalNumber.optional().default(null),
  })
  .passthrough();

const changeSchema = z
  .object({
    action: z.enum(["added", "increased", "decreased", "removed"]),
    fund_code: z.string(),
    fund_name: z.string(),
    symbol: z.string(),
    name: z.string(),
    source_provider: z.string(),
    source_url: publicHttpUrl,
    fetched_at: sourceTimestamp,
    data_date: dataDate,
    previous_date: dataDate,
    current_shares: z.number().int().nonnegative(),
    previous_shares: z.number().int().nonnegative(),
    share_delta: z.number().int(),
    share_delta_pct: nullableDecimalNumber,
    current_weight_pct: decimalNumber,
    previous_weight_pct: decimalNumber,
    weight_delta_pct_points: decimalNumber,
    relative_share_change_pct: nullableDecimalNumber,
    likely_fund_scale_change: z.boolean(),
  })
  .passthrough();

const consensusSchema = z
  .object({
    symbol: z.string(),
    name: z.string(),
    direction: z.enum(["increase", "decrease", "mixed"]),
    fund_count: z.number().int().nonnegative(),
    added_count: z.number().int().nonnegative(),
    increased_count: z.number().int().nonnegative(),
    decreased_count: z.number().int().nonnegative(),
    removed_count: z.number().int().nonnegative(),
  })
  .passthrough();

export const activeEtfDailyResponseSchema = z
  .object({
    data_date: dataDate,
    available_dates: z.array(dataDate),
    generated_at: sourceTimestamp,
    expected_funds: z.number().int().nonnegative(),
    covered_funds: z.number().int().nonnegative(),
    summary: z
      .object({
        changed_funds: z.number().int().nonnegative(),
        changed_stocks: z.number().int().nonnegative(),
        changed_rows: z.number().int().nonnegative(),
        additions: z.number().int().nonnegative(),
        increases: z.number().int().nonnegative(),
        decreases: z.number().int().nonnegative(),
        removals: z.number().int().nonnegative(),
      })
      .passthrough(),
    funds: z.array(coverageFundSchema),
    changes: z.array(changeSchema),
    consensus: z.array(consensusSchema),
  })
  .passthrough();

export function parseActiveEtfDailyResponse(value: unknown): ActiveEtfDailyResponse {
  return activeEtfDailyResponseSchema.parse(value) as ActiveEtfDailyResponse;
}
