import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  useAddPortfolioEntryMutation,
  useBackfillLifecyclePlanMutation,
  useClosePortfolioItemMutation,
  useDeletePortfolioItemMutation,
  useUpdateLifecyclePlanMutation,
  useUpdatePortfolioItemMutation,
} from "../features/portfolio/mutations";
import {
  useDecisionContextStatusQuery,
  useLatestPortfolioHistoryQuery,
  useLifecyclePlanQuery,
  usePortfolioItemsQuery,
  usePortfolioRiskSummaryQuery,
} from "../features/portfolio/queries";
import { portfolioKeys } from "../features/portfolio/queryKeys";
import { deleteAsyncMapValue, setAsyncMapValue } from "../lib/asyncMap";
import { analyzeSymbol } from "../lib/analyzeApi";
import type { AnalyzeResponse, PositionResult } from "../lib/analysisTypes";
import { formatPrice } from "../lib/formatters";
import { InsightText } from "../components/InsightText";
import {
  fetchPortfolioHistory,
  runPortfolioPositionAnalysis,
  type AddEntryRequest,
  type PortfolioHistoryEntry,
} from "../lib/portfolioApi";
import {
  ADD_ENTRY_REASON_CODE_VALUES,
  DECISION_CONFIDENCE_LEVEL_VALUES,
  PLAN_ADHERENCE_VALUES,
} from "../lib/portfolioTypes";
import type {
  AddEntryCondition,
  AddEntryReasonCode,
  BackfillLifecyclePlanRequest,
  BackfillLifecyclePlanResponse,
  ClosedPortfolioItem,
  DecisionConfidenceLevel,
  DefaultStopRule,
  LifecycleSetupType,
  PlanAdherence,
  PlannedHoldingPeriod,
  PortfolioPhase1CurrentDayListKey,
  PortfolioPhase1ObservationItem,
  PortfolioItem,
  PortfolioRiskSummary,
} from "../lib/portfolioTypes";
import {
  ADD_ENTRY_CONDITION_LABEL,
  ADD_ENTRY_CONDITION_OPTIONS,
  ADD_ENTRY_REASON_CODE_LABEL,
  CONFIDENCE_LEVEL_LABEL,
  DEFAULT_STOP_RULE_OPTIONS,
  LIFECYCLE_SETUP_TYPE_OPTIONS,
  OPERATION_PLAN_STATUS_CLASS,
  OPERATION_PLAN_STATUS_LABEL,
  PLAN_ADHERENCE_LABEL,
  PLANNED_HOLDING_PERIOD_OPTIONS,
} from "../lib/portfolioLabels";

type HistoryEntry = PortfolioHistoryEntry;
type PortfolioPositionRiskItem = PortfolioRiskSummary["position_risks"][number];
type AutoDefensePrices = NonNullable<PortfolioRiskSummary["position_risks"][number]["auto_defense_prices"]>;

interface PortfolioPageProps {
  onNavigateAnalyze: (symbol: string) => void;
}

function getTodayDateString(): string {
  return new Date().toISOString().slice(0, 10);
}

function parseRequiredNumberInput(value: string): number | null {
  const trimmedValue = value.trim();
  if (trimmedValue === "") return null;
  const parsedValue = Number(trimmedValue);
  return Number.isFinite(parsedValue) ? parsedValue : null;
}

function parseOptionalNumberInput(value: string): number | null | undefined {
  const trimmedValue = value.trim();
  if (trimmedValue === "") return undefined;
  const parsedValue = Number(trimmedValue);
  return Number.isFinite(parsedValue) ? parsedValue : null;
}

interface BackfillPlanModalProps {
  item: PortfolioItem;
  onClose: () => void;
  onSaved: (response: BackfillLifecyclePlanResponse) => void;
}

function BackfillPlanModal({ item, onClose, onSaved }: BackfillPlanModalProps) {
  const backfillLifecyclePlanMutation = useBackfillLifecyclePlanMutation();
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);
  const [thesis, setThesis] = useState("");
  const [setupType, setSetupType] = useState<LifecycleSetupType | "">("");
  const [plannedHoldingPeriod, setPlannedHoldingPeriod] = useState<PlannedHoldingPeriod | "">("");
  const [defaultStopRule, setDefaultStopRule] = useState<DefaultStopRule | "">("");
  const [addEntryCondition, setAddEntryCondition] = useState<AddEntryCondition | "">("");
  const [plannedInvalidation, setPlannedInvalidation] = useState("");
  const [plannedStopPrice, setPlannedStopPrice] = useState("");
  const [plannedTargetOrScaleOutRule, setPlannedTargetOrScaleOutRule] = useState("");
  const [plannedRiskAmount, setPlannedRiskAmount] = useState("");
  const [plannedRiskPct, setPlannedRiskPct] = useState("");
  const [positionSizingRationale, setPositionSizingRationale] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function parseOptionalNumber(value: string, label: string): number | undefined {
    const trimmedValue = value.trim();
    if (!trimmedValue) return undefined;
    const parsedValue = Number(trimmedValue);
    if (!Number.isFinite(parsedValue)) throw new Error(`${label} 必須是有效數字。`);
    return parsedValue;
  }

  async function handleSave() {
    setError(null);

    let parsedStopPrice: number | undefined;
    let parsedRiskAmount: number | undefined;
    let parsedRiskPct: number | undefined;
    try {
      parsedStopPrice = parseOptionalNumber(plannedStopPrice, "停損價");
      parsedRiskAmount = parseOptionalNumber(plannedRiskAmount, "停損金額");
      parsedRiskPct = parseOptionalNumber(plannedRiskPct, "停損比例");
    } catch (err) {
      setError(err instanceof Error ? err.message : "請確認數字欄位。");
      return;
    }

    if (parsedStopPrice != null && parsedStopPrice <= 0) {
      setError("停損價必須大於 0。");
      return;
    }
    if (defaultStopRule === "fixed_price" && parsedStopPrice == null) {
      setError("選擇固定價格停損時，請填寫停損價。");
      return;
    }
    if (parsedRiskAmount != null && parsedRiskAmount < 0) {
      setError("停損金額不可小於 0。");
      return;
    }
    if (parsedRiskPct != null && parsedRiskPct < 0) {
      setError("停損比例不可小於 0。");
      return;
    }

    const body: BackfillLifecyclePlanRequest = {};
    const trimmedThesis = thesis.trim();
    const trimmedInvalidation = plannedInvalidation.trim();
    const trimmedTargetRule = plannedTargetOrScaleOutRule.trim();
    const trimmedSizingRationale = positionSizingRationale.trim();

    if (trimmedThesis) body.thesis = trimmedThesis;
    if (setupType) body.setup_type = setupType;
    if (plannedHoldingPeriod) body.planned_holding_period = plannedHoldingPeriod;
    if (defaultStopRule) body.default_stop_rule = defaultStopRule;
    if (addEntryCondition) body.add_entry_condition = addEntryCondition;
    if (trimmedInvalidation) body.planned_invalidation = trimmedInvalidation;
    if (parsedStopPrice != null) body.planned_stop_price = parsedStopPrice;
    if (trimmedTargetRule) body.planned_target_or_scale_out_rule = trimmedTargetRule;
    if (parsedRiskAmount != null) body.planned_risk_amount = parsedRiskAmount;
    if (parsedRiskPct != null) body.planned_risk_pct = parsedRiskPct;
    if (trimmedSizingRationale) body.position_sizing_rationale = trimmedSizingRationale;

    if (Object.keys(body).length === 0) {
      setError("請至少補填一個 plan 欄位。");
      return;
    }

    setSaving(true);
    try {
      const data = await backfillLifecyclePlanMutation.mutateAsync({ id: item.id, body });
      onSaved(data);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "補填 plan 失敗");
      setSaving(false);
    }
  }

  return (
    <div
      ref={backdropRef}
      onMouseDown={(e) => {
        mouseDownOnBackdrop.current = e.target === backdropRef.current;
      }}
      onClick={(e) => {
        if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose();
        mouseDownOnBackdrop.current = false;
      }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-card shadow-xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border-subtle bg-card px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">補填 operation plan · {portfolioDisplayName(item)}</p>
            <p className="mt-1 text-xs text-text-faint">
              成本 {formatPrice(item.entry_price, item.symbol)} ｜ 進場日 {item.entry_date}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
            這是事後補填的 operation plan，只用來改善未來 lifecycle review 的脈絡品質；它不是原始進場當下的
            plan，也不會取代既有分析、新增批次、結案批次或結案流程。
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-muted">投資假設 / Thesis</span>
            <textarea
              value={thesis}
              onChange={(e) => setThesis(e.target.value)}
              rows={3}
              placeholder="補填你當時能確認的操作假設，不要引用後來的價格或損益結果"
              className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </label>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">Setup type</span>
              <select
                value={setupType}
                onChange={(e) => setSetupType(e.target.value as LifecycleSetupType | "")}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="">未選擇（不送出）</option>
                {LIFECYCLE_SETUP_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">預計持有期間</span>
              <select
                value={plannedHoldingPeriod}
                onChange={(e) => setPlannedHoldingPeriod(e.target.value as PlannedHoldingPeriod | "")}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="">未選擇（不送出）</option>
                {PLANNED_HOLDING_PERIOD_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">預設停損規則</span>
              <select
                value={defaultStopRule}
                onChange={(e) => setDefaultStopRule(e.target.value as DefaultStopRule | "")}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="">未選擇（不送出）</option>
                {DEFAULT_STOP_RULE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">新增批次條件</span>
              <select
                value={addEntryCondition}
                onChange={(e) => setAddEntryCondition(e.target.value as AddEntryCondition | "")}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="">未選擇（不送出）</option>
                {ADD_ENTRY_CONDITION_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-muted">失效條件</span>
            <textarea
              value={plannedInvalidation}
              onChange={(e) => setPlannedInvalidation(e.target.value)}
              rows={2}
              className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </label>

          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">停損價</span>
              <input
                type="number"
                step="0.01"
                value={plannedStopPrice}
                onChange={(e) => setPlannedStopPrice(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">停損金額</span>
              <input
                type="number"
                step="0.01"
                value={plannedRiskAmount}
                onChange={(e) => setPlannedRiskAmount(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">停損比例 %</span>
              <input
                type="number"
                step="0.01"
                value={plannedRiskPct}
                onChange={(e) => setPlannedRiskPct(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-muted">獲利保護 / 分批降低曝險規則</span>
            <textarea
              value={plannedTargetOrScaleOutRule}
              onChange={(e) => setPlannedTargetOrScaleOutRule(e.target.value)}
              rows={2}
              className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-muted">部位大小理由</span>
            <textarea
              value={positionSizingRationale}
              onChange={(e) => setPositionSizingRationale(e.target.value)}
              rows={2}
              className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </label>

          {error && (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </p>
          )}
        </div>

        <div className="sticky bottom-0 flex justify-end gap-2 border-t border-border-subtle bg-card px-5 py-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:bg-card-hover"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? "儲存中…" : "儲存事後補填 plan"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Edit Modal ───────────────────────────────────────────────

interface EditPortfolioModalProps {
  item: PortfolioItem;
  autoDefensePrices?: PortfolioRiskSummary["position_risks"][number]["auto_defense_prices"];
  onClose: () => void;
  onSaved: (updated: PortfolioItem) => void;
}

function formatPriceForInput(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return String(Number(value.toFixed(2)));
}

function autoDefensePricesFromIndicators(indicators: AnalyzeResponse["technical_indicators"]): AutoDefensePrices | null {
  if (!indicators) return null;
  return {
    break_20d_low: indicators.low_20d,
    break_ma20: indicators.ma20,
    break_ma60: indicators.ma60,
  };
}

function derivePlanStopPrice(
  rule: DefaultStopRule | "",
  autoDefensePrices?: AutoDefensePrices | null,
): number | null {
  const value = rule ? autoDefensePrices?.[rule as keyof NonNullable<typeof autoDefensePrices>] : null;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function EditPortfolioModal({ item, autoDefensePrices, onClose, onSaved }: EditPortfolioModalProps) {
  const updatePortfolioItemMutation = useUpdatePortfolioItemMutation();
  const updateLifecyclePlanMutation = useUpdateLifecyclePlanMutation();
  const lifecyclePlanQuery = useLifecyclePlanQuery(item.id);
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);
  const [entryPrice, setEntryPrice] = useState(String(item.entry_price));
  const [quantity, setQuantity] = useState(String(item.quantity));
  const [entryDate, setEntryDate] = useState(item.entry_date);
  const [notes, setNotes] = useState(item.notes ?? "");
  const [thesis, setThesis] = useState("");
  const [setupType, setSetupType] = useState<LifecycleSetupType | "">("");
  const [plannedHoldingPeriod, setPlannedHoldingPeriod] = useState<PlannedHoldingPeriod | "">("");
  const [defaultStopRule, setDefaultStopRule] = useState<DefaultStopRule | "">("");
  const [addEntryCondition, setAddEntryCondition] = useState<AddEntryCondition | "">("");
  const [plannedInvalidation, setPlannedInvalidation] = useState("");
  const [plannedStopPrice, setPlannedStopPrice] = useState("");
  const [plannedTargetOrScaleOutRule, setPlannedTargetOrScaleOutRule] = useState("");
  const [plannedRiskAmount, setPlannedRiskAmount] = useState("");
  const [plannedRiskPct, setPlannedRiskPct] = useState("");
  const [positionSizingRationale, setPositionSizingRationale] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [latestAutoDefensePrices, setLatestAutoDefensePrices] = useState<AutoDefensePrices | null>(null);
  const [stopReferenceLoading, setStopReferenceLoading] = useState(false);
  const [stopReferenceError, setStopReferenceError] = useState<string | null>(null);
  const stopPriceTouchedRef = useRef(false);
  const effectiveAutoDefensePrices = latestAutoDefensePrices ?? autoDefensePrices ?? null;
  const autoPlannedStopPrice = derivePlanStopPrice(defaultStopRule, effectiveAutoDefensePrices);

  useEffect(() => {
    const plan = lifecyclePlanQuery.data;
    if (!plan) return;
    const planDefaultStopRule = plan.default_stop_rule ?? "";
    const derivedStopPrice = derivePlanStopPrice(planDefaultStopRule, autoDefensePrices ?? null);
    setThesis(plan.thesis ?? "");
    setSetupType(plan.setup_type ?? "");
    setPlannedHoldingPeriod(plan.planned_holding_period ?? "");
    setDefaultStopRule(planDefaultStopRule);
    setAddEntryCondition(plan.add_entry_condition ?? "");
    setPlannedInvalidation(plan.planned_invalidation ?? "");
    if (plan.planned_stop_price != null) {
      setPlannedStopPrice(String(plan.planned_stop_price));
      stopPriceTouchedRef.current = false;
    } else if (!stopPriceTouchedRef.current) {
      setPlannedStopPrice(derivedStopPrice != null ? formatPriceForInput(derivedStopPrice) : "");
    }
    setPlannedTargetOrScaleOutRule(plan.planned_target_or_scale_out_rule ?? "");
    setPlannedRiskAmount(plan.planned_risk_amount != null ? String(plan.planned_risk_amount) : "");
    setPlannedRiskPct(plan.planned_risk_pct != null ? String(plan.planned_risk_pct) : "");
    setPositionSizingRationale(plan.position_sizing_rationale ?? "");
  }, [lifecyclePlanQuery.data]);

  useEffect(() => {
    const plan = lifecyclePlanQuery.data;
    if (plan?.planned_stop_price != null || stopPriceTouchedRef.current) return;
    const derivedStopPrice = derivePlanStopPrice(defaultStopRule, effectiveAutoDefensePrices);
    if (derivedStopPrice != null) {
      setPlannedStopPrice(formatPriceForInput(derivedStopPrice));
    }
  }, [defaultStopRule, effectiveAutoDefensePrices, lifecyclePlanQuery.data]);

  useEffect(() => {
    const controller = new AbortController();
    setStopReferenceLoading(true);
    setStopReferenceError(null);
    void analyzeSymbol({ symbol: item.symbol, skip_ai: true }, controller.signal)
      .then((result) => {
        const prices = autoDefensePricesFromIndicators(result.technical_indicators ?? null);
        setLatestAutoDefensePrices(prices);
        if (!prices) setStopReferenceError("找不到可用的 MA20 / MA60 / 20 日低點。");
      })
      .catch((err) => {
        if (err instanceof Error && err.name === "AbortError") return;
        setStopReferenceError(err instanceof Error ? err.message : "無法取得最新停損參考價。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setStopReferenceLoading(false);
      });

    return () => controller.abort();
  }, [item.symbol]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleSave() {
    setError(null);

    const parsedEntryPrice = parseRequiredNumberInput(entryPrice);
    const parsedQuantity = parseRequiredNumberInput(quantity);
    const parsedStopPrice = parseOptionalNumberInput(plannedStopPrice);
    const parsedRiskAmount = parseOptionalNumberInput(plannedRiskAmount);
    const parsedRiskPct = parseOptionalNumberInput(plannedRiskPct);

    if (parsedEntryPrice == null || parsedEntryPrice <= 0) {
      setError("成本價必須是大於 0 的有效數字。");
      return;
    }
    if (parsedQuantity == null || parsedQuantity < 0 || !Number.isInteger(parsedQuantity)) {
      setError("持有股數必須是 0 或正整數。");
      return;
    }
    if (parsedStopPrice === null || parsedRiskAmount === null || parsedRiskPct === null) {
      setError("請確認計畫中的數字欄位。");
      return;
    }
    if (parsedStopPrice !== undefined && parsedStopPrice <= 0) {
      setError("停損價必須大於 0。");
      return;
    }
    if (defaultStopRule === "fixed_price" && parsedStopPrice == null) {
      setError("選擇固定價格停損時，請填寫停損價。");
      return;
    }

    const planBody: BackfillLifecyclePlanRequest = {};
    const trimmedThesis = thesis.trim();
    const trimmedInvalidation = plannedInvalidation.trim();
    const trimmedTargetRule = plannedTargetOrScaleOutRule.trim();
    const trimmedSizingRationale = positionSizingRationale.trim();

    if (trimmedThesis) planBody.thesis = trimmedThesis;
    if (setupType) planBody.setup_type = setupType;
    if (plannedHoldingPeriod) planBody.planned_holding_period = plannedHoldingPeriod;
    if (defaultStopRule) planBody.default_stop_rule = defaultStopRule;
    if (addEntryCondition) planBody.add_entry_condition = addEntryCondition;
    if (trimmedInvalidation) planBody.planned_invalidation = trimmedInvalidation;
    if (parsedStopPrice !== undefined) planBody.planned_stop_price = parsedStopPrice;
    if (trimmedTargetRule) planBody.planned_target_or_scale_out_rule = trimmedTargetRule;
    if (parsedRiskAmount !== undefined) planBody.planned_risk_amount = parsedRiskAmount;
    if (parsedRiskPct !== undefined) planBody.planned_risk_pct = parsedRiskPct;
    if (trimmedSizingRationale) planBody.position_sizing_rationale = trimmedSizingRationale;

    setSaving(true);
    try {
      const updated = await updatePortfolioItemMutation.mutateAsync({
        id: item.id,
        body: {
          entry_price: parsedEntryPrice,
          quantity: parsedQuantity,
          entry_date: entryDate,
          notes: notes.trim() || null,
        },
      });
      if (Object.keys(planBody).length > 0 || lifecyclePlanQuery.data?.source != null) {
        await updateLifecyclePlanMutation.mutateAsync({ id: item.id, body: planBody });
      }
      onSaved(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "儲存失敗");
    } finally {
      setSaving(false);
    }
  }

  function handleDefaultStopRuleChange(value: DefaultStopRule | "") {
    const derivedStopPrice = derivePlanStopPrice(value, effectiveAutoDefensePrices);
    setDefaultStopRule(value);
    stopPriceTouchedRef.current = false;
    setPlannedStopPrice(derivedStopPrice != null ? formatPriceForInput(derivedStopPrice) : "");
  }

  return (
    <div
      ref={backdropRef}
      onMouseDown={(e) => {
        mouseDownOnBackdrop.current = e.target === backdropRef.current;
      }}
      onClick={(e) => {
        if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose();
        mouseDownOnBackdrop.current = false;
      }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">編輯持股與計畫 · {portfolioDisplayName(item)}</p>
            {lifecyclePlanQuery.data?.source && (
              <p className="mt-1 text-xs text-text-faint">
                計畫來源：{lifecyclePlanQuery.data.created_after_entry ? "事後補填" : "進場時記錄"}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          {saved ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
              持倉資訊與操作計畫已更新。若成本價、日期或停損價有變更，請重新觸發分析以確保診斷數據正確。
            </div>
          ) : (
            <>
              <section className="space-y-3">
                <p className="text-xs font-semibold text-text-muted">持股資料</p>
                <div className="grid grid-cols-2 gap-3">
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">成本價</span>
                    <input
                      type="number"
                      step="0.01"
                      value={entryPrice}
                      onChange={(e) => setEntryPrice(e.target.value)}
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">持有股數</span>
                    <input
                      type="number"
                      step="1"
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    />
                  </label>
                </div>
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-text-muted">購入日期</span>
                  <input
                    type="date"
                    value={entryDate}
                    onChange={(e) => setEntryDate(e.target.value)}
                    className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-text-muted">備註（選填）</span>
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={2}
                    className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </label>
              </section>

              <section className="space-y-3 border-t border-border-subtle pt-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-semibold text-text-muted">操作計畫</p>
                  {lifecyclePlanQuery.isLoading && <span className="text-xs text-text-faint">讀取中…</span>}
                </div>
                {lifecyclePlanQuery.error && (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                    無法讀取現有計畫，仍可儲存持股資料。
                  </p>
                )}
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-text-muted">投資假設 / Thesis</span>
                  <textarea
                    value={thesis}
                    onChange={(e) => setThesis(e.target.value)}
                    rows={2}
                    className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </label>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">Setup type</span>
                    <select
                      value={setupType}
                      onChange={(e) => setSetupType(e.target.value as LifecycleSetupType | "")}
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      <option value="">未選擇</option>
                      {LIFECYCLE_SETUP_TYPE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">預計持有期間</span>
                    <select
                      value={plannedHoldingPeriod}
                      onChange={(e) => setPlannedHoldingPeriod(e.target.value as PlannedHoldingPeriod | "")}
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      <option value="">未選擇</option>
                      {PLANNED_HOLDING_PERIOD_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">停損規則</span>
                    <select
                      value={defaultStopRule}
                      onChange={(e) => handleDefaultStopRuleChange(e.target.value as DefaultStopRule | "")}
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      <option value="">未選擇</option>
                      {DEFAULT_STOP_RULE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">新增批次條件</span>
                    <select
                      value={addEntryCondition}
                      onChange={(e) => setAddEntryCondition(e.target.value as AddEntryCondition | "")}
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      <option value="">未選擇</option>
                      {ADD_ENTRY_CONDITION_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-text-muted">失效條件</span>
                  <textarea
                    value={plannedInvalidation}
                    onChange={(e) => setPlannedInvalidation(e.target.value)}
                    rows={2}
                    className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-text-muted">停損價</span>
                  <input
                    type="number"
                    step="0.01"
                    value={plannedStopPrice}
                    onChange={(e) => {
                      stopPriceTouchedRef.current = true;
                      setPlannedStopPrice(e.target.value);
                    }}
                    placeholder={
                      defaultStopRule === "fixed_price"
                        ? "請輸入固定停損價"
                        : autoPlannedStopPrice != null
                          ? formatPriceForInput(autoPlannedStopPrice)
                          : stopReferenceLoading
                            ? "讀取最新參考價..."
                          : "未選擇則不送出"
                    }
                    className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                  <span className="block text-xs leading-relaxed text-text-faint">
                    {stopReferenceError ??
                      "MA20、MA60、20 日低點會從最新個股技術指標自動帶入；固定價格請手動確認。"}
                  </span>
                </label>
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-text-muted">目標 / 分批出場規則</span>
                  <textarea
                    value={plannedTargetOrScaleOutRule}
                    onChange={(e) => setPlannedTargetOrScaleOutRule(e.target.value)}
                    rows={2}
                    className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-text-muted">倉位 sizing 理由</span>
                  <textarea
                    value={positionSizingRationale}
                    onChange={(e) => setPositionSizingRationale(e.target.value)}
                    rows={2}
                    className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </label>
              </section>
              {error && (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
                  {error}
                </p>
              )}
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border-subtle px-5 py-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:bg-card-hover"
          >
            {saved ? "關閉" : "取消"}
          </button>
          {!saved && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "儲存中…" : "儲存持股與計畫"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

interface ClosePositionModalProps {
  item: PortfolioItem;
  onClose: () => void;
  onClosed: (sourceItem: PortfolioItem, closed: ClosedPortfolioItem) => void;
}

function ClosePositionModal({ item, onClose, onClosed }: ClosePositionModalProps) {
  const closePortfolioItemMutation = useClosePortfolioItemMutation();
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);
  const [exitDate, setExitDate] = useState(getTodayDateString());
  const [exitPrice, setExitPrice] = useState("");
  const [exitQuantity, setExitQuantity] = useState(String(item.quantity));
  const [fees, setFees] = useState("");
  const [taxes, setTaxes] = useState("");
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleClosePosition() {
    setError(null);
    const parsedExitPrice = parseRequiredNumberInput(exitPrice);
    const parsedExitQuantity = parseRequiredNumberInput(exitQuantity);
    const parsedFees = parseOptionalNumberInput(fees);
    const parsedTaxes = parseOptionalNumberInput(taxes);
    if (parsedExitPrice == null || parsedExitQuantity == null) {
      setError("請完整填寫有效的結案價格與股數。");
      return;
    }
    if (parsedExitPrice <= 0) {
      setError("結案價格必須大於 0。");
      return;
    }
    if (!Number.isInteger(parsedExitQuantity) || parsedExitQuantity <= 0) {
      setError("結案股數必須是大於 0 的整數。");
      return;
    }
    if (parsedExitQuantity > item.quantity) {
      setError("結案股數不可超過目前持有股數。");
      return;
    }
    if (
      parsedFees === null ||
      parsedTaxes === null ||
      (parsedFees !== undefined && parsedFees < 0) ||
      (parsedTaxes !== undefined && parsedTaxes < 0)
    ) {
      setError("手續費與交易稅需為非負數；留空則自動估算。");
      return;
    }

    const body: {
      exit_date: string;
      exit_price: number;
      exit_quantity: number;
      fees?: number;
      taxes?: number;
    } = {
      exit_date: exitDate,
      exit_price: parsedExitPrice,
      exit_quantity: parsedExitQuantity,
    };
    if (parsedFees !== undefined) body.fees = parsedFees;
    if (parsedTaxes !== undefined) body.taxes = parsedTaxes;

    setClosing(true);
    try {
      const closed = await closePortfolioItemMutation.mutateAsync({ id: item.id, body });
      onClosed(item, closed);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "結案失敗");
      setClosing(false);
    }
  }

  return (
    <div
      ref={backdropRef}
      onMouseDown={(e) => {
        mouseDownOnBackdrop.current = e.target === backdropRef.current;
      }}
      onClick={(e) => {
        if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose();
        mouseDownOnBackdrop.current = false;
      }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="w-full max-w-md rounded-2xl bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">結案批次記錄 · {portfolioDisplayName(item)}</p>
            <p className="text-xs text-text-faint">
              持有 {item.quantity} 股，成本 {formatPrice(item.entry_price, item.symbol)}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
            可輸入部分或全部結案股數。部分結案會保留剩餘持股，全部結案則移至已結案紀錄。
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">結案日期</span>
              <input
                type="date"
                value={exitDate}
                onChange={(e) => setExitDate(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">結案價格</span>
              <input
                type="number"
                step="0.01"
                value={exitPrice}
                onChange={(e) => setExitPrice(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">結案股數</span>
              <input
                type="number"
                step="1"
                value={exitQuantity}
                onChange={(e) => setExitQuantity(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">手續費（留空自動估算）</span>
              <input
                type="number"
                step="0.01"
                value={fees}
                onChange={(e) => setFees(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">交易稅（留空自動估算）</span>
              <input
                type="number"
                step="0.01"
                value={taxes}
                onChange={(e) => setTaxes(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
          </div>
          <p className="text-xs leading-relaxed text-text-faint">
            留空時使用後端台股預設費率估算；實際券商折扣或特殊稅率可手動覆寫。
          </p>
          {error && (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border-subtle px-5 py-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:bg-card-hover"
          >
            取消
          </button>
          <button
            onClick={handleClosePosition}
            disabled={closing}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {closing ? "結案中…" : "確認結案"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface AddEntryModalProps {
  item: PortfolioItem;
  onClose: () => void;
  onAdded: (updated: PortfolioItem) => void;
}

function AddEntryModal({ item, onClose, onAdded }: AddEntryModalProps) {
  const addPortfolioEntryMutation = useAddPortfolioEntryMutation();
  const lifecyclePlanQuery = useLifecyclePlanQuery(item.id);
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);
  const [eventDate, setEventDate] = useState(getTodayDateString());
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [fees, setFees] = useState("");
  const [taxes, setTaxes] = useState("");
  const [reasonCode, setReasonCode] = useState<AddEntryReasonCode>("planned_scale_in");
  const [planAdherence, setPlanAdherence] = useState<PlanAdherence>("yes");
  const [confidenceLevel, setConfidenceLevel] = useState<DecisionConfidenceLevel>("medium");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleAddEntry() {
    setError(null);
    const parsedPrice = parseRequiredNumberInput(price);
    const parsedQuantity = parseRequiredNumberInput(quantity);
    const parsedFees = parseOptionalNumberInput(fees);
    const parsedTaxes = parseOptionalNumberInput(taxes);

    if (!eventDate) {
      setError("請選擇新增批次日期。");
      return;
    }
    if (parsedPrice == null || parsedQuantity == null) {
      setError("請完整填寫有效的價格與股數。");
      return;
    }
    if (parsedPrice <= 0) {
      setError("新增批次價格必須大於 0。");
      return;
    }
    if (!Number.isInteger(parsedQuantity) || parsedQuantity <= 0) {
      setError("新增批次股數必須是大於 0 的整數。");
      return;
    }
    if (
      parsedFees === null ||
      parsedTaxes === null ||
      (parsedFees !== undefined && parsedFees < 0) ||
      (parsedTaxes !== undefined && parsedTaxes < 0)
    ) {
      setError("手續費與交易稅需為非負數；留空則使用預設值。");
      return;
    }

    const trimmedNote = note.trim();
    const body: AddEntryRequest = {
      event_date: eventDate,
      price: parsedPrice,
      quantity: parsedQuantity,
      reason_code: reasonCode,
      plan_adherence: planAdherence,
      confidence_level: confidenceLevel,
    };
    if (parsedFees !== undefined) body.fees = parsedFees;
    if (parsedTaxes !== undefined) body.taxes = parsedTaxes;
    if (trimmedNote) body.note = trimmedNote;

    setSubmitting(true);
    try {
      const data = await addPortfolioEntryMutation.mutateAsync({ id: item.id, body });
      onAdded(data.portfolio);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "新增批次紀錄失敗");
      setSubmitting(false);
    }
  }

  const lifecyclePlan = lifecyclePlanQuery.data ?? null;
  const planLoading = lifecyclePlanQuery.isLoading;
  const planError = lifecyclePlanQuery.error instanceof Error ? lifecyclePlanQuery.error.message : null;
  const recordedCondition = lifecyclePlan?.add_entry_condition;

  return (
    <div
      ref={backdropRef}
      onMouseDown={(e) => {
        mouseDownOnBackdrop.current = e.target === backdropRef.current;
      }}
      onClick={(e) => {
        if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose();
        mouseDownOnBackdrop.current = false;
      }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-card shadow-xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border-subtle bg-card px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">新增批次記錄 · {portfolioDisplayName(item)}</p>
            <p className="text-xs text-text-faint">
              目前 {item.quantity} 股，平均成本 {formatPrice(item.entry_price, item.symbol)}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm dark:border-indigo-900 dark:bg-indigo-950">
            <p className="text-xs font-semibold text-indigo-700 dark:text-indigo-300">原始新增批次條件</p>
            {planLoading ? (
              <p className="mt-1 text-text-muted">讀取原始計畫中…</p>
            ) : planError ? (
              <p className="mt-1 text-text-muted">無法讀取原始新增批次條件：{planError}</p>
            ) : recordedCondition && recordedCondition !== "not_recorded" ? (
              <p className="mt-1 font-medium text-text-primary">{ADD_ENTRY_CONDITION_LABEL[recordedCondition]}</p>
            ) : (
              <p className="mt-1 text-text-muted">未記錄原始新增批次條件。</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">新增批次日期</span>
              <input
                type="date"
                value={eventDate}
                onChange={(e) => setEventDate(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">新增批次價格</span>
              <input
                type="number"
                step="0.01"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">新增批次股數</span>
              <input
                type="number"
                step="1"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">手續費（留空自動估算）</span>
              <input
                type="number"
                step="0.01"
                value={fees}
                onChange={(e) => setFees(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">交易稅（買入通常為 0）</span>
              <input
                type="number"
                step="0.01"
                value={taxes}
                onChange={(e) => setTaxes(e.target.value)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </label>
          </div>
          <p className="text-xs leading-relaxed text-text-faint">
            留空時手續費使用後端台股預設費率估算，買入交易稅預設為 0；實際成本可手動覆寫。
          </p>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-muted">新增批次理由</span>
            <select
              value={reasonCode}
              onChange={(e) => setReasonCode(e.target.value as AddEntryReasonCode)}
              className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              {ADD_ENTRY_REASON_CODE_VALUES.map((value) => (
                <option key={value} value={value}>
                  {ADD_ENTRY_REASON_CODE_LABEL[value]}
                </option>
              ))}
            </select>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">是否符合原計畫</span>
              <select
                value={planAdherence}
                onChange={(e) => setPlanAdherence(e.target.value as PlanAdherence)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                {PLAN_ADHERENCE_VALUES.map((value) => (
                  <option key={value} value={value}>
                    {PLAN_ADHERENCE_LABEL[value]}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-text-muted">決策信心</span>
              <select
                value={confidenceLevel}
                onChange={(e) => setConfidenceLevel(e.target.value as DecisionConfidenceLevel)}
                className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                {DECISION_CONFIDENCE_LEVEL_VALUES.map((value) => (
                  <option key={value} value={value}>
                    {CONFIDENCE_LEVEL_LABEL[value]}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {planAdherence === "no" && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
              此新增批次會被明確記錄為違反原始新增批次計畫，供日後交易檢討使用。
            </div>
          )}

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-muted">備註（選填）</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              className="w-full resize-none rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </label>

          {error && (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </p>
          )}
        </div>

        <div className="sticky bottom-0 flex justify-end gap-2 border-t border-border-subtle bg-card px-5 py-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:bg-card-hover"
          >
            取消
          </button>
          <button
            onClick={handleAddEntry}
            disabled={submitting}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {submitting ? "記錄中…" : "確認記錄"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Delete Confirm Modal ─────────────────────────────────────

interface DeleteConfirmModalProps {
  item: PortfolioItem;
  onClose: () => void;
  onDeleted: (id: number) => void;
}

function DeleteConfirmModal({ item, onClose, onDeleted }: DeleteConfirmModalProps) {
  const deletePortfolioItemMutation = useDeletePortfolioItemMutation();
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleDelete() {
    setError(null);
    setDeleting(true);
    try {
      await deletePortfolioItemMutation.mutateAsync(item.id);
      onDeleted(item.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "刪除失敗");
      setDeleting(false);
    }
  }

  return (
    <div
      ref={backdropRef}
      onMouseDown={(e) => {
        mouseDownOnBackdrop.current = e.target === backdropRef.current;
      }}
      onClick={(e) => {
        if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose();
        mouseDownOnBackdrop.current = false;
      }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="w-full max-w-sm rounded-2xl bg-card shadow-xl">
        <div className="p-5">
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-red-100 dark:bg-red-950">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5 text-red-600 dark:text-red-400"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <p className="font-semibold text-text-primary">刪除 {portfolioDisplayName(item)} 持股？</p>
          <p className="mt-1.5 text-sm text-text-muted">
            此操作將同時移除所有歷史診斷紀錄，且
            <span className="font-medium text-red-600 dark:text-red-400">無法復原</span>。
          </p>
          {error && (
            <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-border-subtle px-5 py-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:bg-card-hover"
          >
            取消
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            {deleting ? "刪除中…" : "確認刪除"}
          </button>
        </div>
      </div>
    </div>
  );
}

const STATUS_CONFIG = {
  profitable_safe: {
    label: "獲利安全區",
    color: "text-green-700 dark:text-green-400",
    bg: "bg-green-50 border-green-200 dark:bg-green-950 dark:border-green-800",
    dot: "🟢",
  },
  at_risk: {
    label: "成本邊緣",
    color: "text-yellow-700 dark:text-yellow-400",
    bg: "bg-yellow-50 border-yellow-200 dark:bg-yellow-950 dark:border-yellow-800",
    dot: "🟡",
  },
  under_water: {
    label: "套牢防守",
    color: "text-red-700 dark:text-red-400",
    bg: "bg-red-50 border-red-200 dark:bg-red-950 dark:border-red-800",
    dot: "🔴",
  },
} as const;

const RISK_STATE_CONFIG = {
  stable: {
    label: "風險穩定",
    color: "text-green-700 dark:text-green-400",
    bg: "bg-green-50 border-green-200 dark:bg-green-950 dark:border-green-800",
  },
  watch: {
    label: "需要觀察",
    color: "text-yellow-700 dark:text-yellow-400",
    bg: "bg-yellow-50 border-yellow-200 dark:bg-yellow-950 dark:border-yellow-800",
  },
  elevated: {
    label: "風險升高",
    color: "text-yellow-700 dark:text-yellow-400",
    bg: "bg-yellow-50 border-yellow-200 dark:bg-yellow-950 dark:border-yellow-800",
  },
  critical: {
    label: "防守條件觸發",
    color: "text-red-700 dark:text-red-400",
    bg: "bg-red-50 border-red-200 dark:bg-red-950 dark:border-red-800",
  },
} as const;

function legacyActionRiskLabel(action: string | null | undefined): string | null {
  if (action === "Exit") return "防守條件觸發";
  if (action === "Trim") return "風險升高";
  if (action === "Hold") return "風險穩定";
  return action ?? null;
}

function legacyActionRiskClass(action: string | null | undefined): string {
  if (action === "Exit") return "bg-red-50 text-red-600 border border-red-200";
  if (action === "Trim") return "bg-yellow-50 text-yellow-600 border border-yellow-200";
  if (action === "Hold") return "bg-green-50 text-green-600 border border-green-200";
  return "bg-badge-neutral-bg text-badge-neutral-text";
}

function historyRiskClass(riskState: string | null | undefined, action: string | null | undefined): string {
  if (riskState === "critical") return "bg-red-50 text-red-600 border border-red-200";
  if (riskState === "elevated" || riskState === "watch") return "bg-yellow-50 text-yellow-600 border border-yellow-200";
  if (riskState === "stable") return "bg-green-50 text-green-600 border border-green-200";
  return legacyActionRiskClass(action);
}

function historyRiskTextClass(riskState: string | null | undefined, action: string | null | undefined): string {
  if (riskState === "critical") return "text-red-600 font-semibold";
  if (riskState === "elevated" || riskState === "watch") return "text-yellow-600 font-semibold";
  if (riskState === "stable") return "text-green-600 font-semibold";
  if (action === "Exit") return "text-red-600 font-semibold";
  if (action === "Trim") return "text-yellow-600 font-semibold";
  if (action === "Hold") return "text-green-600 font-semibold";
  return "text-text-secondary";
}

const BATCH_STATUS_STYLES = {
  running: {
    container: "border-indigo-200 bg-indigo-50 dark:border-indigo-800 dark:bg-indigo-950",
    text: "text-indigo-700 dark:text-indigo-300",
  },
  done: {
    container: "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950",
    text: "text-green-700 dark:text-green-300",
  },
  partialError: {
    container: "border-yellow-200 bg-yellow-50 dark:border-yellow-800 dark:bg-yellow-950",
    text: "text-yellow-700 dark:text-yellow-300",
  },
} as const;

const PORTFOLIO_RISK_BUDGET_LABEL = {
  available: "可能回吐可控",
  watch: "回吐空間偏大",
  constrained: "可能回吐過高",
  unknown: "暫無法估算",
} as const;

const PHASE1_LABEL_STYLE = {
  加碼: "border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-300",
  建倉: "border-indigo-200 bg-indigo-50 text-indigo-700 dark:border-indigo-800 dark:bg-indigo-950 dark:text-indigo-300",
  續抱: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300",
  停損警戒: "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300",
  資料不足: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
} as const;

const PHASE1_HOLDING_LIST_CONFIG: Array<{
  key: PortfolioPhase1CurrentDayListKey;
  title: string;
  description: string;
}> = [
  {
    key: "holding_risk_alerts",
    title: "持股風險警戒",
    description: "優先檢查支撐與風險控制條件。",
  },
  {
    key: "holding_management_candidates",
    title: "持股管理觀察",
    description: "追蹤續抱、加碼觀察或獲利保護狀態。",
  },
];

function formatPortfolioMoney(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: 0,
  }).format(value);
}

function formatPortfolioPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(2)}%`;
}

function formatPhase1AnchorType(type: string | null | undefined): string {
  if (!type) return "觀察錨點";
  const labels: Record<string, string> = {
    breakout: "突破錨點",
    breakout_20d: "20 日突破錨點",
    entry: "進場錨點",
    entry_date: "進場錨點",
    high_volume: "大量錨點",
    high_volume_60d: "60 日大量錨點",
    swing_low: "波段低點錨點",
    swing_low_60d: "60 日低點錨點",
  };
  return labels[type] ?? type;
}

function formatPhase1Distance(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function phase1DistanceClass(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "text-text-faint";
  return value >= 0 ? "text-emerald-600 dark:text-emerald-300" : "text-red-600 dark:text-red-300";
}

function phase1ObservationDisplayName(item: PortfolioPhase1ObservationItem): string {
  return item.name ? `${item.name} ${item.symbol}` : item.symbol;
}

function formatPhase1ObservationState(state: string | undefined): string {
  const labels: Record<string, string> = {
    add_watch: "加碼觀察",
    data_unavailable: "資料不足",
    exit_risk: "停損警戒",
    hold: "續抱",
    overheated: "過熱",
    profit_take_watch: "獲利保護",
    pullback_watch: "回測觀察",
    range_watch: "區間觀察",
    strong_breakout: "突破觀察",
    warning: "警戒",
  };
  return state ? (labels[state] ?? state) : "觀察";
}

function phase1BadgeText(item: PortfolioPhase1ObservationItem): string {
  return item.label ?? formatPhase1ObservationState(item.position_state);
}

function phase1BadgeClass(item: PortfolioPhase1ObservationItem): string {
  if (item.label) return PHASE1_LABEL_STYLE[item.label];
  if (item.position_state === "overheated") {
    return "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300";
  }
  return PHASE1_LABEL_STYLE["資料不足"];
}

function findRiskCaveatCount(summary: PortfolioRiskSummary, code: string): number {
  return summary.data_quality.caveats.find((caveat) => caveat.code === code)?.count ?? 0;
}

function riskBudgetDisplay(summary: PortfolioRiskSummary): { label: string; note: string } {
  const missingDefenseCount = findRiskCaveatCount(summary, "missing_defense_reference");
  const missingPriceCount = findRiskCaveatCount(summary, "missing_price");
  const zeroQuantityCount = findRiskCaveatCount(summary, "zero_quantity");

  if (missingDefenseCount > 0) {
    return {
      label: "估算不完整",
      note: `${missingDefenseCount} 檔缺停損價，先補齊後再判讀可能回吐。`,
    };
  }
  if (missingPriceCount > 0) {
    return {
      label: "估算不完整",
      note: `${missingPriceCount} 檔缺近期價格，先更新價格後再判讀可能回吐。`,
    };
  }
  if (zeroQuantityCount > 0) {
    return {
      label: "估算不完整",
      note: `${zeroQuantityCount} 檔數量為 0，未納入可能回吐判讀。`,
    };
  }

  return {
    label: PORTFOLIO_RISK_BUDGET_LABEL[summary.risk_budget_status.status],
    note: `若照目前停損出場，可能回吐約 ${formatPortfolioPct(summary.total_at_risk_pct)} 的持股市值。`,
  };
}

function stopLossPullbackPct(risk: PortfolioPositionRiskItem): number | null {
  if (risk.current_price == null || risk.defense_reference.price == null || risk.current_price <= 0) return null;
  return Math.max(0, ((risk.current_price - risk.defense_reference.price) / risk.current_price) * 100);
}

function stopLossCheckReason(summary: PortfolioRiskSummary): string {
  const missingDefenseCount = findRiskCaveatCount(summary, "missing_defense_reference");
  const missingPriceCount = findRiskCaveatCount(summary, "missing_price");
  const zeroQuantityCount = findRiskCaveatCount(summary, "zero_quantity");
  if (missingDefenseCount > 0) return `${missingDefenseCount} 檔缺停損價，暫時不能完整判斷。`;
  if (missingPriceCount > 0) return `${missingPriceCount} 檔缺近期價格，暫時不能完整判斷。`;
  if (zeroQuantityCount > 0) return `${zeroQuantityCount} 檔數量為 0，已排除在估算外。`;

  const contributors = summary.position_risks
    .filter((risk) => (risk.estimated_risk_amount ?? 0) > 0)
    .sort((a, b) => (b.estimated_risk_amount ?? 0) - (a.estimated_risk_amount ?? 0));
  const primary = contributors[0];
  if (!primary) return "目前持股已接近或低於停損價，停損前可能回吐為 0。";

  const primaryName = portfolioDisplayName(primary);
  if (contributors.length === 1) {
    return `原因：主要來自 ${primaryName}，若跌到停損價，可能回吐 ${formatPortfolioMoney(primary.estimated_risk_amount)}。`;
  }

  const contributionPct =
    summary.total_at_risk > 0 && primary.estimated_risk_amount != null
      ? (primary.estimated_risk_amount / summary.total_at_risk) * 100
      : null;
  return `原因：${contributors.length} 檔持股合計可能回吐 ${formatPortfolioMoney(summary.total_at_risk)}，其中 ${primaryName} 佔 ${formatPortfolioPct(contributionPct)}。`;
}

function totalRiskExplanation(summary: PortfolioRiskSummary): string | null {
  if (summary.total_at_risk !== 0) return null;
  const missingDefenseCount = findRiskCaveatCount(summary, "missing_defense_reference");
  const missingPriceCount = findRiskCaveatCount(summary, "missing_price");
  const touchedReferenceCount = summary.position_risks.filter(
    (risk) =>
      risk.current_price != null &&
      risk.defense_reference.price != null &&
      risk.current_price <= risk.defense_reference.price,
  ).length;

  if (missingDefenseCount > 0) return `${missingDefenseCount} 檔缺停損價，未納入可能回吐估算。`;
  if (missingPriceCount > 0) return `${missingPriceCount} 檔缺近期價格，未納入可能回吐估算。`;
  if (touchedReferenceCount > 0) return `${touchedReferenceCount} 檔已觸及或低於停損價，剩餘可能回吐為 0。`;
  return null;
}

function hasPhase1CurrentDayLists(summary: PortfolioRiskSummary): boolean {
  const lists = summary.phase1_current_day_lists;
  if (!lists) return false;
  return PHASE1_HOLDING_LIST_CONFIG.some((config) => lists.implemented_lists.includes(config.key));
}

function PortfolioPhase1ObservationRow({ item }: { item: PortfolioPhase1ObservationItem }) {
  const anchor = item.display_anchor;
  const estimated = item.data_quality.estimated === true || anchor?.estimated === true;

  return (
    <article className="border-t border-border-subtle px-3 py-3 first:border-t-0">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-text-primary">{phase1ObservationDisplayName(item)}</p>
          {anchor?.anchor_date && (
            <p className="mt-1 text-xs text-text-faint">
              {formatPhase1AnchorType(anchor.type)}：{anchor.anchor_date}
            </p>
          )}
        </div>
        <span className={`shrink-0 rounded-md border px-2 py-0.5 text-xs font-medium ${phase1BadgeClass(item)}`}>
          {phase1BadgeText(item)}
        </span>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-text-secondary">{item.current_day_observation}</p>
      {item.matched_rules.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {item.matched_rules.slice(0, 2).map((rule) => (
            <span
              key={rule}
              className="max-w-full break-words rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text"
            >
              {rule}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 grid grid-cols-3 gap-2 rounded-lg border border-border-subtle bg-card px-3 py-2 text-xs">
        <div className="min-w-0">
          <p className="text-text-faint">現價</p>
          <p className="mt-0.5 truncate font-mono font-semibold text-text-primary">
            {formatPrice(item.close, item.symbol)}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-text-faint">成本</p>
          <p className="mt-0.5 truncate font-mono font-semibold text-text-primary">
            {formatPrice(item.holding_avg_cost, item.symbol)}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-text-faint">相對錨點</p>
          <p
            className={`mt-0.5 truncate font-mono font-semibold ${phase1DistanceClass(anchor?.distance_to_avwap_pct)}`}
          >
            {formatPhase1Distance(anchor?.distance_to_avwap_pct)}
          </p>
        </div>
      </div>
      {estimated && <p className="mt-2 text-xs text-amber-600 dark:text-amber-300">日資料估算</p>}
    </article>
  );
}

function PortfolioPhase1CurrentDayPanel({ summary }: { summary: PortfolioRiskSummary }) {
  const lists = summary.phase1_current_day_lists;
  if (!lists || !hasPhase1CurrentDayLists(summary)) return null;

  const holdingGroups = PHASE1_HOLDING_LIST_CONFIG.filter((config) => lists.implemented_lists.includes(config.key)).map(
    (config) => ({
      ...config,
      items: lists[config.key],
    }),
  );
  const totalHoldingItems = holdingGroups.reduce((total, group) => total + group.items.length, 0);

  return (
    <div className="mt-4 border-t border-border-subtle pt-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-text-primary">持股 AVWAP 觀察</h4>
          <p className="mt-1 text-xs text-text-faint">
            只顯示目前持股對應的 AVWAP 讀取結果；非持股候選請回 Daily Radar 或關注清單查看。
          </p>
        </div>
        <span className="rounded-md border border-border-subtle bg-card-hover px-2 py-1 text-xs text-text-muted">
          {totalHoldingItems} 筆
        </span>
      </div>

      {totalHoldingItems === 0 ? (
        <p className="mt-3 rounded-lg border border-border-subtle bg-background px-3 py-2 text-xs text-text-muted">
          目前沒有持股進入 AVWAP 觀察清單。
        </p>
      ) : (
        <Phase1ObservationGroupList groups={holdingGroups} />
      )}
    </div>
  );
}

function Phase1ObservationGroupList({
  groups,
}: {
  groups: Array<{
    key: PortfolioPhase1CurrentDayListKey;
    title: string;
    description: string;
    items: PortfolioPhase1ObservationItem[];
  }>;
}) {
  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      {groups.map((group) => (
        <section key={group.key} className="min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-text-primary">{group.title}</p>
              <p className="mt-0.5 text-xs text-text-faint">{group.description}</p>
            </div>
            <span className="shrink-0 text-xs text-text-faint">{group.items.length}</span>
          </div>
          {group.items.length > 0 ? (
            <div className="mt-2 overflow-hidden rounded-lg border border-border-subtle bg-background">
              {group.items.map((item) => (
                <PortfolioPhase1ObservationRow key={`${group.key}-${item.symbol}`} item={item} />
              ))}
            </div>
          ) : (
            <p className="mt-2 rounded-lg border border-border-subtle bg-background px-3 py-2 text-xs text-text-muted">
              目前沒有符合條件的持股。
            </p>
          )}
        </section>
      ))}
    </div>
  );
}

function PortfolioRiskSummaryPanel({ summary, error }: { summary: PortfolioRiskSummary | null; error: string | null }) {
  const [expanded, setExpanded] = useState(false);

  if (error) {
    return (
      <section className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
        停損檢查暫不可用：{error}
      </section>
    );
  }
  if (!summary) return null;

  const riskExplanation = totalRiskExplanation(summary);
  const riskBudget = riskBudgetDisplay(summary);
  const stopCheckReason = stopLossCheckReason(summary);

  return (
    <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">停損檢查</h3>
          <p className="mt-1 text-xs text-text-faint">檢查目前停損設定下，整體可能回吐多少。</p>
        </div>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-border-subtle bg-background px-3 py-2">
          <p className="text-xs text-text-faint">總市值</p>
          <p className="mt-1 text-sm font-semibold text-text-primary">
            {formatPortfolioMoney(summary.portfolio_value)}
          </p>
        </div>
        <div className="rounded-lg border border-border-subtle bg-background px-3 py-2">
          <p className="text-xs text-text-faint">未實現損益</p>
          <p
            className={`mt-1 text-sm font-semibold ${summary.total_unrealized_pnl >= 0 ? "text-green-600" : "text-red-600"}`}
          >
            {summary.total_unrealized_pnl > 0 ? "+" : ""}
            {formatPortfolioMoney(summary.total_unrealized_pnl)}
          </p>
        </div>
      </div>

      <div
        className={`mt-3 flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3 ${
          riskExplanation ? "justify-between" : "justify-end"
        }`}
      >
        {riskExplanation && <div className="min-w-0 text-xs text-text-faint">{riskExplanation}</div>}
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          className="rounded-lg border border-border-subtle bg-background px-3 py-1.5 text-xs font-medium text-text-secondary hover:bg-card-hover hover:text-text-primary"
        >
          {expanded ? "收起停損細節" : "展開停損細節"}
        </button>
      </div>

      {expanded && (
        <>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg border border-border-subtle bg-background px-3 py-2">
              <p className="text-xs text-text-faint">若照停損出場</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">
                可能回吐 {formatPortfolioMoney(summary.total_at_risk)}
                <span className="ml-1 text-xs font-normal text-text-faint">
                  {formatPortfolioPct(summary.total_at_risk_pct)}
                </span>
              </p>
              <p className="mt-1 text-xs text-text-faint">佔目前持股總市值的比例</p>
            </div>
            <div className="rounded-lg border border-border-subtle bg-background px-3 py-2">
              <p className="text-xs text-text-faint">為什麼是這個狀態</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">{riskBudget.label}</p>
              <p className="mt-1 text-xs leading-relaxed text-text-faint">{stopCheckReason}</p>
            </div>
          </div>

          <PortfolioPhase1CurrentDayPanel summary={summary} />
        </>
      )}
    </section>
  );
}

interface AnalysisModalProps {
  item: PortfolioItem;
  result: PositionResult | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

function AnalysisModal({ item, result, loading, error, onClose }: AnalysisModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);
  const pa = result?.position_analysis;
  const disciplineTriggers = pa?.discipline_triggers ?? [];
  const observationConditions = pa?.observation_conditions ?? [];

  function handleBackdropMouseDown(e: React.MouseEvent) {
    mouseDownOnBackdrop.current = e.target === backdropRef.current;
  }

  function handleBackdropClick(e: React.MouseEvent) {
    if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose();
    mouseDownOnBackdrop.current = false;
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      ref={backdropRef}
      onMouseDown={handleBackdropMouseDown}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-2xl bg-card shadow-xl">
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border-subtle bg-card px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">{portfolioDisplayName(item)} 持股診斷</p>
            <p className="text-xs text-text-faint">
              成本 {formatPrice(item.entry_price, item.symbol)}
              {item.quantity > 0 && ` ｜ ${item.quantity} 股`}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 p-5">
          {loading && (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <div
                className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400"
                style={{ animationDuration: "1s" }}
              />
              <p className="text-sm font-medium text-text-primary">AI 持倉診斷中</p>
              <p className="text-xs text-text-muted">通常需要 15–30 秒</p>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </div>
          )}

          {result && pa && (
            <>
              {disciplineTriggers.length > 0 && (
                <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950">
                  <div className="mb-1 font-semibold text-red-700 dark:text-red-400">紀律觸發</div>
                  <ul className="space-y-1 text-sm text-red-700 dark:text-red-400">
                    {disciplineTriggers.map((trigger, index) => (
                      <li key={`${trigger}-${index}`}>{trigger}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {pa.position_status && (
                  <article className={`rounded-xl border p-4 shadow-sm ${STATUS_CONFIG[pa.position_status].bg}`}>
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs font-semibold text-text-muted">倉位狀態</span>
                      <span className={`text-xs font-bold ${STATUS_CONFIG[pa.position_status].color}`}>
                        {STATUS_CONFIG[pa.position_status].dot} {STATUS_CONFIG[pa.position_status].label}
                      </span>
                    </div>
                    {pa.position_narrative && <p className="text-sm text-text-secondary">{pa.position_narrative}</p>}
                    <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                      <div className="text-center">
                        <div className="text-text-faint">成本</div>
                        <div className="font-mono font-medium text-text-secondary">
                          {formatPrice(pa.entry_price, item.symbol)}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-text-faint">現價</div>
                        <div className="font-mono font-medium text-text-secondary">
                          {formatPrice(result.snapshot?.current_price as number | undefined, item.symbol)}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-text-faint">損益</div>
                        <div
                          className={`font-mono font-medium ${
                            pa.profit_loss_pct != null && pa.profit_loss_pct >= 0 ? "text-green-600" : "text-red-600"
                          }`}
                        >
                          {pa.profit_loss_pct != null
                            ? `${pa.profit_loss_pct > 0 ? "+" : ""}${pa.profit_loss_pct.toFixed(2)}%`
                            : "—"}
                        </div>
                      </div>
                    </div>
                  </article>
                )}

                {pa.risk_state && RISK_STATE_CONFIG[pa.risk_state] && (
                  <article className={`rounded-xl border p-4 shadow-sm ${RISK_STATE_CONFIG[pa.risk_state].bg}`}>
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs font-semibold text-text-muted">風險狀態</span>
                      <span className={`text-xl font-bold ${RISK_STATE_CONFIG[pa.risk_state].color}`}>
                        {pa.risk_state_label ?? RISK_STATE_CONFIG[pa.risk_state].label}
                      </span>
                    </div>
                    <div className="flex justify-between text-xs text-text-muted">
                      <span>風險控制參考</span>
                      <span className="font-mono font-medium text-orange-600 dark:text-orange-400">
                        {formatPrice(pa.risk_control_reference?.reference_price ?? pa.trailing_stop, item.symbol)}
                      </span>
                    </div>
                    {pa.risk_control_reference?.reason && (
                      <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                        {pa.risk_control_reference.reason}
                      </p>
                    )}
                    {observationConditions.length > 0 && (
                      <ul className="mt-3 space-y-1 text-xs leading-relaxed text-text-secondary">
                        {observationConditions.slice(0, 3).map((condition, index) => (
                          <li key={`${condition}-${index}`}>{condition}</li>
                        ))}
                      </ul>
                    )}
                    {pa.recommended_action && (
                      <details className="mt-3 text-xs text-text-faint">
                        <summary className="cursor-pointer">相容欄位（secondary）</summary>
                        <div className="mt-1 space-y-1">
                          <p>recommended_action: {pa.recommended_action}</p>
                          {pa.exit_reason && <p>exit_reason: {pa.exit_reason}</p>}
                        </div>
                      </details>
                    )}
                  </article>
                )}
              </div>

              {result.analysis_detail?.final_verdict && (
                <article className="rounded-xl border border-indigo-100 bg-indigo-50 p-4 shadow-sm dark:border-indigo-900 dark:bg-indigo-950">
                  <div className="mb-2 text-xs font-semibold text-indigo-700 dark:text-indigo-400">綜合研判</div>
                  <InsightText text={result.analysis_detail.final_verdict} emptyText="—" />
                </article>
              )}

              {[
                { label: "技術面防守", content: result.analysis_detail?.tech_insight },
                { label: "主力動向", content: result.analysis_detail?.inst_insight },
                { label: "消息面風險", content: result.analysis_detail?.news_insight },
              ]
                .filter(({ content }) => content)
                .map(({ label, content }) => (
                  <article key={label} className="rounded-xl border border-border bg-card p-4 shadow-sm">
                    <div className="mb-2 text-xs font-semibold text-text-muted">{label}</div>
                    <InsightText text={content} emptyText="—" />
                  </article>
                ))}

              <p className="text-center text-xs text-text-faint">本診斷結果僅供研究與紀律檢查，不構成投資建議。</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

type BatchStatus = "idle" | "running" | "done" | "partialError";
type PortfolioId = PortfolioItem["id"];
type PortfolioIdKey = `${PortfolioId}`;

function portfolioIdKey(id: PortfolioId): PortfolioIdKey {
  return String(id) as PortfolioIdKey;
}

function portfolioDisplayName(item: { symbol: string; name?: string | null }): string {
  return item.name ? `${item.name} ${item.symbol}` : item.symbol;
}

function portfolioCardDisplayName(
  item: PortfolioItem,
  namesBySymbol: Map<string, string>,
): { primary: string; secondary: string | null } {
  const name = item.name?.trim() || namesBySymbol.get(item.symbol) || null;
  return name ? { primary: name, secondary: item.symbol } : { primary: item.symbol, secondary: null };
}

export default function PortfolioPage({ onNavigateAnalyze: _onNavigateAnalyze }: PortfolioPageProps) {
  const queryClient = useQueryClient();
  const portfolioItemsQuery = usePortfolioItemsQuery();
  const portfolioRiskSummaryQuery = usePortfolioRiskSummaryQuery();
  const latestPortfolioHistoryQuery = useLatestPortfolioHistoryQuery();
  const decisionContextStatusQuery = useDecisionContextStatusQuery();
  const items = portfolioItemsQuery.data ?? [];
  const loading = portfolioItemsQuery.isLoading;
  const latestMap = (latestPortfolioHistoryQuery.data ?? {}) as Record<PortfolioIdKey, HistoryEntry | null>;
  const decisionContextStatusMap = decisionContextStatusQuery.data ?? {};
  const riskSummary = portfolioRiskSummaryQuery.data ?? null;
  const riskSummaryError =
    portfolioRiskSummaryQuery.error instanceof Error ? portfolioRiskSummaryQuery.error.message : null;
  const riskSummaryNamesBySymbol = useMemo(() => {
    const names = new Map<string, string>();
    for (const risk of riskSummary?.position_risks ?? []) {
      if (risk.name?.trim()) names.set(risk.symbol, risk.name.trim());
    }
    return names;
  }, [riskSummary]);
  const autoDefensePricesBySymbol = useMemo(() => {
    const prices = new Map<string, PortfolioRiskSummary["position_risks"][number]["auto_defense_prices"]>();
    for (const risk of riskSummary?.position_risks ?? []) {
      prices.set(risk.symbol, risk.auto_defense_prices);
    }
    return prices;
  }, [riskSummary]);
  const stopLossRiskBySymbol = useMemo(() => {
    const risks = new Map<string, PortfolioPositionRiskItem>();
    for (const risk of riskSummary?.position_risks ?? []) {
      risks.set(risk.symbol, risk);
    }
    return risks;
  }, [riskSummary]);
  const [historyMap, setHistoryMap] = useState<Record<number, HistoryEntry[]>>({});
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [historyLoading, setHistoryLoading] = useState<Record<number, boolean>>({});

  // Modal state
  const [modalItem, setModalItem] = useState<PortfolioItem | null>(null);
  const [analysisMap, setAnalysisMap] = useState<Record<number, PositionResult | null>>({});
  const [analysisLoading, setAnalysisLoading] = useState<Record<number, boolean>>({});
  const [analysisError, setAnalysisError] = useState<Record<number, string | null>>({});

  // Edit / Delete modal state
  const [editItem, setEditItem] = useState<PortfolioItem | null>(null);
  const [backfillItem, setBackfillItem] = useState<PortfolioItem | null>(null);
  const [addEntryItem, setAddEntryItem] = useState<PortfolioItem | null>(null);
  const [closeItem, setCloseItem] = useState<PortfolioItem | null>(null);
  const [deleteItem, setDeleteItem] = useState<PortfolioItem | null>(null);

  // Batch analysis state
  const batchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchStatus>("idle");
  const [batchProgress, setBatchProgress] = useState({ done: 0, total: 0 });
  const [batchFailedSymbols, setBatchFailedSymbols] = useState<string[]>([]);

  useEffect(() => {
    return () => {
      if (batchTimerRef.current) clearTimeout(batchTimerRef.current);
    };
  }, []);

  async function toggleHistory(id: number) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (historyMap[id]) return;
    setAsyncMapValue(setHistoryLoading, id, true);
    try {
      const body = await fetchPortfolioHistory(id, 20);
      setAsyncMapValue(setHistoryMap, id, body.records);
    } finally {
      setAsyncMapValue(setHistoryLoading, id, false);
    }
  }

  async function runPositionAnalysis(item: PortfolioItem): Promise<void> {
    setAsyncMapValue(setAnalysisLoading, item.id, true);
    setAsyncMapValue(setAnalysisError, item.id, null);
    try {
      const body: Record<string, unknown> = {
        symbol: item.symbol,
        entry_price: item.entry_price,
      };
      if (item.entry_date) body.entry_date = item.entry_date;
      if (item.quantity > 0) body.quantity = item.quantity;

      const data = await runPortfolioPositionAnalysis(body);
      setAsyncMapValue(setAnalysisMap, item.id, data);

      deleteAsyncMapValue(setHistoryMap, item.id);
      try {
        const hBody = await fetchPortfolioHistory(item.id, 20);
        queryClient.setQueryData<Record<PortfolioIdKey, HistoryEntry | null>>(
          portfolioKeys.latestHistory(),
          (current) => ({
            ...current,
            [portfolioIdKey(item.id)]: hBody.records[0] ?? null,
          }),
        );
        setAsyncMapValue(setHistoryMap, item.id, hBody.records);
      } catch {
        /* ignore */
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "請求失敗";
      setAsyncMapValue(setAnalysisError, item.id, msg);
      throw err; // re-throw so batch runner knows it failed
    } finally {
      setAsyncMapValue(setAnalysisLoading, item.id, false);
    }
  }

  async function openAnalysis(item: PortfolioItem): Promise<void> {
    setModalItem(item);
    await runPositionAnalysis(item).catch(() => {});
  }

  async function runBatchAnalysis(): Promise<void> {
    if (batchStatus === "running" || items.length === 0) return;

    setBatchStatus("running");
    setBatchProgress({ done: 0, total: items.length });
    setBatchFailedSymbols([]);

    const CONCURRENCY = 2;
    let index = 0;
    let completedCount = 0;
    const failed: string[] = [];

    async function runOne(item: PortfolioItem): Promise<void> {
      if (!analysisLoading[item.id]) {
        try {
          await runPositionAnalysis(item);
        } catch {
          try {
            await runPositionAnalysis(item);
          } catch {
            failed.push(item.symbol);
          }
        }
      }
      completedCount += 1;
      setBatchProgress({ done: completedCount, total: items.length });
    }

    async function worker(): Promise<void> {
      while (index < items.length) {
        const item = items[index];
        index += 1;
        await runOne(item);
      }
    }

    const workers = Array.from({ length: Math.min(CONCURRENCY, items.length) }, () => worker());
    await Promise.all(workers);

    setBatchFailedSymbols(failed);
    setBatchStatus(failed.length > 0 ? "partialError" : "done");

    batchTimerRef.current = setTimeout(() => {
      setBatchStatus("idle");
      setBatchProgress({ done: 0, total: 0 });
      setBatchFailedSymbols([]);
    }, 3000);
  }

  function clearLocalPositionState(sourceId: number) {
    setExpandedId((prev) => (prev === sourceId ? null : prev));
    deleteAsyncMapValue(setHistoryMap, sourceId);
    deleteAsyncMapValue(setAnalysisMap, sourceId);
    deleteAsyncMapValue(setAnalysisLoading, sourceId);
    deleteAsyncMapValue(setAnalysisError, sourceId);
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="h-4 w-16 animate-pulse rounded bg-border" />
          <div className="h-3 w-8 animate-pulse rounded bg-border" />
        </div>
        {[1, 2].map((i) => (
          <div key={i} className="rounded-xl border border-border bg-card p-4 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <div className="h-5 w-20 animate-pulse rounded bg-border" />
              <div className="h-5 w-16 animate-pulse rounded bg-border" />
            </div>
            <div className="h-3 w-32 animate-pulse rounded bg-border" />
            <div className="flex gap-2 pt-1">
              <div className="h-8 w-16 animate-pulse rounded-lg bg-border" />
              <div className="h-8 w-16 animate-pulse rounded-lg bg-border" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        {items.length === 0 ? (
          <div className="rounded-xl border border-border bg-card p-6 text-center text-sm text-text-faint shadow-sm">
            尚無追蹤中的持股。請至「個股分析」頁新增。
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">我的持股</h2>
              <span className="text-xs text-text-faint">共 {items.length} 筆</span>
            </div>

            <button
              onClick={runBatchAnalysis}
              disabled={batchStatus !== "idle"}
              className="rounded-lg bg-indigo-500 px-4 py-2.5 text-xs font-medium text-white hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {batchStatus === "running" ? "分析中…" : "一鍵全部分析"}
            </button>

            {batchStatus !== "idle" && (
              <div className={`rounded-xl border px-4 py-3 text-sm ${BATCH_STATUS_STYLES[batchStatus].container}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className={`font-medium ${BATCH_STATUS_STYLES[batchStatus].text}`}>
                    {batchStatus === "running" && `分析中 ${batchProgress.done}/${batchProgress.total}…`}
                    {batchStatus === "done" && `✓ 已更新 ${batchProgress.total} 筆分析結果`}
                    {batchStatus === "partialError" &&
                      `完成 ${batchProgress.total - batchFailedSymbols.length}/${batchProgress.total}，失敗：${batchFailedSymbols.join("、")}`}
                  </span>
                  {batchStatus === "running" && (
                    <div className="h-1.5 w-32 overflow-hidden rounded-full bg-indigo-100 dark:bg-indigo-900">
                      <div
                        className="h-full rounded-full bg-indigo-500 transition-all duration-300"
                        style={{
                          width: `${batchProgress.total > 0 ? (batchProgress.done / batchProgress.total) * 100 : 0}%`,
                        }}
                      />
                    </div>
                  )}
                </div>
              </div>
            )}

            <PortfolioRiskSummaryPanel summary={riskSummary} error={riskSummaryError} />

            {items.map((item) => {
              const itemKey = portfolioIdKey(item.id);
              const latest = latestMap[itemKey];
              const decisionStatus = decisionContextStatusMap[itemKey];
              const history = historyMap[item.id];
              const isExpanded = expandedId === item.id;
              const isAnalyzing = analysisLoading[item.id];
              const displayName = portfolioCardDisplayName(item, riskSummaryNamesBySymbol);
              const stopLossRisk = stopLossRiskBySymbol.get(item.symbol) ?? null;
              const stopLossPullbackPctValue = stopLossRisk ? stopLossPullbackPct(stopLossRisk) : null;

              return (
                <article key={item.id} className="rounded-xl border border-border bg-card shadow-sm">
                  <div className="p-4">
                    {/* Info row */}
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-semibold text-text-primary">{displayName.primary}</p>
                        {displayName.secondary && (
                          <p className="text-xs font-mono text-text-faint">{displayName.secondary}</p>
                        )}
                      </div>
                      {/* P/L badge — top-right, only when available */}
                      {(() => {
                        const closePrice = latest?.indicators?.close_price;
                        const plPct =
                          closePrice != null ? ((closePrice - item.entry_price) / item.entry_price) * 100 : null;
                        return plPct != null ? (
                          <span
                            className={`rounded-md px-2 py-0.5 text-xs font-mono font-medium ${
                              plPct >= 0
                                ? "bg-green-50 text-green-600 border border-green-200"
                                : "bg-red-50 text-red-600 border border-red-200"
                            }`}
                          >
                            {plPct > 0 ? "+" : ""}
                            {plPct.toFixed(2)}%
                          </span>
                        ) : null;
                      })()}
                    </div>

                    {/* Meta badges */}
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                        成本{" "}
                        <span className="font-medium text-text-primary">
                          {formatPrice(item.entry_price, item.symbol)}
                        </span>
                      </span>
                      {item.quantity > 0 && (
                        <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                          <span className="font-medium text-text-primary">{item.quantity}</span> 股
                        </span>
                      )}
                      <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                        {item.entry_date}
                      </span>
                      {decisionStatus && (
                        <span
                          className={`rounded-md border px-2 py-0.5 text-xs font-medium ${OPERATION_PLAN_STATUS_CLASS[decisionStatus.operation_plan_status]}`}
                        >
                          {OPERATION_PLAN_STATUS_LABEL[decisionStatus.operation_plan_status]}
                        </span>
                      )}
                    </div>

                    {/* Last analysis row */}
                    {latest &&
                      (() => {
                        const action = latest.recommended_action;
                        const actionLabel = latest.risk_state_label ?? legacyActionRiskLabel(action);
                        const actionBadge = historyRiskClass(latest.risk_state, action);
                        return (
                          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                            <span className="text-xs text-text-faint">上次分析：{latest.record_date}</span>
                            {actionLabel && (
                              <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${actionBadge}`}>
                                {actionLabel}
                              </span>
                            )}
                          </div>
                        );
                      })()}

                    {stopLossRisk && (
                      <div className="mt-3 rounded-lg border border-border-subtle bg-background px-3 py-2">
                        <p className="text-xs font-semibold text-text-primary">停損檢查</p>
                        {stopLossRisk.estimated_risk_amount != null ? (
                          <p className="mt-1 text-sm font-medium text-text-primary">
                            若跌到停損價，可能回吐 {formatPortfolioMoney(stopLossRisk.estimated_risk_amount)}
                            {stopLossPullbackPctValue != null && (
                              <span className="ml-1 text-xs font-normal text-text-faint">
                                {formatPortfolioPct(stopLossPullbackPctValue)}
                              </span>
                            )}
                          </p>
                        ) : (
                          <p className="mt-1 text-sm font-medium text-text-muted">停損前回吐暫無法估算</p>
                        )}
                        <div className="mt-1 flex flex-wrap gap-2 text-xs text-text-faint">
                          {stopLossRisk.defense_reference.price != null && (
                            <span>停損價 {formatPrice(stopLossRisk.defense_reference.price, item.symbol)}</span>
                          )}
                          {stopLossRisk.current_price != null && (
                            <span>現價 {formatPrice(stopLossRisk.current_price, item.symbol)}</span>
                          )}
                        </div>
                        {stopLossRisk.data_quality.caveats.length > 0 && (
                          <p className="mt-1 line-clamp-2 text-xs text-amber-600 dark:text-amber-300">
                            {stopLossRisk.data_quality.caveats.map((caveat) => caveat.message ?? caveat.code).join("；")}
                          </p>
                        )}
                      </div>
                    )}

                    {decisionStatus?.operation_plan_status === "missing" && (
                      <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div>
                            <p className="font-semibold">缺少 operation plan，可選擇補填 plan</p>
                            <p className="mt-1 text-xs leading-relaxed">
                              這是非必填提示：補填可改善日後 lifecycle
                              review，但不要求也不會阻擋即時分析、新增批次、結案批次或結案。
                            </p>
                          </div>
                          <button
                            onClick={() => setBackfillItem(item)}
                            className="shrink-0 rounded-lg border border-amber-300 bg-card px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-card-hover dark:border-amber-700 dark:bg-slate-900 dark:text-amber-300"
                          >
                            補填 plan
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Action buttons */}
                    <div className="mt-3 flex items-center gap-2">
                      <button
                        onClick={() => openAnalysis(item)}
                        disabled={isAnalyzing}
                        className="rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {isAnalyzing ? "分析中…" : "即時分析"}
                      </button>
                      <button
                        onClick={() => toggleHistory(item.id)}
                        className="rounded-lg px-3 py-1.5 text-xs text-text-muted hover:bg-card-hover"
                      >
                        {isExpanded ? "收起歷史" : "歷史紀錄"}
                      </button>
                      <button
                        onClick={() => setAddEntryItem(item)}
                        className="rounded-lg border border-green-200 bg-green-50 px-3 py-1.5 text-xs font-medium text-green-700 hover:bg-green-100 dark:border-green-800 dark:bg-green-950 dark:text-green-300 dark:hover:bg-green-900"
                      >
                        加碼
                      </button>
                      <div className="ml-auto flex items-center gap-1">
                        <button
                          onClick={() => setCloseItem(item)}
                          className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 dark:border-indigo-800 dark:bg-indigo-950 dark:text-indigo-300 dark:hover:bg-indigo-900"
                        >
                          出場
                        </button>
                        <button
                          onClick={() => setEditItem(item)}
                          title="編輯持股"
                          className="rounded-lg p-2 text-text-faint hover:bg-card-hover hover:text-text-secondary"
                        >
                          <svg
                            xmlns="http://www.w3.org/2000/svg"
                            className="h-4 w-4"
                            viewBox="0 0 20 20"
                            fill="currentColor"
                          >
                            <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                          </svg>
                        </button>
                        <button
                          onClick={() => setDeleteItem(item)}
                          title="刪除持股"
                          className="rounded-lg p-2 text-text-faint hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-950 dark:hover:text-red-400"
                        >
                          <svg
                            xmlns="http://www.w3.org/2000/svg"
                            className="h-4 w-4"
                            viewBox="0 0 20 20"
                            fill="currentColor"
                          >
                            <path
                              fillRule="evenodd"
                              d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z"
                              clipRule="evenodd"
                            />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="border-t border-border-subtle px-4 pb-4 pt-3">
                      {historyLoading[item.id] ? (
                        <p className="text-xs text-text-faint">載入中…</p>
                      ) : history && history.length > 0 ? (
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-left text-text-faint">
                              <th className="pb-1 font-medium">日期</th>
                              <th className="pb-1 font-medium">風險狀態</th>
                              <th className="pb-1 font-medium text-right">當時損益</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-border-subtle">
                            {history.map((row) => {
                              const action = row.recommended_action;
                              const actionColor = historyRiskTextClass(row.risk_state, action);
                              const actionLabel = row.risk_state_label ?? legacyActionRiskLabel(action) ?? "—";
                              const closePrice = row.indicators?.close_price;
                              const plPct =
                                closePrice != null ? ((closePrice - item.entry_price) / item.entry_price) * 100 : null;
                              return (
                                <tr key={row.record_date} className="text-text-secondary">
                                  <td className="py-1">{row.record_date}</td>
                                  <td className={`py-1 ${actionColor}`}>{actionLabel}</td>
                                  <td
                                    className={`py-1 text-right font-mono text-xs ${
                                      plPct == null ? "text-text-faint" : plPct >= 0 ? "text-green-600" : "text-red-600"
                                    }`}
                                  >
                                    {plPct == null ? "—" : `${plPct > 0 ? "+" : ""}${plPct.toFixed(2)}%`}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      ) : (
                        <p className="text-xs text-text-faint">尚無診斷紀錄。</p>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </>
        )}
      </div>

      {modalItem && (
        <AnalysisModal
          item={modalItem}
          result={analysisMap[modalItem.id] ?? null}
          loading={analysisLoading[modalItem.id] ?? false}
          error={analysisError[modalItem.id] ?? null}
          onClose={() => setModalItem(null)}
        />
      )}

      {editItem && (
        <EditPortfolioModal
          item={editItem}
          autoDefensePrices={autoDefensePricesBySymbol.get(editItem.symbol)}
          onClose={() => setEditItem(null)}
          onSaved={() => {
            setEditItem(null);
          }}
        />
      )}

      {backfillItem && (
        <BackfillPlanModal
          item={backfillItem}
          onClose={() => setBackfillItem(null)}
          onSaved={() => {
            setBackfillItem(null);
          }}
        />
      )}

      {addEntryItem && (
        <AddEntryModal
          item={addEntryItem}
          onClose={() => setAddEntryItem(null)}
          onAdded={() => {
            clearLocalPositionState(addEntryItem.id);
            setAddEntryItem(null);
          }}
        />
      )}

      {closeItem && (
        <ClosePositionModal
          item={closeItem}
          onClose={() => setCloseItem(null)}
          onClosed={(sourceItem) => {
            clearLocalPositionState(sourceItem.id);
            setCloseItem(null);
          }}
        />
      )}

      {deleteItem && (
        <DeleteConfirmModal
          item={deleteItem}
          onClose={() => setDeleteItem(null)}
          onDeleted={(id) => {
            clearLocalPositionState(id);
            setDeleteItem(null);
          }}
        />
      )}
    </>
  );
}
