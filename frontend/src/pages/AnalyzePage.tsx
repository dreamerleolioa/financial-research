import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { analyzeSymbol } from "../lib/analyzeApi";
import type { AnalyzeResponse } from "../lib/analysisTypes";
import { TechnicalIndicatorsPanel } from "../components/TechnicalIndicatorsPanel";
import { WorkspaceEmptyState } from "../components/app-shell/WorkspaceEmptyState";
import { formatAnalysisError } from "../lib/presentationLabels";
import {
  buildTechnicalIndicatorsCopyText,
  COPY_STATUS_RESET_MS,
  getAnalyzeSymbolName,
  type CopyStatus,
  writeClipboardText,
} from "../lib/technicalIndicators";
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

export default function AnalyzePage() {
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

    // 取消上一個尚未完成的請求
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setResult(null);
    try {
      const data = await analyzeSymbol({ symbol: symbol.trim() }, controller.signal);
      setResult(data);
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return; // 使用者已送出新請求，忽略
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
        errors: [{ code: "NETWORK_ERROR", message: "分析服務連線失敗" }],
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
  const analyzedSymbolName = getAnalyzeSymbolName(result, snapshot);
  const analyzedDisplayName = analyzedSymbolName ? `${analyzedSymbolName} ${analyzedSymbol}` : analyzedSymbol;
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
          {formatAnalysisError(firstError)}
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
              <p className="mt-0.5 text-xs text-text-faint">確定性分析已更新，可複製資料進行外部研究。</p>
            </div>
          </div>
        )}
      </section>

      {!result && !loading ? (
        <WorkspaceEmptyState
          eyebrow="Research ready"
          title="從一個明確標的開始"
          description="取得可回放的技術、籌碼與風險資料，協助研究投資候選標的；分析完成後可複製給外部 AI 深入研究。"
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
