import { useMemo, useRef, useState } from "react";
import { authHeaders } from "../lib/auth";
import { formatPrice, formatVolume } from "../lib/formatters";
import { InsightText } from "../components/InsightText";

interface ErrorDetail {
  code: string;
  message: string;
}

interface AnalysisDetail {
  summary: string;
  risks: string[];
  technical_signal: "bullish" | "bearish" | "sideways";
  institutional_flow: string | null;
  sentiment_label: string | null;
  tech_insight: string | null;
  inst_insight: string | null;
  news_insight: string | null;
  final_verdict: string | null;
  fundamental_insight?: string | null;
}

interface CleanedNewsQuality {
  quality_score: number;
  quality_flags: string[];
}

interface NewsDisplayItem {
  title: string;
  date: string | null;
  source_url: string | null;
}

interface AnalyzeResponse {
  snapshot: Record<string, unknown>;
  analysis: string;
  analysis_detail: AnalysisDetail | null;
  cleaned_news: Record<string, unknown> | null;
  cleaned_news_quality: CleanedNewsQuality | null;
  news_display_items: NewsDisplayItem[];
  confidence_score: number | null;
  cross_validation_note: string | null;
  strategy_type: "short_term" | "mid_term" | "defensive_wait" | null;
  entry_zone: string | null;
  stop_loss: string | null;
  holding_period: string | null;
  action_plan_tag: "opportunity" | "overheated" | "neutral" | null;
  action_plan: {
    action?: string;
    target_zone?: string;
    defense_line?: string;
    breakeven_note?: string;
    momentum_expectation?: string;
    conviction_level?: "low" | "medium" | "high";
    thesis_points?: string[];
    invalidation_conditions?: string[];
    suggested_position_size?: string;
    /** 升級觸發條件 — 已接收但暫未渲染，預留未來 UI 擴充 */
    upgrade_triggers?: string[];
    /** 降級觸發條件 — 已接收但暫未渲染，預留未來 UI 擴充 */
    downgrade_triggers?: string[];
  } | null;
  institutional_flow_label: string | null;
  data_confidence: number | null;
  is_final: boolean;
  intraday_disclaimer: string | null;
  errors: ErrorDetail[];
  fundamental_data?: {
    ttm_eps?: number | null;
    pe_current?: number | null;
    pe_band?: string | null;
    pe_percentile?: number | null;
    dividend_yield?: number | null;
    yield_signal?: string | null;
  } | null;
}

interface AddPortfolioForm {
  entry_price: string;
  quantity: string;
  entry_date: string;
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
                    <span className="text-emerald-500 shrink-0">↑</span>{t}
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
                    <span className="text-amber-500 shrink-0">↓</span>{t}
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

function mapVolumeSource(value: unknown): string {
  if (value === "realtime") return "即時成交量";
  if (value === "history_fallback") return "歷史資料回填";
  if (value === "unavailable") return "暫無資料";
  return "未知來源";
}

export default function AnalyzePage() {
  const [symbol, setSymbol] = useState("2330.TW");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  const [portfolioSymbols, setPortfolioSymbols] = useState<Set<string>>(new Set());
  const [portfolioCount, setPortfolioCount] = useState(0);
  const [showAddModal, setShowAddModal] = useState(false);
  const [addForm, setAddForm] = useState<AddPortfolioForm>({
    entry_price: "",
    quantity: "",
    entry_date: new Date().toISOString().slice(0, 10),
    notes: "",
  });
  const [addLoading, setAddLoading] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  async function fetchPortfolio() {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio`, { headers: authHeaders() });
      if (!res.ok) return;
      const data: { id: number; symbol: string }[] = await res.json();
      setPortfolioSymbols(new Set(data.map((r) => r.symbol)));
      setPortfolioCount(data.length);
    } catch { /* ignore */ }
  }

  async function handleAddPortfolio(e: React.FormEvent) {
    e.preventDefault();
    setAddLoading(true);
    setAddError(null);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          symbol,
          entry_price: parseFloat(addForm.entry_price),
          quantity: addForm.quantity ? parseInt(addForm.quantity) : 0,
          entry_date: addForm.entry_date,
          notes: addForm.notes || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }
      await fetchPortfolio();
      setShowAddModal(false);
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "新增失敗");
    } finally {
      setAddLoading(false);
    }
  }

  async function handleAnalyze() {
    if (!symbol.trim()) return;

    // 取消上一個尚未完成的請求
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/analyze`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ symbol: symbol.trim() }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }
      const data: AnalyzeResponse = await res.json();
      setResult(data);
      await fetchPortfolio();
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return; // 使用者已送出新請求，忽略
      const message = err instanceof Error ? err.message : "無法連線後端，請確認伺服器已啟動。";
      setResult({
        snapshot: {}, analysis: "", analysis_detail: null, cleaned_news: null,
        cleaned_news_quality: null, news_display_items: [], confidence_score: null,
        cross_validation_note: null, strategy_type: null, entry_zone: null,
        stop_loss: null, holding_period: null, action_plan_tag: null, action_plan: null,
        institutional_flow_label: null, data_confidence: null,
        is_final: true, intraday_disclaimer: null,
        errors: [{ code: "NETWORK_ERROR", message }],
      });
    } finally {
      setLoading(false);
    }
  }

  const isTracked = portfolioSymbols.has(symbol);
  const portfolioFull = portfolioCount >= 5;
  const confidenceScore = result?.confidence_score ?? null;
  const circumference = 2 * Math.PI * 52;
  const dashOffset = useMemo(
    () => (confidenceScore != null ? circumference * (1 - confidenceScore / 100) : circumference),
    [circumference, confidenceScore],
  );
  const firstError = result?.errors?.[0];
  const snapshot = result?.snapshot ?? {};

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
            onKeyDown={(e) => e.key === "Enter" && !loading && handleAnalyze()}
            className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary ring-indigo-200 transition outline-none focus:ring-2 dark:ring-indigo-500 md:max-w-sm"
            placeholder="例如 2330.TW 或 6488.TWO"
            disabled={loading}
          />
          <button
            onClick={handleAnalyze}
            disabled={loading}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "分析中..." : "開始分析"}
          </button>
          {result && (
            <button
              onClick={() => { setAddError(null); setShowAddModal(true); }}
              disabled={isTracked || portfolioFull}
              title={isTracked ? "已追蹤" : portfolioFull ? "最多追蹤 5 筆持股" : "加入我的持股"}
              className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-600 transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-600 dark:text-indigo-400 dark:hover:bg-indigo-950"
            >
              {isTracked ? "已追蹤" : "加入我的持股"}
            </button>
          )}
        </div>
        <p className="mt-2 text-xs text-text-muted">上市股票請用 .TW，上櫃股票請用 .TWO。</p>
        <p className="mt-1 text-xs text-text-muted">上市範例：2330.TW（台積電）；上櫃範例：6488.TWO（環球晶）。</p>
      </section>

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <div className="mb-1 flex items-center gap-2">
          <h2 className="text-sm font-semibold text-text-primary">新倉策略建議</h2>
          {result?.action_plan_tag && ACTION_TAG_MAP[result.action_plan_tag] && (
            <span className={`text-sm font-medium ${ACTION_TAG_MAP[result.action_plan_tag].color}`}>
              {ACTION_TAG_MAP[result.action_plan_tag].emoji} {ACTION_TAG_MAP[result.action_plan_tag].label}
            </span>
          )}
        </div>
        <p className="mb-4 text-xs text-text-muted">用於評估是否觀察、等待與分批建立新倉，不提供持股中的續抱／減碼／出場指令。</p>
        {result ? (
          result.action_plan ? (
            <div className="rounded-xl border border-border bg-card p-4 space-y-4">

              {/* 段落一：建議動作 */}
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-text-primary flex-1">
                  {result.action_plan.action}
                </p>
                <div className="flex items-center gap-1.5 shrink-0">
                  {!result.is_final && (
                    <span className="rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800">
                      盤中版
                    </span>
                  )}
                  {result.action_plan.conviction_level && CONVICTION_BADGE[result.action_plan.conviction_level] && (
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONVICTION_BADGE[result.action_plan.conviction_level].cls}`}>
                      {CONVICTION_BADGE[result.action_plan.conviction_level].label}
                    </span>
                  )}
                </div>
              </div>

              {/* 段落二：主要理由 */}
              {result.action_plan.thesis_points && result.action_plan.thesis_points.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-text-muted mb-1.5">主要理由</p>
                  <ul className="space-y-1">
                    {result.action_plan.thesis_points.map((point, i) => (
                      <li key={i} className="flex gap-1.5 text-sm text-text-primary">
                        <span className="text-text-muted shrink-0">·</span>
                        {point}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* 段落三：關鍵價位 */}
              <div className="rounded-lg bg-card-hover p-3 grid grid-cols-2 gap-2">
                <p className="text-xs font-semibold text-text-muted col-span-2 mb-0.5">關鍵價位</p>
                {result.action_plan.target_zone && (
                  <div>
                    <p className="text-xs text-text-muted">進場區間</p>
                    <p className="text-sm font-medium text-text-primary">{result.action_plan.target_zone}</p>
                  </div>
                )}
                {result.action_plan.defense_line && (
                  <div>
                    <p className="text-xs text-text-muted">停損位</p>
                    <p className="text-sm font-medium text-text-primary">{result.action_plan.defense_line}</p>
                  </div>
                )}
                {result.action_plan.momentum_expectation && (
                  <div className="col-span-2">
                    <p className="text-xs text-text-muted">動能預期</p>
                    <p className="text-sm text-text-primary">{result.action_plan.momentum_expectation}</p>
                  </div>
                )}
                {result.action_plan.suggested_position_size && (
                  <div className="col-span-2">
                    <p className="text-xs text-text-muted">建議部位規模</p>
                    <p className="text-sm text-text-primary">{result.action_plan.suggested_position_size}</p>
                  </div>
                )}
              </div>

              {/* 段落四：失效條件 */}
              {result.action_plan.invalidation_conditions && result.action_plan.invalidation_conditions.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-text-muted mb-1.5">失效條件</p>
                  <ul className="space-y-1">
                    {result.action_plan.invalidation_conditions.map((cond, i) => (
                      <li key={i} className="flex gap-1.5 text-sm text-text-primary">
                        <span className="text-rose-400 shrink-0">⚠</span>
                        {cond}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* 可收合：條件變化 */}
              <TriggersSection
                upgradeTriggers={result.action_plan.upgrade_triggers}
                downgradeTriggers={result.action_plan.downgrade_triggers}
              />

              {/* 免責聲明（移至底部） */}
              {result.intraday_disclaimer && (
                <p className="text-xs text-text-muted border-t border-border pt-2">
                  {result.intraday_disclaimer}
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm text-text-faint">無策略建議。</p>
          )
        ) : (
          <p className="text-sm text-text-faint">請先執行分析。</p>
        )}
      </section>

      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <div className="w-full max-w-md rounded-xl bg-card p-6 shadow-xl">
            <h3 className="mb-4 text-base font-semibold text-text-primary">加入我的持股</h3>
            <form onSubmit={handleAddPortfolio} className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">股票代碼</label>
                <input value={symbol} readOnly className="w-full rounded-lg border border-border bg-card-hover px-3 py-2 text-sm text-text-muted" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">成本價 *</label>
                <input type="number" value={addForm.entry_price} onChange={(e) => setAddForm((f) => ({ ...f, entry_price: e.target.value }))} required min="0.01" step="0.01" placeholder="980" className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">持有股數 *</label>
                <input type="number" value={addForm.quantity} onChange={(e) => setAddForm((f) => ({ ...f, quantity: e.target.value }))} required min="1" placeholder="1000" className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">購入日期</label>
                <input type="date" value={addForm.entry_date} onChange={(e) => setAddForm((f) => ({ ...f, entry_date: e.target.value }))} className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-text-muted">備註（選填）</label>
                <input value={addForm.notes} onChange={(e) => setAddForm((f) => ({ ...f, notes: e.target.value }))} placeholder="自訂備註" className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 dark:ring-indigo-500" />
              </div>
              {addError && <p className="text-sm text-red-600 dark:text-red-400">{addError}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={() => setShowAddModal(false)} className="rounded-lg px-4 py-2 text-sm text-text-muted hover:bg-card-hover">取消</button>
                <button type="submit" disabled={addLoading} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60">{addLoading ? "新增中..." : "確認新增"}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <section className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        <article className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
          <h2 className="mb-3 text-sm font-semibold text-text-primary">信心指數</h2>
          <div className="flex flex-col items-center">
            <div className="relative flex items-center justify-center">
              <svg width="120" height="120" viewBox="0 0 140 140" className="-rotate-90">
                <circle cx="70" cy="70" r="52" strokeWidth="12" className="fill-none stroke-border" />
                <circle cx="70" cy="70" r="52" strokeWidth="12" strokeLinecap="round" className="fill-none stroke-indigo-600" strokeDasharray={circumference} strokeDashoffset={dashOffset} style={{ transition: "stroke-dashoffset 0.5s ease" }} />
              </svg>
              <div className="absolute text-center">
                <div className="text-xl font-semibold">{confidenceScore != null ? `${confidenceScore}%` : "—"}</div>
                <div className="text-xs text-text-muted">Confidence</div>
              </div>
            </div>
            {result?.cross_validation_note && <p className="mt-2 text-center text-xs text-text-muted">{result.cross_validation_note}</p>}
            {result?.data_confidence != null && result.data_confidence < 60 && (
              <p className="mt-1 text-center text-xs text-text-faint">⚠️ 資料不足（{result.data_confidence}%）</p>
            )}
          </div>
        </article>

        <article className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
          <h2 className="mb-3 text-sm font-semibold text-text-primary">快照資訊</h2>
          {result ? (
            <dl className="space-y-2 text-sm text-text-secondary">
              <div className="flex justify-between"><dt className="text-text-muted">代碼</dt><dd className="font-medium">{String(snapshot.symbol ?? "—")}</dd></div>
              <div className="flex justify-between"><dt className="text-text-muted">現價</dt><dd className="font-medium">{formatPrice(snapshot.current_price as number | null | undefined, snapshot.symbol as string | undefined)}</dd></div>
              <div className="flex justify-between"><dt className="text-text-muted">成交量</dt><dd className="font-medium">{formatVolume(snapshot.volume)}</dd></div>
              <div className="flex justify-between"><dt className="text-text-muted">成交量來源</dt><dd className="font-medium">{mapVolumeSource(snapshot.volume_source)}</dd></div>
            </dl>
          ) : (
            <p className="text-sm text-text-faint">請先執行分析。</p>
          )}
        </article>
      </section>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold text-text-primary">分析報告</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">技術面</h3>
              {result?.analysis_detail ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${SIGNAL_CLASS[result.analysis_detail.technical_signal] ?? SIGNAL_CLASS.sideways}`}>{SIGNAL_LABEL[result.analysis_detail.technical_signal] ?? "盤整"}</span>
              ) : <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">—</span>}
            </div>
            <InsightText text={result?.analysis_detail?.tech_insight} />
          </article>

          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">籌碼面</h3>
              {result?.institutional_flow_label && INST_FLOW_BADGE[result.institutional_flow_label] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${INST_FLOW_BADGE[result.institutional_flow_label].cls}`}>{INST_FLOW_BADGE[result.institutional_flow_label].label}</span>
              ) : <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">—</span>}
            </div>
            <InsightText text={result?.analysis_detail?.inst_insight} />
          </article>

          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">基本面</h3>
              {result?.fundamental_data?.pe_band && PE_BAND_BADGE[result.fundamental_data.pe_band] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${PE_BAND_BADGE[result.fundamental_data.pe_band].cls}`}>{PE_BAND_BADGE[result.fundamental_data.pe_band].label}</span>
              ) : <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">—</span>}
            </div>
            {result?.analysis_detail?.fundamental_insight ? (
              <InsightText text={result.analysis_detail.fundamental_insight} />
            ) : result?.fundamental_data ? (
              <div className="space-y-1 text-sm text-text-muted">
                {result.fundamental_data.pe_current != null && <p>PE：{result.fundamental_data.pe_current.toFixed(1)} 倍（{result.fundamental_data.pe_band === "cheap" ? "偏低" : result.fundamental_data.pe_band === "expensive" ? "偏高" : "合理"}）</p>}
                {result.fundamental_data.dividend_yield != null && <p>殖利率：{result.fundamental_data.dividend_yield.toFixed(2)}%</p>}
                {result.fundamental_data.pe_percentile != null && <p>PE 百分位：{result.fundamental_data.pe_percentile.toFixed(0)}%</p>}
              </div>
            ) : (
              <p className="text-sm text-text-faint">本次無基本面資料</p>
            )}
          </article>

          <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-text-muted">消息面</h3>
              {result?.analysis_detail?.sentiment_label ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${SENTIMENT_CLASS[result.analysis_detail.sentiment_label] ?? SENTIMENT_CLASS.neutral}`}>{SENTIMENT_LABEL[result.analysis_detail.sentiment_label] ?? "中性"}</span>
              ) : <span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">—</span>}
            </div>
            <InsightText text={result?.analysis_detail?.news_insight} />
          </article>
        </div>

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
          <pre className="rounded-xl border border-border bg-card p-4 text-sm leading-relaxed wrap-break-word whitespace-pre-wrap text-text-secondary">{result.analysis}</pre>
        )}
      </section>

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">近期新聞</h2>
          {result?.cleaned_news && typeof result.cleaned_news.sentiment_label === "string" && (
            <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${SENTIMENT_CLASS[result.cleaned_news.sentiment_label] ?? SENTIMENT_CLASS.neutral}`}>
              {SENTIMENT_LABEL[result.cleaned_news.sentiment_label] ?? "中性"}
            </span>
          )}
        </div>
        {result?.cleaned_news_quality != null && (result.cleaned_news_quality.quality_score < 60 || result.cleaned_news_quality.quality_flags.length > 0) && (
          <p className="mt-2 rounded-md bg-badge-neutral-bg px-3 py-1.5 text-xs text-text-muted">摘要品質受限</p>
        )}
        {result ? (
          result.news_display_items.length > 0 ? (
            <ul className="mt-3 divide-y divide-border-subtle">
              {result.news_display_items.map((item, idx) => (
                <li key={idx} className="py-2.5">
                  {item.source_url ? (
                    <a href={item.source_url} target="_blank" rel="noopener noreferrer" className="block text-sm text-text-primary hover:text-indigo-600 hover:underline dark:hover:text-indigo-400">{item.title}</a>
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
          <a href="https://mops.twse.com.tw" target="_blank" rel="noopener noreferrer" className="ml-0.5 text-indigo-500 hover:underline dark:text-indigo-400">公開資訊觀測站</a>。
        </p>
      </section>

    </div>
  );
}
