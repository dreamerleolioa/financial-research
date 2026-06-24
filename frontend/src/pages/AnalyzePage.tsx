import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { analyzeSymbol } from "../lib/analyzeApi";
import type { AnalyzeResponse } from "../lib/analysisTypes";
import { InsightText } from "../components/InsightText";
import { TechnicalIndicatorsPanel } from "../components/TechnicalIndicatorsPanel";
import { createPortfolioItem, fetchPortfolioItems, type CreatePortfolioRequest } from "../lib/portfolioApi";
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

const SIGNAL_LABEL: Record<string, string> = {
  bullish: "看多",
  bearish: "看空",
  sideways: "盤整",
};

const SIGNAL_CLASS: Record<string, string> = {
  bullish: "bg-emerald-100 text-emerald-800",
  bearish: "bg-red-100 text-red-800",
  sideways: "bg-badge-neutral-bg text-badge-neutral-text",
};

const SENTIMENT_LABEL: Record<string, string> = {
  positive: "偏正向",
  neutral: "中性",
  negative: "偏負向",
};

const SENTIMENT_CLASS: Record<string, string> = {
  positive: "bg-emerald-100 text-emerald-800",
  neutral: "bg-badge-neutral-bg text-badge-neutral-text",
  negative: "bg-rose-100 text-rose-800",
};

const PE_BAND_BADGE: Record<string, { label: string; cls: string }> = {
  cheap: { label: "低估", cls: "bg-emerald-100 text-emerald-800" },
  fair: { label: "合理", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
  expensive: { label: "高估", cls: "bg-red-100 text-red-800" },
};

const INST_FLOW_BADGE: Record<string, { label: string; cls: string }> = {
  institutional_accumulation: { label: "法人買超", cls: "bg-emerald-100 text-emerald-800" },
  distribution: { label: "主力出貨", cls: "bg-red-100 text-red-800" },
  retail_chasing: { label: "散戶追高", cls: "bg-orange-100 text-orange-800" },
  neutral: { label: "籌碼中性", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const ACTION_TAG_MAP: Record<string, { emoji: string; label: string; color: string }> = {
  opportunity: { emoji: "🟢", label: "機會", color: "text-green-600" },
  overheated: { emoji: "🔴", label: "過熱", color: "text-red-600" },
  neutral: { emoji: "🔵", label: "中性", color: "text-blue-500" },
};

const CONVICTION_BADGE: Record<string, { label: string; cls: string }> = {
  high: { label: "高信心", cls: "bg-emerald-100 text-emerald-800" },
  medium: { label: "中信心", cls: "bg-yellow-100 text-yellow-800" },
  low: { label: "低信心", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

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
  const [searchParams] = useSearchParams();
  const querySymbol = searchParams.get("symbol") ?? "2330.TW";
  const [symbol, setSymbol] = useState(querySymbol);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [isRawOnly, setIsRawOnly] = useState(false);

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
        setAddError("停損價必須是有效數字。");
        return;
      }
      if (parsedStopPrice != null && parsedStopPrice <= 0) {
        setAddError("停損價必須大於 0。");
        return;
      }
      if (addForm.default_stop_rule === "fixed_price" && parsedStopPrice == null) {
        setAddError("選擇固定價格停損時，請填寫停損價。");
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

      await createPortfolioItem(payload);
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

  async function handleAnalyze(skipAi: boolean = false) {
    if (!symbol.trim()) return;
    setIsRawOnly(skipAi);
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
      const data = await analyzeSymbol({ symbol: symbol.trim(), skip_ai: skipAi }, controller.signal);
      setResult(data);
      await Promise.all([fetchPortfolio(), fetchWatchlist()]);
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return; // 使用者已送出新請求，忽略
      const message = err instanceof Error ? err.message : "無法連線後端，請確認伺服器已啟動。";
      setResult({
        snapshot: {},
        symbol_name: null,
        analysis: "",
        analysis_detail: null,
        cleaned_news: null,
        cleaned_news_quality: null,
        news_display_items: [],
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
  const actionPlanConvictionLevel = actionPlan?.conviction_level;
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

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <label htmlFor="symbol" className="mb-2 block text-sm font-medium text-text-secondary">
          股票代碼
        </label>
        <div className="flex flex-col gap-3 md:flex-row">
          <input
            id="symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !loading && handleAnalyze(true)}
            className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary ring-indigo-200 transition outline-none focus:ring-2 dark:ring-indigo-500 md:max-w-sm"
            placeholder="例如 2330.TW 或 6488.TWO"
            disabled={loading}
          />
          <button
            onClick={() => handleAnalyze(true)}
            disabled={loading}
            className="rounded-lg border border-indigo-600 bg-card px-4 py-2 text-sm font-medium text-indigo-600 transition hover:bg-card-hover disabled:cursor-not-allowed disabled:opacity-60 dark:border-indigo-400 dark:text-indigo-400"
          >
            {loading && isRawOnly ? "讀取中..." : "查詢個股資訊"}
          </button>
          <button
            onClick={() => handleAnalyze(false)}
            disabled={loading}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading && !isRawOnly ? "分析中..." : "開始 AI 分析"}
          </button>
          {result && (
            <>
              <button
                type="button"
                onClick={() => void handleAddWatchlist()}
                disabled={isWatchlisted || watchlistLoading}
                title={isWatchlisted ? "已在關注列表" : "加入關注列表"}
                className="rounded-lg border border-emerald-300 px-4 py-2 text-sm font-medium text-emerald-700 transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-950"
              >
                {watchlistLoading ? "儲存中..." : isWatchlisted ? "已關注" : "加入關注"}
              </button>
              <button
                onClick={() => {
                  setAddError(null);
                  setAddForm(createInitialAddPortfolioForm());
                  setShowAddModal(true);
                }}
                disabled={isTracked}
                title={isTracked ? "已追蹤" : "加入我的持股"}
                className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-600 transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-600 dark:text-indigo-400 dark:hover:bg-indigo-950"
              >
                {isTracked ? "已追蹤" : "加入我的持股"}
              </button>
            </>
          )}
        </div>
        {watchlistMessage && (
          <p
            className={`mt-2 text-xs ${watchlistStatus === "error" ? "text-red-600 dark:text-red-400" : "text-emerald-700 dark:text-emerald-300"}`}
          >
            {watchlistMessage}
          </p>
        )}
        <p className="mt-2 text-xs text-text-muted">上市股票請用 .TW，上櫃股票請用 .TWO。</p>
        <p className="mt-1 text-xs text-text-muted">上市範例：2330.TW（台積電）；上櫃範例：6488.TWO（環球晶）。</p>
      </section>

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <div className="mb-1 flex items-center gap-2">
          <h2 className="text-sm font-semibold text-text-primary">觀察與風險紀律</h2>
          {result?.action_plan_tag && ACTION_TAG_MAP[result.action_plan_tag] && (
            <span className={`text-sm font-medium ${ACTION_TAG_MAP[result.action_plan_tag].color}`}>
              {ACTION_TAG_MAP[result.action_plan_tag].emoji} {ACTION_TAG_MAP[result.action_plan_tag].label}
            </span>
          )}
        </div>
        <p className="mb-4 text-xs text-text-muted">
          用於評估是否納入觀察、等待條件與紀律觸發，不提供持股中的操作指令。
        </p>
        {loading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
            <div
              className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400"
              style={{ animationDuration: "1s" }}
            />
            <p className="text-sm font-medium text-text-primary">{isRawOnly ? "資料讀取中" : "AI 分析中"}</p>
            <p className="text-xs text-text-muted">
              {isRawOnly
                ? "正在取得最新數據並計算指標，約需 1–3 秒"
                : "正在抓取行情、計算指標並呼叫 AI，通常需要 15–30 秒"}
            </p>
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
                        <p className="text-xs font-semibold text-text-muted">信心指數</p>
                        {actionPlanConvictionLevel && CONVICTION_BADGE[actionPlanConvictionLevel] && (
                          <span
                            className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONVICTION_BADGE[actionPlanConvictionLevel].cls}`}
                          >
                            {CONVICTION_BADGE[actionPlanConvictionLevel].label}
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
                        <span className="text-xs text-text-muted">信心分數</span>
                        <span className="text-xl font-semibold text-text-primary">
                          {confidenceScore != null ? `${confidenceScore}%` : "—"}
                        </span>
                      </div>
                      <div className="h-2 rounded-full bg-border">
                        <div
                          className="h-2 rounded-full bg-indigo-600 transition-all"
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
                <p className="mt-4 text-xs text-text-muted border-t border-border pt-2">{result.intraday_disclaimer}</p>
              )}
            </div>
          ) : (
            <p className="text-sm text-text-faint">尚無可用觀察條件。</p>
          )
        ) : (
          <p className="text-sm text-text-faint">請先執行分析。</p>
        )}
      </section>

      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <div className="max-h-screen w-full max-w-lg overflow-y-auto rounded-xl bg-card p-6 shadow-xl">
            <h3 className="mb-4 text-base font-semibold text-text-primary">加入我的持股</h3>
            <form onSubmit={handleAddPortfolio} className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">股票代碼</label>
                <input
                  value={symbol}
                  readOnly
                  className="w-full rounded-lg border border-border bg-card-hover px-3 py-2 text-sm text-text-muted"
                />
              </div>
              {analyzedSymbolName && (
                <div>
                  <label className="mb-1 block text-xs font-medium text-text-muted">股票名稱</label>
                  <input
                    value={analyzedSymbolName}
                    readOnly
                    className="w-full rounded-lg border border-border bg-card-hover px-3 py-2 text-sm text-text-muted"
                  />
                </div>
              )}
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">成本價 *</label>
                <input
                  type="number"
                  value={addForm.entry_price}
                  onChange={(e) => setAddForm((f) => ({ ...f, entry_price: e.target.value }))}
                  required
                  min="0.01"
                  step="0.01"
                  placeholder="980"
                  className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">持有股數 *</label>
                <input
                  type="number"
                  value={addForm.quantity}
                  onChange={(e) => setAddForm((f) => ({ ...f, quantity: e.target.value }))}
                  required
                  min="1"
                  placeholder="1000"
                  className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">購入日期</label>
                <input
                  type="date"
                  value={addForm.entry_date}
                  onChange={(e) => setAddForm((f) => ({ ...f, entry_date: e.target.value }))}
                  className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
                />
              </div>
              <div className="rounded-lg border border-border bg-card-hover/70 p-3">
                <div className="mb-3">
                  <p className="text-xs font-semibold text-text-primary">進場紀錄（選填）</p>
                  <p className="mt-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                    AI 分析輸出不會自動儲存為你的進場意圖；只有在此表單明確確認的欄位才會寫入進場紀錄。
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">進場理由</span>
                    <select
                      value={addForm.entry_reason}
                      onChange={(e) =>
                        setAddForm((f) => ({ ...f, entry_reason: e.target.value as AddPortfolioForm["entry_reason"] }))
                      }
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
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
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
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
                      value={addForm.default_stop_rule}
                      onChange={(e) => handleDefaultStopRuleChange(e.target.value as AddPortfolioForm["default_stop_rule"])}
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
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
                    <span className="text-xs font-medium text-text-muted">停損價</span>
                    <input
                      type="number"
                      value={addForm.planned_stop_price}
                      onChange={(e) => setAddForm((f) => ({ ...f, planned_stop_price: e.target.value }))}
                      min="0.01"
                      step="0.01"
                      placeholder={
                        addForm.default_stop_rule === "fixed_price"
                          ? "請輸入固定停損價"
                          : autoPlannedStopPrice != null
                            ? formatPriceForInput(autoPlannedStopPrice)
                            : "未選擇則不送出"
                      }
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
                    />
                    <span className="block text-xs leading-relaxed text-text-faint">
                      MA20、MA60、20 日低點會從本次分析自動帶入；固定價格請手動確認。
                    </span>
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-text-muted">新增批次條件</span>
                    <select
                      value={addForm.add_entry_condition}
                      onChange={(e) =>
                        setAddForm((f) => ({
                          ...f,
                          add_entry_condition: e.target.value as AddPortfolioForm["add_entry_condition"],
                        }))
                      }
                      className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
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
                <div className="mt-3">
                  <label className="mb-1 block text-xs font-medium text-text-muted">備註（選填，補充脈絡）</label>
                  <textarea
                    value={addForm.notes}
                    onChange={(e) => setAddForm((f) => ({ ...f, notes: e.target.value }))}
                    rows={3}
                    placeholder="僅補充你已確認的進場脈絡，不會自動引用分析結果"
                    className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500"
                  />
                </div>
              </div>
              {addError && <p className="text-sm text-red-600 dark:text-red-400">{addError}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="rounded-lg px-4 py-2 text-sm text-text-muted hover:bg-card-hover"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={addLoading}
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
                >
                  {addLoading ? "新增中..." : "確認新增"}
                </button>
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
              className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
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

      <section className="space-y-4">
        <h2 className="text-sm font-semibold text-text-primary">分析報告</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">技術面</h3>
              {result?.analysis_detail ? (
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${SIGNAL_CLASS[result.analysis_detail.technical_signal] ?? SIGNAL_CLASS.sideways}`}
                >
                  {SIGNAL_LABEL[result.analysis_detail.technical_signal] ?? "盤整"}
                </span>
              ) : (
                <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">
                  —
                </span>
              )}
            </div>
            <InsightText text={result?.analysis_detail?.tech_insight} />
          </article>

          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">籌碼面</h3>
              {result?.institutional_flow_label && INST_FLOW_BADGE[result.institutional_flow_label] ? (
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${INST_FLOW_BADGE[result.institutional_flow_label].cls}`}
                >
                  {INST_FLOW_BADGE[result.institutional_flow_label].label}
                </span>
              ) : (
                <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">
                  —
                </span>
              )}
            </div>
            <InsightText text={result?.analysis_detail?.inst_insight} />
          </article>

          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">基本面</h3>
              {result?.fundamental_data?.pe_band && PE_BAND_BADGE[result.fundamental_data.pe_band] ? (
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${PE_BAND_BADGE[result.fundamental_data.pe_band].cls}`}
                >
                  {PE_BAND_BADGE[result.fundamental_data.pe_band].label}
                </span>
              ) : (
                <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">
                  —
                </span>
              )}
            </div>
            {result?.analysis_detail?.fundamental_insight ? (
              <InsightText text={result.analysis_detail.fundamental_insight} />
            ) : result?.fundamental_data ? (
              <div className="space-y-1 text-sm text-text-muted">
                {result.fundamental_data.pe_current != null && (
                  <p>
                    PE：{result.fundamental_data.pe_current.toFixed(1)} 倍（
                    {result.fundamental_data.pe_band === "cheap"
                      ? "偏低"
                      : result.fundamental_data.pe_band === "expensive"
                        ? "偏高"
                        : "合理"}
                    ）
                  </p>
                )}
                {result.fundamental_data.dividend_yield != null && (
                  <p>殖利率：{result.fundamental_data.dividend_yield.toFixed(2)}%</p>
                )}
                {result.fundamental_data.pe_percentile != null && (
                  <p>PE 百分位：{result.fundamental_data.pe_percentile.toFixed(0)}%</p>
                )}
              </div>
            ) : (
              <p className="text-sm text-text-faint">本次無基本面資料</p>
            )}
          </article>

          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">消息面</h3>
              {result?.analysis_detail?.sentiment_label ? (
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${SENTIMENT_CLASS[result.analysis_detail.sentiment_label] ?? SENTIMENT_CLASS.neutral}`}
                >
                  {SENTIMENT_LABEL[result.analysis_detail.sentiment_label] ?? "中性"}
                </span>
              ) : (
                <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">
                  —
                </span>
              )}
            </div>
            <InsightText text={result?.analysis_detail?.news_insight} />
          </article>
        </div>

        {result?.analysis_detail?.thought_process && (
          <details className="group rounded-xl border border-indigo-100 bg-indigo-50/50 p-4 shadow-sm dark:border-indigo-800 dark:bg-indigo-950/30">
            <summary className="flex cursor-pointer items-center justify-between text-xs font-semibold text-indigo-700 dark:text-indigo-400 select-none">
              <span>Skeptic Mode 審查摘要</span>
              <span className="transition-transform duration-200 group-open:-rotate-180">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </span>
            </summary>
            <div className="mt-3 text-sm leading-relaxed text-text-secondary border-t border-indigo-100/50 pt-3 dark:border-indigo-900/50 italic">
              {result.analysis_detail.thought_process}
            </div>
          </details>
        )}

        {result?.analysis_detail && (
          <article className="rounded-xl border border-indigo-100 bg-indigo-50 p-4 shadow-sm dark:border-indigo-800 dark:bg-indigo-950/60">
            <h3 className="mb-3 text-xs font-semibold text-indigo-700 dark:text-indigo-400">綜合仲裁</h3>
            <InsightText text={result.analysis_detail.final_verdict ?? result.analysis_detail.summary} />
            {result.analysis_detail.risks.length > 0 && (
              <div className="mt-4 border-t border-indigo-100 pt-3 dark:border-indigo-900">
                <p className="mb-1.5 text-xs font-medium text-text-muted">風險提示</p>
                <ul className="space-y-1">
                  {result.analysis_detail.risks.map((risk, i) => (
                    <li key={i} className="flex gap-2 text-sm text-text-secondary">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-rose-400" />
                      {risk}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </article>
        )}

        {result && !result.analysis_detail && result.analysis && (
          <pre className="rounded-xl border border-border bg-card p-4 text-sm leading-relaxed wrap-break-word whitespace-pre-wrap text-text-secondary">
            {result.analysis}
          </pre>
        )}
      </section>

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">近期新聞</h2>
          {result?.cleaned_news && typeof result.cleaned_news.sentiment_label === "string" && (
            <span
              className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${SENTIMENT_CLASS[result.cleaned_news.sentiment_label] ?? SENTIMENT_CLASS.neutral}`}
            >
              {SENTIMENT_LABEL[result.cleaned_news.sentiment_label] ?? "中性"}
            </span>
          )}
        </div>
        {result?.cleaned_news_quality != null &&
          (result.cleaned_news_quality.quality_score < 60 || result.cleaned_news_quality.quality_flags.length > 0) && (
            <p className="mt-2 rounded-md bg-badge-neutral-bg px-3 py-1.5 text-xs text-text-muted">摘要品質受限</p>
          )}
        {result ? (
          result.news_display_items.length > 0 ? (
            <ul className="mt-3 divide-y divide-border-subtle">
              {result.news_display_items.map((item, idx) => (
                <li key={idx} className="py-2.5">
                  {item.source_url ? (
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block text-sm text-text-primary hover:text-indigo-600 hover:underline dark:hover:text-indigo-400"
                    >
                      {item.title}
                    </a>
                  ) : (
                    <p className="text-sm text-text-primary">{item.title}</p>
                  )}
                  {item.date && <p className="mt-0.5 text-xs text-text-faint">{item.date}</p>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-text-faint">本次無新聞資料。</p>
          )
        ) : (
          <p className="mt-3 text-sm text-text-faint">請先執行分析。</p>
        )}
        <p className="mt-3 text-xs text-text-faint">
          以上為市場情緒參考新聞。財報數字請參閱
          <a
            href="https://mops.twse.com.tw"
            target="_blank"
            rel="noopener noreferrer"
            className="ml-0.5 text-indigo-500 hover:underline dark:text-indigo-400"
          >
            公開資訊觀測站
          </a>
          。
        </p>
      </section>
    </div>
  );
}
