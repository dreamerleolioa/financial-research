import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
  thought_process?: string | null;
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

interface TechnicalIndicators {
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  high_20d: number | null;
  low_20d: number | null;
  high_60d: number | null;
  low_60d: number | null;
  bollinger_upper: number | null;
  bollinger_mid: number | null;
  bollinger_lower: number | null;
  bollinger_bandwidth: number | null;
  bollinger_position: string | null;
  macd_line: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  macd_bias: string | null;
  kd_k: number | null;
  kd_d: number | null;
  kd_signal: string | null;
  kd_zone: string | null;
  adx: number | null;
  adx_trend_strength: string | null;
  adx_trend_direction: string | null;
  obv: number | null;
  obv_signal: string | null;
  obv_trend_20d: string | null;
  obv_trend_mid_long: string | null;
  obv_trend_mid_long_window: string | null;
  atr: number | null;
  atr_pct: number | null;
  volatility_level: string | null;
  mfi: number | null;
  mfi_signal: string | null;
  donchian_upper: number | null;
  donchian_lower: number | null;
  donchian_mid: number | null;
  donchian_width_pct: number | null;
  donchian_position: string | null;
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
  technical_indicators?: TechnicalIndicators | null;
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

const MAX_PORTFOLIO_COUNT = 8;

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

const BOLLINGER_POSITION_LABEL: Record<string, { label: string; cls: string }> = {
  near_upper: { label: "接近上軌", cls: "bg-red-100 text-red-800" },
  above_mid: { label: "中軌上方", cls: "bg-emerald-100 text-emerald-800" },
  below_mid: { label: "中軌下方", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
  near_lower: { label: "接近下軌", cls: "bg-blue-100 text-blue-800" },
  flat: { label: "通道平坦", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const MACD_BIAS_LABEL: Record<string, { label: string; cls: string }> = {
  bullish: { label: "多方動能", cls: "bg-emerald-100 text-emerald-800" },
  bearish: { label: "空方動能", cls: "bg-red-100 text-red-800" },
  neutral: { label: "中性", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const KD_SIGNAL_LABEL: Record<string, { label: string; cls: string }> = {
  bullish_cross: { label: "黃金交叉", cls: "bg-emerald-100 text-emerald-800" },
  bearish_cross: { label: "死亡交叉", cls: "bg-red-100 text-red-800" },
  neutral: { label: "中性", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const KD_ZONE_LABEL: Record<string, { label: string; cls: string }> = {
  oversold: { label: "低檔區", cls: "bg-blue-100 text-blue-800" },
  overbought: { label: "高檔區", cls: "bg-orange-100 text-orange-800" },
  neutral: { label: "中性區", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const ADX_STRENGTH_LABEL: Record<string, { label: string; cls: string }> = {
  strong: { label: "趨勢明確", cls: "bg-emerald-100 text-emerald-800" },
  neutral: { label: "趨勢中等", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
  weak: { label: "趨勢偏弱", cls: "bg-yellow-100 text-yellow-800" },
};

const ADX_DIRECTION_LABEL: Record<string, { label: string; cls: string }> = {
  bullish: { label: "多方趨勢", cls: "bg-emerald-100 text-emerald-800" },
  bearish: { label: "空方趨勢", cls: "bg-red-100 text-red-800" },
  neutral: { label: "中性", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const OBV_SIGNAL_LABEL: Record<string, { label: string; cls: string }> = {
  price_volume_confirm: { label: "量價確認", cls: "bg-emerald-100 text-emerald-800" },
  bearish_divergence: { label: "量價背離", cls: "bg-red-100 text-red-800" },
  bullish_divergence: { label: "低檔承接", cls: "bg-blue-100 text-blue-800" },
  price_volume_weak: { label: "量價轉弱", cls: "bg-red-100 text-red-800" },
  neutral: { label: "中性", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const OBV_TREND_LABEL: Record<string, { label: string; cls: string }> = {
  rising: { label: "上升", cls: "bg-emerald-100 text-emerald-800" },
  falling: { label: "下降", cls: "bg-red-100 text-red-800" },
  flat: { label: "盤整", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const VOLATILITY_LEVEL_LABEL: Record<string, { label: string; cls: string }> = {
  high: { label: "高波動", cls: "bg-red-100 text-red-800" },
  medium: { label: "中波動", cls: "bg-yellow-100 text-yellow-800" },
  low: { label: "低波動", cls: "bg-emerald-100 text-emerald-800" },
  unknown: { label: "未知", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const MFI_SIGNAL_LABEL: Record<string, { label: string; cls: string }> = {
  overbought: { label: "資金過熱", cls: "bg-orange-100 text-orange-800" },
  oversold: { label: "資金低檔", cls: "bg-blue-100 text-blue-800" },
  bullish_flow: { label: "資金偏多", cls: "bg-emerald-100 text-emerald-800" },
  bearish_flow: { label: "資金偏弱", cls: "bg-red-100 text-red-800" },
  neutral: { label: "中性", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

const DONCHIAN_POSITION_LABEL: Record<string, { label: string; cls: string }> = {
  breakout_up: { label: "突破上緣", cls: "bg-emerald-100 text-emerald-800" },
  breakdown_down: { label: "跌破下緣", cls: "bg-red-100 text-red-800" },
  near_upper: { label: "接近上緣", cls: "bg-yellow-100 text-yellow-800" },
  near_lower: { label: "接近下緣", cls: "bg-blue-100 text-blue-800" },
  upper_half: { label: "區間上半", cls: "bg-emerald-100 text-emerald-800" },
  lower_half: { label: "區間下半", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
  flat: { label: "區間平坦", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};

function formatIndicatorNumber(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
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

  async function handleAnalyze(skipAi: boolean = false) {
    if (!symbol.trim()) return;
    setIsRawOnly(skipAi);

    // 取消上一個尚未完成的請求
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/analyze`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ symbol: symbol.trim(), skip_ai: skipAi }),
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
  const portfolioFull = portfolioCount >= MAX_PORTFOLIO_COUNT;
  const confidenceScore = result?.confidence_score ?? null;
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
            <button
              onClick={() => { setAddError(null); setShowAddModal(true); }}
              disabled={isTracked || portfolioFull}
              title={isTracked ? "已追蹤" : portfolioFull ? `最多追蹤 ${MAX_PORTFOLIO_COUNT} 筆持股` : "加入我的持股"}
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
        {loading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
            <div className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" style={{ animationDuration: "1s" }} />
            <p className="text-sm font-medium text-text-primary">{isRawOnly ? "資料讀取中" : "AI 分析中"}</p>
            <p className="text-xs text-text-muted">
              {isRawOnly
                ? "正在取得最新數據並計算指標，約需 1–3 秒"
                : "正在抓取行情、計算指標並呼叫 AI，通常需要 15–30 秒"}
            </p>
          </div>
        ) : result ? (
          result.action_plan ? (
            <div className="rounded-xl border border-border bg-card p-4">
              <div className="space-y-4">
                <div className="rounded-lg border border-border bg-card-hover/70 p-3">
                  <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px] md:items-center">
                    <div>
                      <div className="mb-1.5 flex flex-wrap items-center gap-2">
                        <p className="text-xs font-semibold text-text-muted">信心指數</p>
                        {result.action_plan.conviction_level && CONVICTION_BADGE[result.action_plan.conviction_level] && (
                          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONVICTION_BADGE[result.action_plan.conviction_level].cls}`}>
                            {CONVICTION_BADGE[result.action_plan.conviction_level].label}
                          </span>
                        )}
                        {result.data_confidence != null && result.data_confidence < 60 && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                            資料不足 {result.data_confidence}%
                          </span>
                        )}
                      </div>
                      {result.cross_validation_note && <p className="text-xs text-text-muted">{result.cross_validation_note}</p>}
                    </div>
                    <div>
                      <div className="mb-1 flex items-baseline justify-between gap-3">
                        <span className="text-xs text-text-muted">信心分數</span>
                        <span className="text-xl font-semibold text-text-primary">{confidenceScore != null ? `${confidenceScore}%` : "—"}</span>
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

      {result?.technical_indicators && (
        <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
          <h3 className="mb-3 text-xs font-semibold text-text-muted">技術指標摘要</h3>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-4">
            <div>
              <p className="text-xs text-text-muted mb-1">現價</p>
              <p className="text-sm font-medium text-text-primary">{formatPrice(snapshot.current_price as number | null | undefined, snapshot.symbol as string | undefined)}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">成交量</p>
              <p className="text-sm font-medium text-text-primary">{formatVolume(snapshot.volume)}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">均線 MA5／20／60</p>
              <p className="text-sm font-medium text-text-primary">
                {result.technical_indicators.ma5 != null || result.technical_indicators.ma20 != null || result.technical_indicators.ma60 != null
                  ? `${formatPrice(result.technical_indicators.ma5, snapshot.symbol as string | undefined)} / ${formatPrice(result.technical_indicators.ma20, snapshot.symbol as string | undefined)} / ${formatPrice(result.technical_indicators.ma60, snapshot.symbol as string | undefined)}`
                  : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">20 日最高／最低</p>
              <p className="text-sm font-medium text-text-primary">
                {result.technical_indicators.high_20d != null || result.technical_indicators.low_20d != null
                  ? `${formatPrice(result.technical_indicators.high_20d, snapshot.symbol as string | undefined)} / ${formatPrice(result.technical_indicators.low_20d, snapshot.symbol as string | undefined)}`
                  : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">60 日最高／最低</p>
              <p className="text-sm font-medium text-text-primary">
                {result.technical_indicators.high_60d != null || result.technical_indicators.low_60d != null
                  ? `${formatPrice(result.technical_indicators.high_60d, snapshot.symbol as string | undefined)} / ${formatPrice(result.technical_indicators.low_60d, snapshot.symbol as string | undefined)}`
                  : "資料不足"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">布林通道位階</p>
              {result.technical_indicators.bollinger_position && BOLLINGER_POSITION_LABEL[result.technical_indicators.bollinger_position] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${BOLLINGER_POSITION_LABEL[result.technical_indicators.bollinger_position].cls}`}>
                  {BOLLINGER_POSITION_LABEL[result.technical_indicators.bollinger_position].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">指數平滑異同移動平均方向（MACD）</p>
              {result.technical_indicators.macd_bias && MACD_BIAS_LABEL[result.technical_indicators.macd_bias] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${MACD_BIAS_LABEL[result.technical_indicators.macd_bias].cls}`}>
                  {MACD_BIAS_LABEL[result.technical_indicators.macd_bias].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">隨機指標交叉（KD）</p>
              {result.technical_indicators.kd_signal && KD_SIGNAL_LABEL[result.technical_indicators.kd_signal] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${KD_SIGNAL_LABEL[result.technical_indicators.kd_signal].cls}`}>
                  {KD_SIGNAL_LABEL[result.technical_indicators.kd_signal].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">隨機指標區間（KD）</p>
              {result.technical_indicators.kd_zone && KD_ZONE_LABEL[result.technical_indicators.kd_zone] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${KD_ZONE_LABEL[result.technical_indicators.kd_zone].cls}`}>
                  {KD_ZONE_LABEL[result.technical_indicators.kd_zone].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">平均趨向指標強度（ADX）</p>
              {result.technical_indicators.adx_trend_strength && ADX_STRENGTH_LABEL[result.technical_indicators.adx_trend_strength] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${ADX_STRENGTH_LABEL[result.technical_indicators.adx_trend_strength].cls}`}>
                  {ADX_STRENGTH_LABEL[result.technical_indicators.adx_trend_strength].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">平均趨向指標方向（ADX）</p>
              {result.technical_indicators.adx_trend_direction && ADX_DIRECTION_LABEL[result.technical_indicators.adx_trend_direction] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${ADX_DIRECTION_LABEL[result.technical_indicators.adx_trend_direction].cls}`}>
                  {ADX_DIRECTION_LABEL[result.technical_indicators.adx_trend_direction].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">能量潮訊號（OBV）</p>
              {result.technical_indicators.obv_signal && OBV_SIGNAL_LABEL[result.technical_indicators.obv_signal] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${OBV_SIGNAL_LABEL[result.technical_indicators.obv_signal].cls}`}>
                  {OBV_SIGNAL_LABEL[result.technical_indicators.obv_signal].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">OBV 20 日趨勢</p>
              {result.technical_indicators.obv_trend_20d && OBV_TREND_LABEL[result.technical_indicators.obv_trend_20d] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${OBV_TREND_LABEL[result.technical_indicators.obv_trend_20d].cls}`}>
                  {OBV_TREND_LABEL[result.technical_indicators.obv_trend_20d].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">OBV 中長期趨勢</p>
              {result.technical_indicators.obv_trend_mid_long && OBV_TREND_LABEL[result.technical_indicators.obv_trend_mid_long] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${OBV_TREND_LABEL[result.technical_indicators.obv_trend_mid_long].cls}`}>
                  {OBV_TREND_LABEL[result.technical_indicators.obv_trend_mid_long].label}
                  {result.technical_indicators.obv_trend_mid_long_window ? `（${result.technical_indicators.obv_trend_mid_long_window}）` : ""}
                </span>
              ) : <span className="text-sm text-text-faint">資料不足</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">平均真實波幅（ATR）</p>
              {result.technical_indicators.volatility_level && VOLATILITY_LEVEL_LABEL[result.technical_indicators.volatility_level] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${VOLATILITY_LEVEL_LABEL[result.technical_indicators.volatility_level].cls}`}>
                  {VOLATILITY_LEVEL_LABEL[result.technical_indicators.volatility_level].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">資金流量訊號（MFI）</p>
              {result.technical_indicators.mfi_signal && MFI_SIGNAL_LABEL[result.technical_indicators.mfi_signal] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${MFI_SIGNAL_LABEL[result.technical_indicators.mfi_signal].cls}`}>
                  {MFI_SIGNAL_LABEL[result.technical_indicators.mfi_signal].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">唐奇安通道位階</p>
              {result.technical_indicators.donchian_position && DONCHIAN_POSITION_LABEL[result.technical_indicators.donchian_position] ? (
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${DONCHIAN_POSITION_LABEL[result.technical_indicators.donchian_position].cls}`}>
                  {DONCHIAN_POSITION_LABEL[result.technical_indicators.donchian_position].label}
                </span>
              ) : <span className="text-sm text-text-faint">—</span>}
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">布林上軌</p>
              <p className="text-sm font-medium text-text-primary">{result.technical_indicators.bollinger_upper != null ? result.technical_indicators.bollinger_upper.toFixed(2) : "—"}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">布林中軌</p>
              <p className="text-sm font-medium text-text-primary">{result.technical_indicators.bollinger_mid != null ? result.technical_indicators.bollinger_mid.toFixed(2) : "—"}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">布林下軌</p>
              <p className="text-sm font-medium text-text-primary">{result.technical_indicators.bollinger_lower != null ? result.technical_indicators.bollinger_lower.toFixed(2) : "—"}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">MACD 線</p>
              <p className={`text-sm font-medium ${result.technical_indicators.macd_line != null ? (result.technical_indicators.macd_line >= 0 ? "text-emerald-600" : "text-red-600") : "text-text-primary"}`}>
                {result.technical_indicators.macd_line != null ? result.technical_indicators.macd_line.toFixed(3) : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">MACD 訊號線（趨勢判斷基準）</p>
              <p className="text-sm font-medium text-text-primary">{result.technical_indicators.macd_signal != null ? result.technical_indicators.macd_signal.toFixed(3) : "—"}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">MACD 動能柱狀體（正負差值）</p>
              <p className={`text-sm font-medium ${result.technical_indicators.macd_hist != null ? (result.technical_indicators.macd_hist >= 0 ? "text-emerald-600" : "text-red-600") : "text-text-primary"}`}>
                {result.technical_indicators.macd_hist != null ? result.technical_indicators.macd_hist.toFixed(3) : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">隨機指標 K／D（KD）</p>
              <p className="text-sm font-medium text-text-primary">
                {result.technical_indicators.kd_k != null || result.technical_indicators.kd_d != null
                  ? `${formatIndicatorNumber(result.technical_indicators.kd_k, 1)} / ${formatIndicatorNumber(result.technical_indicators.kd_d, 1)}`
                  : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">ADX</p>
              <p className="text-sm font-medium text-text-primary">{formatIndicatorNumber(result.technical_indicators.adx, 1)}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">OBV 累積值參考</p>
              <p className="text-sm font-medium text-text-secondary">
                {formatVolume(result.technical_indicators.obv)}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">平均真實波幅／百分比（ATR）</p>
              <p className="text-sm font-medium text-text-primary">
                {result.technical_indicators.atr != null || result.technical_indicators.atr_pct != null
                  ? `${formatIndicatorNumber(result.technical_indicators.atr, 2)} / ${formatIndicatorNumber(result.technical_indicators.atr_pct, 2)}%`
                  : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">資金流量指標（MFI）</p>
              <p className="text-sm font-medium text-text-primary">{formatIndicatorNumber(result.technical_indicators.mfi, 1)}</p>
            </div>
            <div>
              <p className="text-xs text-text-muted mb-1">唐奇安通道上／下緣</p>
              <p className="text-sm font-medium text-text-primary">
                {result.technical_indicators.donchian_upper != null || result.technical_indicators.donchian_lower != null
                  ? `${formatIndicatorNumber(result.technical_indicators.donchian_upper, 2)} / ${formatIndicatorNumber(result.technical_indicators.donchian_lower, 2)}`
                  : "—"}
              </p>
            </div>
          </div>
        </article>
      )}


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
