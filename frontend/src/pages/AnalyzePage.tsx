import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { analyzeSymbol } from "../lib/analyzeApi";
import type { AnalyzeResponse } from "../lib/analysisTypes";
import { TechnicalIndicatorsPanel } from "../components/TechnicalIndicatorsPanel";
import { WorkspaceEmptyState } from "../components/app-shell/WorkspaceEmptyState";
import { useCreatePortfolioItemMutation } from "../features/portfolio/mutations";
import { fetchPortfolioItems, type CreatePortfolioRequest } from "../lib/portfolioApi";
import { createWatchlistItem, fetchWatchlistItems } from "../lib/watchlistApi";
import {
  buildTechnicalIndicatorsCopyText,
  COPY_STATUS_RESET_MS,
  getAnalyzeSymbolName,
  type CopyStatus,
  writeClipboardText,
} from "../lib/technicalIndicators";
import {
  type AddEntryCondition,
  type DefaultStopRule,
  type EntryRecordContext,
  type EntryRecordReason,
  type PlannedHoldingPeriod,
} from "../lib/portfolioTypes";
import {
  ADD_ENTRY_CONDITION_OPTIONS,
  DEFAULT_STOP_RULE_OPTIONS,
  ENTRY_RECORD_REASON_OPTIONS,
  PLANNED_HOLDING_PERIOD_OPTIONS,
} from "../lib/portfolioLabels";

interface AddPortfolioForm {
  entry_price: string;
  quantity: string;
  entry_date: string;
  entry_reason: EntryRecordReason | "";
  planned_holding_period: PlannedHoldingPeriod | "";
  default_stop_rule: DefaultStopRule | "";
  planned_stop_price: string;
  add_entry_condition: AddEntryCondition | "";
  notes: string;
}

const ACTION_TAG_MAP: Record<string, { emoji: string; label: string; color: string }> = {
  opportunity: { emoji: "🟢", label: "機會", color: "text-green-600" },
  overheated: { emoji: "🔴", label: "過熱", color: "text-red-600" },
  neutral: { emoji: "🔵", label: "中性", color: "text-blue-500" },
};

const SIGNAL_DIRECTION_BADGE: Record<string, { label: string; cls: string }> = {
  strong_bullish: { label: "強烈偏多", cls: "bg-emerald-100 text-emerald-800" },
  bullish: { label: "偏多", cls: "bg-green-100 text-green-800" },
  mixed: { label: "中性／混合", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
  bearish: { label: "偏空", cls: "bg-orange-100 text-orange-800" },
  strong_bearish: { label: "強烈偏空", cls: "bg-red-100 text-red-800" },
};

function signalDirectionLevel(
  score: number | null,
): "strong_bullish" | "bullish" | "mixed" | "bearish" | "strong_bearish" | null {
  if (score == null) return null;
  if (score >= 80) return "strong_bullish";
  if (score >= 60) return "bullish";
  if (score > 40) return "mixed";
  if (score > 20) return "bearish";
  return "strong_bearish";
}

function TriggersSection({
  upgradeTriggers,
  downgradeTriggers,
}: {
  upgradeTriggers?: string[];
  downgradeTriggers?: string[];
}) {
  const [open, setOpen] = useState(false);
  const hasUpgrade = upgradeTriggers && upgradeTriggers.length > 0;
  const hasDowngrade = downgradeTriggers && downgradeTriggers.length > 0;

  if (!hasUpgrade && !hasDowngrade) return null;

  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-text-muted hover:text-text-primary transition-colors"
      >
        <span>{open ? "▲" : "▼"}</span>
        條件變化
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {hasUpgrade && (
            <div>
              <p className="text-xs font-semibold text-emerald-600 mb-1">升級觸發</p>
              <ul className="space-y-0.5">
                {upgradeTriggers!.map((t, i) => (
                  <li key={i} className="text-xs text-text-primary flex gap-1.5">
                    <span className="text-emerald-500 shrink-0">↑</span>
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {hasDowngrade && (
            <div>
              <p className="text-xs font-semibold text-amber-600 mb-1">降級觸發</p>
              <ul className="space-y-0.5">
                {downgradeTriggers!.map((t, i) => (
                  <li key={i} className="text-xs text-text-primary flex gap-1.5">
                    <span className="text-amber-500 shrink-0">↓</span>
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function createInitialAddPortfolioForm(): AddPortfolioForm {
  return {
    entry_price: "",
    quantity: "",
    entry_date: new Date().toISOString().slice(0, 10),
    entry_reason: "",
    planned_holding_period: "",
    default_stop_rule: "",
    planned_stop_price: "",
    add_entry_condition: "",
    notes: "",
  };
}

function parseOptionalNumberInput(value: string): number | null | undefined {
  const trimmedValue = value.trim();
  if (trimmedValue === "") return undefined;
  const parsedValue = Number(trimmedValue);
  return Number.isFinite(parsedValue) ? parsedValue : null;
}

function formatPriceForInput(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return String(Number(value.toFixed(2)));
}

function derivePlannedStopPrice(
  rule: AddPortfolioForm["default_stop_rule"],
  indicators: AnalyzeResponse["technical_indicators"],
): number | null {
  if (!indicators) return null;

  const value =
    rule === "break_20d_low"
      ? indicators.low_20d
      : rule === "break_ma20"
        ? indicators.ma20
        : rule === "break_ma60"
          ? indicators.ma60
          : null;

  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function buildEntryRecord(
  addForm: AddPortfolioForm,
  plannedStopPrice: number | undefined,
): EntryRecordContext | undefined {
  const entryRecord: EntryRecordContext = {};
  const note = addForm.notes.trim();

  if (addForm.entry_reason) entryRecord.entry_reason = addForm.entry_reason;
  if (addForm.planned_holding_period) entryRecord.planned_holding_period = addForm.planned_holding_period;
  if (addForm.default_stop_rule) entryRecord.default_stop_rule = addForm.default_stop_rule;
  if (plannedStopPrice !== undefined) entryRecord.planned_stop_price = plannedStopPrice;
  if (addForm.add_entry_condition) entryRecord.add_entry_condition = addForm.add_entry_condition;
  if (note) entryRecord.note = note;

  return Object.keys(entryRecord).length > 0 ? entryRecord : undefined;
}

export default function AnalyzePage() {
  const createPortfolioItemMutation = useCreatePortfolioItemMutation();
  const addPortfolioTitleId = useId();
  const [searchParams] = useSearchParams();
  const querySymbol = searchParams.get("symbol") ?? "2330.TW";
  const [symbol, setSymbol] = useState(querySymbol);
  const symbolInputRef = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);

  useEffect(() => {
    setSymbol(querySymbol);
  }, [querySymbol]);

  const abortControllerRef = useRef<AbortController | null>(null);

  const [portfolioSymbols, setPortfolioSymbols] = useState<Set<string>>(new Set());
  const [watchlistSymbols, setWatchlistSymbols] = useState<Set<string>>(new Set());
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [watchlistStatus, setWatchlistStatus] = useState<"idle" | "success" | "error">("idle");
  const [watchlistMessage, setWatchlistMessage] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const addPortfolioDialogRef = useRef<HTMLDivElement>(null);
  const addPortfolioCloseButtonRef = useRef<HTMLButtonElement>(null);
  const [addForm, setAddForm] = useState<AddPortfolioForm>(() => createInitialAddPortfolioForm());
  const [addLoading, setAddLoading] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [technicalCopyStatus, setTechnicalCopyStatus] = useState<CopyStatus>("idle");
  const technicalCopyResetTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (technicalCopyResetTimerRef.current != null) {
        window.clearTimeout(technicalCopyResetTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!showAddModal) return;

    const previouslyFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    addPortfolioCloseButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setShowAddModal(false);
        return;
      }

      if (event.key !== "Tab") return;

      const dialog = addPortfolioDialogRef.current;
      if (!dialog) return;

      const focusableElements = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => element.getAttribute("aria-hidden") !== "true");
      if (focusableElements.length === 0) return;

      const firstFocusableElement = focusableElements[0];
      const lastFocusableElement = focusableElements[focusableElements.length - 1];
      const activeElement = document.activeElement;

      if (event.shiftKey && activeElement === firstFocusableElement) {
        event.preventDefault();
        lastFocusableElement.focus();
      } else if (!event.shiftKey && activeElement === lastFocusableElement) {
        event.preventDefault();
        firstFocusableElement.focus();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocusedElement?.focus();
    };
  }, [showAddModal]);

  function updateTechnicalCopyStatus(status: CopyStatus) {
    if (technicalCopyResetTimerRef.current != null) {
      window.clearTimeout(technicalCopyResetTimerRef.current);
    }

    setTechnicalCopyStatus(status);

    if (status !== "idle") {
      technicalCopyResetTimerRef.current = window.setTimeout(() => {
        setTechnicalCopyStatus("idle");
        technicalCopyResetTimerRef.current = null;
      }, COPY_STATUS_RESET_MS);
    }
  }

  async function fetchPortfolio() {
    try {
      const data = await fetchPortfolioItems();
      setPortfolioSymbols(new Set(data.map((r) => r.symbol.trim().toUpperCase())));
    } catch {
      /* ignore */
    }
  }

  async function fetchWatchlist() {
    try {
      const data = await fetchWatchlistItems();
      setWatchlistSymbols(new Set(data.map((item) => item.symbol.trim().toUpperCase())));
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    void fetchWatchlist();
  }, []);

  async function handleAddPortfolio(e: React.FormEvent) {
    e.preventDefault();
    setAddLoading(true);
    setAddError(null);
    try {
      const parsedStopPrice = parseOptionalNumberInput(addForm.planned_stop_price);
      if (parsedStopPrice === null) {
        setAddError("防守價必須是有效數字。");
        return;
      }
      if (parsedStopPrice != null && parsedStopPrice <= 0) {
        setAddError("防守價必須大於 0。");
        return;
      }
      if (addForm.default_stop_rule === "fixed_price" && parsedStopPrice == null) {
        setAddError("選擇固定防守價時，請填寫防守價。");
        return;
      }

      const entryRecord = buildEntryRecord(addForm, parsedStopPrice);
      const notes = addForm.notes.trim();
      const payload: CreatePortfolioRequest = {
        symbol,
        entry_price: parseFloat(addForm.entry_price),
        quantity: addForm.quantity ? parseInt(addForm.quantity) : 0,
        entry_date: addForm.entry_date,
        notes: notes || null,
      };

      if (entryRecord) payload.entry_record = entryRecord;

      await createPortfolioItemMutation.mutateAsync(payload);
      await fetchPortfolio();
      setShowAddModal(false);
      setAddForm(createInitialAddPortfolioForm());
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "新增失敗");
    } finally {
      setAddLoading(false);
    }
  }

  async function handleAddWatchlist() {
    const targetSymbol = typeof result?.snapshot.symbol === "string" ? result.snapshot.symbol : symbol;
    if (!targetSymbol.trim()) return;

    setWatchlistLoading(true);
    setWatchlistStatus("idle");
    setWatchlistMessage(null);
    try {
      const item = await createWatchlistItem({ symbol: targetSymbol.trim() });
      setWatchlistSymbols((current) => new Set(current).add(item.symbol.trim().toUpperCase()));
      setWatchlistStatus("success");
      setWatchlistMessage("已加入關注列表");
    } catch (err) {
      setWatchlistStatus("error");
      setWatchlistMessage(err instanceof Error ? err.message : "加入關注列表失敗");
    } finally {
      setWatchlistLoading(false);
    }
  }

  async function handleCopyTechnicalIndicators(): Promise<void> {
    if (!result) return;

    try {
      await writeClipboardText(buildTechnicalIndicatorsCopyText(result, snapshot));
      updateTechnicalCopyStatus("success");
    } catch {
      updateTechnicalCopyStatus("error");
    }
  }

  async function handleAnalyze() {
    if (!symbol.trim()) return;
    updateTechnicalCopyStatus("idle");
    setWatchlistStatus("idle");
    setWatchlistMessage(null);

    // 取消上一個尚未完成的請求
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setResult(null);
    try {
      const data = await analyzeSymbol({ symbol: symbol.trim() }, controller.signal);
      setResult(data);
      await Promise.all([fetchPortfolio(), fetchWatchlist()]);
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return; // 使用者已送出新請求，忽略
      const message = err instanceof Error ? err.message : "無法連線後端，請確認伺服器已啟動。";
      setResult({
        snapshot: {},
        symbol_name: null,
        analysis: "",
        confidence_score: null,
        cross_validation_note: null,
        strategy_type: null,
        entry_zone: null,
        stop_loss: null,
        holding_period: null,
        action_plan_tag: null,
        action_plan: null,
        risk_state: null,
        risk_state_label: null,
        discipline_triggers: [],
        observation_conditions: [],
        risk_control_reference: null,
        command_language_deprecated: {},
        institutional_flow_label: null,
        data_confidence: null,
        is_final: true,
        intraday_disclaimer: null,
        errors: [{ code: "NETWORK_ERROR", message }],
      });
    } finally {
      setLoading(false);
    }
  }

  const confidenceScore = result?.confidence_score ?? null;
  const signalDirection = signalDirectionLevel(confidenceScore);
  const firstError = result?.errors?.[0];
  const snapshot = result?.snapshot ?? {};
  const analyzedSymbol = typeof snapshot.symbol === "string" ? snapshot.symbol : symbol;
  const normalizedAnalyzedSymbol = analyzedSymbol.trim().toUpperCase();
  const isTracked = portfolioSymbols.has(normalizedAnalyzedSymbol);
  const isWatchlisted = watchlistSymbols.has(normalizedAnalyzedSymbol);
  const analyzedSymbolName = getAnalyzeSymbolName(result, snapshot);
  const analyzedDisplayName = analyzedSymbolName ? `${analyzedSymbolName} ${analyzedSymbol}` : analyzedSymbol;
  const autoPlannedStopPrice = derivePlannedStopPrice(addForm.default_stop_rule, result?.technical_indicators ?? null);
  const riskStateLabel = typeof result?.risk_state_label === "string" ? result.risk_state_label : "狀態未明";
  const observationConditions: string[] = Array.isArray(result?.observation_conditions)
    ? result.observation_conditions.filter((item): item is string => typeof item === "string")
    : [];
  const disciplineTriggers: string[] = Array.isArray(result?.discipline_triggers)
    ? result.discipline_triggers.filter((item): item is string => typeof item === "string")
    : [];
  const actionPlan = result?.action_plan ?? null;
  const actionPlanTargetZone: string | null =
    typeof actionPlan?.target_zone === "string" ? actionPlan.target_zone : null;
  const actionPlanDefenseLine: string | null =
    typeof actionPlan?.defense_line === "string" ? actionPlan.defense_line : null;
  const actionPlanMomentumExpectation: string | null =
    typeof actionPlan?.momentum_expectation === "string" ? actionPlan.momentum_expectation : null;
  const actionPlanSuggestedPositionSize: string | null =
    typeof actionPlan?.suggested_position_size === "string" ? actionPlan.suggested_position_size : null;
  const actionPlanUpgradeTriggers = Array.isArray(actionPlan?.upgrade_triggers)
    ? actionPlan.upgrade_triggers.filter((item): item is string => typeof item === "string")
    : undefined;
  const actionPlanDowngradeTriggers = Array.isArray(actionPlan?.downgrade_triggers)
    ? actionPlan.downgrade_triggers.filter((item): item is string => typeof item === "string")
    : undefined;
  const riskReference: unknown = result?.risk_control_reference?.reference;
  const riskControlReferenceText: string | null =
    typeof riskReference === "string" ? riskReference : actionPlanDefenseLine;
  const riskReferenceRows: Array<{ label: string; value: string; wide?: boolean; strong?: boolean }> = [];
  if (actionPlanTargetZone) riskReferenceRows.push({ label: "觀察區間", value: actionPlanTargetZone, strong: true });
  if (riskControlReferenceText)
    riskReferenceRows.push({ label: "風險控制參考", value: riskControlReferenceText, strong: true });
  if (actionPlanMomentumExpectation)
    riskReferenceRows.push({ label: "動能預期", value: actionPlanMomentumExpectation, wide: true });
  if (actionPlanSuggestedPositionSize)
    riskReferenceRows.push({ label: "部位規模參考", value: actionPlanSuggestedPositionSize, wide: true });
  const riskReferenceContent: ReactNode = riskReferenceRows.map((row) => (
    <div key={row.label} className={row.wide ? "col-span-2" : undefined}>
      <p className="text-xs text-text-muted">{String(row.label)}</p>
      <p className={`text-sm text-text-primary ${row.strong ? "font-medium" : ""}`}>{String(row.value)}</p>
    </div>
  ));

  function handleDefaultStopRuleChange(value: AddPortfolioForm["default_stop_rule"]) {
    const derivedStopPrice = derivePlannedStopPrice(value, result?.technical_indicators ?? null);
    setAddForm((form) => ({
      ...form,
      default_stop_rule: value,
      planned_stop_price: derivedStopPrice != null ? formatPriceForInput(derivedStopPrice) : "",
    }));
  }
  const observationContent: ReactNode =
    observationConditions.length > 0 ? (
      <div>
        <p className="text-xs font-semibold text-text-muted mb-1.5">觀察條件</p>
        <ul className="space-y-1">
          {observationConditions.map((point, i) => (
            <li key={i} className="flex gap-1.5 text-sm text-text-primary">
              <span className="text-text-muted shrink-0">·</span>
              {String(point)}
            </li>
          ))}
        </ul>
      </div>
    ) : null;
  const disciplineContent: ReactNode =
    disciplineTriggers.length > 0 ? (
      <div>
        <p className="text-xs font-semibold text-text-muted mb-1.5">紀律觸發</p>
        <ul className="space-y-1">
          {disciplineTriggers.map((cond, i) => (
            <li key={i} className="flex gap-1.5 text-sm text-text-primary">
              <span className="text-rose-400 shrink-0">⚠</span>
              {String(cond)}
            </li>
          ))}
        </ul>
      </div>
    ) : null;
  const legacyActionPlanAction = result?.command_language_deprecated?.action_plan_action;
  const legacyActionPlanActionText = typeof legacyActionPlanAction === "string" ? legacyActionPlanAction : null;

  return (
    <div className="space-y-6">
      {firstError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="font-semibold">[{firstError.code}]</span> {firstError.message}
        </div>
      )}

      {result?.is_final === false && result.intraday_disclaimer && (
        <div className="rounded-lg border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          {result.intraday_disclaimer}
        </div>
      )}

      <section className="overflow-hidden rounded-[14px] border border-border bg-surface-raised shadow-panel">
        <div className="border-b border-border-subtle px-4 py-4 md:px-6">
          <p className="text-[0.6875rem] font-semibold tracking-[0.14em] text-text-faint uppercase">研究入口</p>
          <div className="mt-2 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
            <h2 className="text-lg font-semibold text-text-primary">執行確定性研究</h2>
            <p className="text-xs text-text-muted">由後端計算技術、籌碼、基本面與風險紀律，不呼叫外部模型。</p>
          </div>
        </div>

        <div className="grid gap-4 px-4 py-4 md:grid-cols-[minmax(220px,0.75fr)_minmax(0,1.25fr)] md:px-6 md:py-5">
          <label htmlFor="symbol" className="space-y-2">
            <span className="block text-xs font-medium text-text-muted">股票代碼</span>
            <input
              id="symbol"
              ref={symbolInputRef}
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !loading && handleAnalyze()}
              className="ui-input font-medium tabular-nums"
              placeholder="例如 2330.TW 或 6488.TWO"
              disabled={loading}
            />
            <span className="block text-xs leading-relaxed text-text-faint">
              上市使用 .TW，上櫃使用 .TWO，例如 2330.TW、6488.TWO。
            </span>
          </label>

          <div className="flex items-end">
            <button
              type="button"
              onClick={() => handleAnalyze()}
              disabled={loading}
              className="group flex min-h-[4.75rem] w-full items-start gap-3 rounded-[10px] bg-accent px-4 py-3 text-left text-accent-contrast shadow-panel transition-[opacity,transform] duration-150 hover:opacity-90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transform-none motion-reduce:transition-none"
            >
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-canvas/20 text-sm font-semibold">
                算
              </span>
              <span>
                <span className="block text-sm font-semibold text-accent-contrast">
                  {loading ? "分析計算中" : "開始分析"}
                </span>
                <span className="mt-1 block text-xs leading-relaxed opacity-80">
                  行情、技術指標、AVWAP 與風險紀律，結果可直接複製到外部 AI。
                </span>
              </span>
            </button>
          </div>
        </div>

        {result && (
          <div className="ui-refresh-highlight flex flex-col gap-3 border-t border-border-subtle bg-card-hover/35 px-4 py-3 sm:flex-row sm:items-center sm:justify-between md:px-6">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-text-primary">{analyzedDisplayName}</p>
              <p className="mt-0.5 text-xs text-text-faint">
                確定性分析已更新，可加入追蹤或複製資料進行外部研究。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleAddWatchlist()}
                disabled={isWatchlisted || watchlistLoading}
                title={isWatchlisted ? "已在關注列表" : "加入關注列表"}
                className="ui-button-secondary min-h-10 px-3 text-xs"
              >
                {watchlistLoading ? "儲存中..." : isWatchlisted ? "已關注" : "加入關注"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setAddError(null);
                  setAddForm(createInitialAddPortfolioForm());
                  setShowAddModal(true);
                }}
                disabled={isTracked}
                title={isTracked ? "已追蹤" : "加入我的持股"}
                className="ui-button-secondary min-h-10 px-3 text-xs"
              >
                {isTracked ? "已追蹤" : "加入持股"}
              </button>
            </div>
          </div>
        )}
        {watchlistMessage && (
          <p
            className={`border-t border-border-subtle px-4 py-2 text-xs md:px-6 ${
              watchlistStatus === "error" ? "text-red-600 dark:text-red-400" : "text-emerald-700 dark:text-emerald-300"
            }`}
          >
            {watchlistMessage}
          </p>
        )}
      </section>

      {!result && !loading ? (
        <WorkspaceEmptyState
          eyebrow="Research ready"
          title="從一個明確標的開始"
          description="取得可回放的技術、籌碼與風險資料；分析完成後可加入關注、持股，或複製給外部 AI 深入研究。"
          meta="上市股票使用 .TW，上櫃股票使用 .TWO。"
          actions={
            <button
              type="button"
              onClick={() => {
                symbolInputRef.current?.focus();
              }}
              className="ui-button-secondary"
            >
              開始輸入標的
            </button>
          }
        />
      ) : (
        <section className="rounded-[14px] border border-border bg-surface-raised p-4 shadow-panel md:p-6">
          <div className="mb-1 flex items-center gap-2">
            <h2 className="text-sm font-semibold text-text-primary">{loading ? "研究進度" : "觀察與風險紀律"}</h2>
            {result?.action_plan_tag && ACTION_TAG_MAP[result.action_plan_tag] && (
              <span className={`text-sm font-medium ${ACTION_TAG_MAP[result.action_plan_tag].color}`}>
                {ACTION_TAG_MAP[result.action_plan_tag].emoji} {ACTION_TAG_MAP[result.action_plan_tag].label}
              </span>
            )}
          </div>
          <p className="mb-4 text-xs text-text-muted">
            {loading
              ? "完成後會先呈現技術資料與風險紀律，再補充其他研究面向。"
              : "用於評估是否納入觀察、等待條件與紀律觸發，不提供持股中的操作指令。"}
          </p>
          {loading ? (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <div
                className="h-10 w-10 animate-spin rounded-full border-4 border-accent-soft border-t-accent"
                style={{ animationDuration: "1s" }}
              />
              <p className="text-sm font-medium text-text-primary">資料分析中</p>
              <p className="text-xs text-text-muted">正在取得最新數據並計算指標與風險紀律</p>
            </div>
          ) : result ? (
            actionPlan ? (
              <div className="rounded-xl border border-border bg-card p-4">
                <div className="space-y-4">
                  <div>
                    <p className="text-xs font-medium text-text-muted">目前標的</p>
                    <p className="mt-1 text-lg font-semibold text-text-primary">{analyzedDisplayName}</p>
                  </div>

                  <div className="rounded-lg border border-border bg-card-hover/70 p-3">
                    <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px] md:items-center">
                      <div>
                        <div className="mb-1.5 flex flex-wrap items-center gap-2">
                          <p className="text-xs font-semibold text-text-muted">綜合訊號強度</p>
                          {signalDirection && (
                            <span
                              className={`rounded-full px-2 py-0.5 text-xs font-medium ${SIGNAL_DIRECTION_BADGE[signalDirection].cls}`}
                            >
                              {SIGNAL_DIRECTION_BADGE[signalDirection].label}
                            </span>
                          )}
                          {result.data_confidence != null && result.data_confidence < 60 && (
                            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                              資料不足 {result.data_confidence}%
                            </span>
                          )}
                        </div>
                        {result.cross_validation_note && (
                          <p className="text-xs text-text-muted">{result.cross_validation_note}</p>
                        )}
                      </div>
                      <div>
                        <div className="mb-1 flex items-baseline justify-between gap-3">
                          <span className="text-xs text-text-muted">訊號分數</span>
                          <span className="text-xl font-semibold text-text-primary">
                            {confidenceScore != null ? `${confidenceScore} / 100` : "—"}
                          </span>
                        </div>
                        <div className="h-2 rounded-full bg-border">
                          <div
                            className="h-2 rounded-full bg-accent"
                            style={{ width: `${Math.max(0, Math.min(confidenceScore ?? 0, 100))}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="space-y-4">
                    {/* 段落一：風險狀態 */}
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium text-text-primary flex-1">{riskStateLabel}</p>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {!result.is_final && (
                          <span className="rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800">
                            盤中版
                          </span>
                        )}
                      </div>
                    </div>

                    {/* 段落二：觀察條件 */}
                    {observationContent}

                    {/* 段落三：風險控制參考 */}
                    <div className="rounded-lg bg-card-hover p-3 grid grid-cols-2 gap-2">
                      <p className="text-xs font-semibold text-text-muted col-span-2 mb-0.5">參考區間與風險控制</p>
                      {riskReferenceContent}
                    </div>

                    {/* 段落四：紀律觸發 */}
                    {disciplineContent}

                    {/* 可收合：條件變化 */}
                    <TriggersSection
                      upgradeTriggers={actionPlanUpgradeTriggers}
                      downgradeTriggers={actionPlanDowngradeTriggers}
                    />
                    {legacyActionPlanActionText ? (
                      <details className="text-xs text-text-faint">
                        <summary className="cursor-pointer">相容欄位（secondary）</summary>
                        <p className="mt-1">action_plan.action: {legacyActionPlanActionText}</p>
                      </details>
                    ) : null}
                  </div>
                </div>

                {/* 免責聲明（移至底部） */}
                {result.intraday_disclaimer && (
                  <p className="mt-4 text-xs text-text-muted border-t border-border pt-2">
                    {result.intraday_disclaimer}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-text-faint">尚無可用觀察條件。</p>
            )
          ) : null}
        </section>
      )}

      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/55 sm:items-center sm:p-4">
          <div
            ref={addPortfolioDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={addPortfolioTitleId}
            className="flex max-h-[92dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-[14px] border border-border bg-surface-raised shadow-panel sm:max-h-[calc(100dvh-2rem)] sm:rounded-[14px]"
          >
            <div className="flex items-start justify-between gap-4 border-b border-border-subtle px-5 py-4">
              <div className="min-w-0">
                <h3 id={addPortfolioTitleId} className="text-base font-semibold text-text-primary">
                  加入我的持股
                </h3>
                <p className="mt-1 text-xs text-text-faint">
                  {analyzedDisplayName}，只儲存你在表單中確認的持股與進場紀錄。
                </p>
              </div>
              <button
                type="button"
                ref={addPortfolioCloseButtonRef}
                onClick={() => setShowAddModal(false)}
                className="ui-icon-button shrink-0 border border-border"
                aria-label="關閉加入持股視窗"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-5 w-5"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                  aria-hidden="true"
                >
                  <path
                    fillRule="evenodd"
                    d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            </div>

            <form onSubmit={handleAddPortfolio} className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 overscroll-contain overflow-y-auto px-5 py-4">
                <div className="space-y-5">
                  <section className="space-y-3">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="space-y-1">
                        <span className="text-xs font-medium text-text-muted">股票代碼</span>
                        <input
                          value={symbol}
                          readOnly
                          className="ui-input bg-card-hover font-medium text-text-secondary"
                        />
                      </label>
                      {analyzedSymbolName && (
                        <label className="space-y-1">
                          <span className="text-xs font-medium text-text-muted">股票名稱</span>
                          <input
                            value={analyzedSymbolName}
                            readOnly
                            className="ui-input bg-card-hover font-medium text-text-secondary"
                          />
                        </label>
                      )}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-3">
                      <label className="space-y-1">
                        <span className="text-xs font-medium text-text-muted">成本價 *</span>
                        <input
                          type="number"
                          value={addForm.entry_price}
                          onChange={(e) => setAddForm((f) => ({ ...f, entry_price: e.target.value }))}
                          required
                          min="0.01"
                          step="0.01"
                          placeholder="980"
                          className="ui-input"
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-xs font-medium text-text-muted">持有股數 *</span>
                        <input
                          type="number"
                          value={addForm.quantity}
                          onChange={(e) => setAddForm((f) => ({ ...f, quantity: e.target.value }))}
                          required
                          min="1"
                          placeholder="1000"
                          className="ui-input"
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-xs font-medium text-text-muted">購入日期</span>
                        <input
                          type="date"
                          value={addForm.entry_date}
                          onChange={(e) => setAddForm((f) => ({ ...f, entry_date: e.target.value }))}
                          className="ui-input"
                        />
                      </label>
                    </div>
                  </section>

                  <section className="border-t border-border-subtle pt-4">
                    <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <p className="text-xs font-semibold text-text-primary">進場紀錄</p>
                        <p className="mt-1 text-xs leading-relaxed text-text-faint">
                          選填，用來保存你當下確認過的進場脈絡。
                        </p>
                      </div>
                      <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                        不會自動引用外部分析結論
                      </span>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="space-y-1">
                        <span className="text-xs font-medium text-text-muted">進場理由</span>
                        <select
                          value={addForm.entry_reason}
                          onChange={(e) =>
                            setAddForm((f) => ({
                              ...f,
                              entry_reason: e.target.value as AddPortfolioForm["entry_reason"],
                            }))
                          }
                          className="ui-input"
                        >
                          <option value="">未選擇（不送出）</option>
                          {ENTRY_RECORD_REASON_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="space-y-1">
                        <span className="text-xs font-medium text-text-muted">預計持有期間</span>
                        <select
                          value={addForm.planned_holding_period}
                          onChange={(e) =>
                            setAddForm((f) => ({
                              ...f,
                              planned_holding_period: e.target.value as AddPortfolioForm["planned_holding_period"],
                            }))
                          }
                          className="ui-input"
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
                        <span className="text-xs font-medium text-text-muted">預設防守規則</span>
                        <select
                          value={addForm.default_stop_rule}
                          onChange={(e) =>
                            handleDefaultStopRuleChange(e.target.value as AddPortfolioForm["default_stop_rule"])
                          }
                          className="ui-input"
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
                        <span className="text-xs font-medium text-text-muted">防守價</span>
                        <input
                          type="number"
                          value={addForm.planned_stop_price}
                          onChange={(e) => setAddForm((f) => ({ ...f, planned_stop_price: e.target.value }))}
                          min="0.01"
                          step="0.01"
                          placeholder={
                            addForm.default_stop_rule === "fixed_price"
                              ? "請輸入固定防守價"
                              : autoPlannedStopPrice != null
                                ? formatPriceForInput(autoPlannedStopPrice)
                                : "未選擇則不送出"
                          }
                          className="ui-input"
                        />
                        <span className="block text-xs leading-relaxed text-text-faint">
                          MA20、MA60、20 日低點可從本次分析帶入；固定價格請手動確認。
                        </span>
                      </label>
                      <label className="space-y-1 sm:col-span-2">
                        <span className="text-xs font-medium text-text-muted">新增批次條件</span>
                        <select
                          value={addForm.add_entry_condition}
                          onChange={(e) =>
                            setAddForm((f) => ({
                              ...f,
                              add_entry_condition: e.target.value as AddPortfolioForm["add_entry_condition"],
                            }))
                          }
                          className="ui-input"
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

                    <label className="mt-3 block space-y-1">
                      <span className="text-xs font-medium text-text-muted">備註（選填）</span>
                      <textarea
                        value={addForm.notes}
                        onChange={(e) => setAddForm((f) => ({ ...f, notes: e.target.value }))}
                        rows={3}
                        placeholder="補充你已確認的進場脈絡"
                        className="ui-input resize-none py-2"
                      />
                    </label>
                  </section>
                </div>
              </div>

              <div className="border-t border-border-subtle bg-surface-raised px-5 py-4 [padding-bottom:max(1rem,env(safe-area-inset-bottom))]">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  {addError ? (
                    <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
                      {addError}
                    </p>
                  ) : (
                    <p className="text-xs text-text-faint">必填欄位完成後即可加入持股。</p>
                  )}
                  <div className="flex justify-end gap-2">
                    <button type="button" onClick={() => setShowAddModal(false)} className="ui-button-secondary">
                      取消
                    </button>
                    <button type="submit" disabled={addLoading} className="ui-button-primary">
                      {addLoading ? "新增中..." : "確認新增"}
                    </button>
                  </div>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}

      {(result?.technical_profile || result?.technical_indicators) && (
        <TechnicalIndicatorsPanel
          result={result}
          snapshot={snapshot}
          actions={
            <button
              type="button"
              onClick={() => void handleCopyTechnicalIndicators()}
              className={`inline-flex min-h-10 items-center justify-center rounded-[10px] border px-3 text-xs font-medium transition-[background-color,border-color,color,transform] duration-150 active:scale-[0.96] motion-reduce:transform-none ${
                technicalCopyStatus === "success"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
                  : technicalCopyStatus === "error"
                    ? "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
                    : "border-border bg-card-hover text-text-secondary hover:border-indigo-200 hover:text-indigo-600 dark:hover:border-indigo-700 dark:hover:text-indigo-300"
              }`}
              aria-label="複製技術指標摘要"
            >
              {technicalCopyStatus === "success" ? "已複製" : technicalCopyStatus === "error" ? "複製失敗" : "複製指標"}
            </button>
          }
        />
      )}

    </div>
  );
}
