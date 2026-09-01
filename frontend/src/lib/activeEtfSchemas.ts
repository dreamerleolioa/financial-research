import { z } from "zod";
import type { ActiveEtfDailyResponse } from "./activeEtfTypes";

const decimalNumber = z.union([
  z.number().finite(),
  z
    .string()
    .trim()
    .regex(/^-?\d+(?:\.\d+)?$/)
    .transform((value) => Number(value)),
]);
const nullableDecimalNumber = decimalNumber.nullable();
const weightPct = decimalNumber.pipe(z.number().min(0).max(100));
const weightDelta = decimalNumber.pipe(z.number().min(-100).max(100));
const dataDate = z.iso.date();
const sourceTimestamp = z.iso.datetime({ offset: true });
const publicHttpUrl = z
  .string()
  .url()
  .refine((value) => {
    const protocol = new URL(value).protocol;
    return protocol === "https:" || protocol === "http:";
  }, "Expected an HTTP(S) URL");

const sourceEvidenceSchema = z
  .object({
    source_provider: z.string().min(1),
    source_url: publicHttpUrl,
    data_date: dataDate,
    fetched_at: sourceTimestamp,
    payload_hash: z.string().regex(/^[a-f\d]{64}$/i),
  })
  .passthrough();

const periodEvidenceSchema = z
  .object({
    period: z.enum(["current", "previous"]),
    data_date: dataDate,
    verification_status: z.enum(["verified", "single_source", "conflict"]),
    source_count: z.number().int().positive(),
    verification_reason: z.string().nullable().optional().default(null),
    sources: z.array(sourceEvidenceSchema),
  })
  .passthrough()
  .superRefine((evidence, context) => {
    if (evidence.source_count !== evidence.sources.length) {
      context.addIssue({ code: "custom", message: "source_count must match sources", path: ["source_count"] });
    }
    if (evidence.verification_status === "verified" && evidence.source_count < 2) {
      context.addIssue({ code: "custom", message: "verified evidence requires two sources", path: ["source_count"] });
    }
    if (evidence.verification_status === "single_source" && evidence.source_count !== 1) {
      context.addIssue({
        code: "custom",
        message: "single-source evidence requires one source",
        path: ["source_count"],
      });
    }
    evidence.sources.forEach((source, index) => {
      if (source.data_date !== evidence.data_date) {
        context.addIssue({
          code: "custom",
          message: "period evidence sources must match the period data date",
          path: ["sources", index, "data_date"],
        });
      }
    });
  });

const coverageFundSchema = z
  .object({
    fund_code: z.string(),
    name: z.string(),
    category: z.string().nullable().optional().default(null),
    source_provider: z.string(),
    source_url: publicHttpUrl,
    status: z.enum(["ready", "no_baseline", "missing", "single_source", "source_conflict"]),
    verification_status: z.enum(["verified", "single_source", "conflict"]).nullable().optional().default(null),
    source_count: z.number().int().nonnegative().optional().default(0),
    verification_reason: z.string().nullable().optional().default(null),
    sources: z.array(sourceEvidenceSchema).optional().default([]),
    evidence_periods: z.array(periodEvidenceSchema).optional().default([]),
    data_date: dataDate.nullable().optional().default(null),
    previous_date: dataDate.nullable().optional().default(null),
    latest_data_date: dataDate.nullable().optional().default(null),
    fetched_at: sourceTimestamp.nullable().optional().default(null),
    change_count: z.number().int().nonnegative().default(0),
    common_scale_ratio: nullableDecimalNumber.optional().default(null),
  })
  .passthrough()
  .superRefine((fund, context) => {
    if (fund.source_count !== fund.sources.length) {
      context.addIssue({ code: "custom", message: "source_count must match sources", path: ["source_count"] });
    }
    if (fund.verification_status === "verified" && fund.source_count < 2) {
      context.addIssue({ code: "custom", message: "verified funds require two sources", path: ["source_count"] });
    }
    if (fund.verification_status === "single_source" && fund.source_count !== 1) {
      context.addIssue({ code: "custom", message: "single-source funds require one source", path: ["source_count"] });
    }
    if (
      ["ready", "no_baseline"].includes(fund.status) &&
      !["verified", "single_source"].includes(fund.verification_status ?? "")
    ) {
      context.addIssue({
        code: "custom",
        message: "covered funds must have a usable source",
        path: ["verification_status"],
      });
    }
    if (fund.status === "single_source" && fund.verification_status !== "single_source") {
      context.addIssue({ code: "custom", message: "single-source status mismatch", path: ["verification_status"] });
    }
    if (fund.status === "source_conflict" && fund.verification_status !== "conflict") {
      context.addIssue({ code: "custom", message: "conflict status mismatch", path: ["verification_status"] });
    }
    if (fund.status === "missing" && (fund.verification_status !== null || fund.source_count !== 0)) {
      context.addIssue({ code: "custom", message: "missing funds cannot claim source verification", path: ["status"] });
    }
    const evidenceByPeriod = new Map(fund.evidence_periods.map((evidence) => [evidence.period, evidence]));
    if (evidenceByPeriod.size !== fund.evidence_periods.length) {
      context.addIssue({ code: "custom", message: "evidence periods must be unique", path: ["evidence_periods"] });
    }
    if (fund.evidence_periods.length > 0) {
      const currentEvidence = evidenceByPeriod.get("current");
      const previousEvidence = evidenceByPeriod.get("previous");
      if (
        !currentEvidence ||
        currentEvidence.data_date !== fund.data_date ||
        currentEvidence.verification_status !== fund.verification_status ||
        currentEvidence.source_count !== fund.source_count
      ) {
        context.addIssue({
          code: "custom",
          message: "current evidence must match the fund snapshot",
          path: ["evidence_periods"],
        });
      }
      if (fund.status === "ready" && (!previousEvidence || previousEvidence.data_date !== fund.previous_date)) {
        context.addIssue({
          code: "custom",
          message: "ready funds require evidence for the selected baseline",
          path: ["evidence_periods"],
        });
      }
      if (fund.status !== "ready" && previousEvidence) {
        context.addIssue({
          code: "custom",
          message: "non-comparable funds cannot expose baseline evidence",
          path: ["evidence_periods"],
        });
      }
    }
  });

const changeSchema = z
  .object({
    action: z.enum(["added", "increased", "decreased", "removed"]),
    fund_code: z.string(),
    fund_name: z.string(),
    symbol: z.string(),
    name: z.string(),
    source_provider: z.string(),
    source_url: publicHttpUrl,
    verification_status: z.enum(["verified", "single_source"]).optional().default("verified"),
    source_count: z.number().int().positive().optional().default(2),
    fetched_at: sourceTimestamp,
    data_date: dataDate,
    previous_date: dataDate,
    current_shares: z.number().int().safe().nonnegative(),
    previous_shares: z.number().int().safe().nonnegative(),
    share_delta: z.number().int().safe(),
    share_delta_pct: nullableDecimalNumber,
    current_weight_pct: weightPct,
    previous_weight_pct: weightPct,
    weight_delta_pct_points: weightDelta,
    relative_share_change_pct: nullableDecimalNumber,
    likely_fund_scale_change: z.boolean(),
  })
  .passthrough()
  .superRefine((change, context) => {
    if (change.verification_status === "verified" && change.source_count < 2) {
      context.addIssue({ code: "custom", message: "verified changes require two sources", path: ["source_count"] });
    }
    if (change.verification_status === "single_source" && change.source_count !== 1) {
      context.addIssue({ code: "custom", message: "single-source changes require one source", path: ["source_count"] });
    }
  });

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
  .passthrough()
  .superRefine((response, context) => {
    const coveredFunds = response.funds.filter((fund) =>
      ["verified", "single_source"].includes(fund.verification_status ?? ""),
    );
    const verifiedFunds = response.funds.filter((fund) => fund.verification_status === "verified");
    const fundsByCode = new Map(response.funds.map((fund) => [fund.fund_code, fund]));
    const fundCodes = response.funds.map((fund) => fund.fund_code);
    const comparableFundCodes = new Set(
      response.funds.filter((fund) => fund.status === "ready").map((fund) => fund.fund_code),
    );
    if (response.expected_funds !== response.funds.length) {
      context.addIssue({ code: "custom", message: "expected_funds must match funds", path: ["expected_funds"] });
    }
    if (![verifiedFunds.length, coveredFunds.length].includes(response.covered_funds)) {
      context.addIssue({
        code: "custom",
        message: "covered_funds must count verified or usable snapshots",
        path: ["covered_funds"],
      });
    }
    if (new Set(fundCodes).size !== fundCodes.length) {
      context.addIssue({ code: "custom", message: "fund codes must be unique", path: ["funds"] });
    }
    if (response.summary.changed_rows !== response.changes.length) {
      context.addIssue({
        code: "custom",
        message: "changed_rows must match changes",
        path: ["summary", "changed_rows"],
      });
    }
    if (response.summary.changed_funds !== new Set(response.changes.map((change) => change.fund_code)).size) {
      context.addIssue({
        code: "custom",
        message: "changed_funds must match changes",
        path: ["summary", "changed_funds"],
      });
    }
    if (response.summary.changed_stocks !== new Set(response.changes.map((change) => change.symbol)).size) {
      context.addIssue({
        code: "custom",
        message: "changed_stocks must match changes",
        path: ["summary", "changed_stocks"],
      });
    }
    response.changes.forEach((change, index) => {
      if (!comparableFundCodes.has(change.fund_code)) {
        context.addIssue({
          code: "custom",
          message: "changes must only reference comparable funds",
          path: ["changes", index, "fund_code"],
        });
      }
      const fund = fundsByCode.get(change.fund_code);
      if (change.verification_status === "verified" && fund?.verification_status !== "verified") {
        context.addIssue({
          code: "custom",
          message: "verified changes require a verified current fund snapshot",
          path: ["changes", index, "verification_status"],
        });
      }
      if (fund && change.source_count > fund.source_count) {
        context.addIssue({
          code: "custom",
          message: "change source count cannot exceed current fund evidence",
          path: ["changes", index, "source_count"],
        });
      }
      if (fund?.evidence_periods.length) {
        const currentEvidence = fund.evidence_periods.find((evidence) => evidence.period === "current");
        const previousEvidence = fund.evidence_periods.find((evidence) => evidence.period === "previous");
        if (
          !currentEvidence ||
          !previousEvidence ||
          currentEvidence.data_date !== change.data_date ||
          previousEvidence.data_date !== change.previous_date
        ) {
          context.addIssue({
            code: "custom",
            message: "change dates must match both evidence periods",
            path: ["changes", index, "data_date"],
          });
        } else {
          const expectedVerificationStatus =
            currentEvidence.verification_status === "verified" && previousEvidence.verification_status === "verified"
              ? "verified"
              : "single_source";
          const expectedSourceCount = Math.min(currentEvidence.source_count, previousEvidence.source_count);
          if (
            change.verification_status !== expectedVerificationStatus ||
            change.source_count !== expectedSourceCount
          ) {
            context.addIssue({
              code: "custom",
              message: "change verification must match both evidence periods",
              path: ["changes", index, "verification_status"],
            });
          }
        }
      }
    });
  });

export function parseActiveEtfDailyResponse(value: unknown): ActiveEtfDailyResponse {
  return activeEtfDailyResponseSchema.parse(value) as ActiveEtfDailyResponse;
}
