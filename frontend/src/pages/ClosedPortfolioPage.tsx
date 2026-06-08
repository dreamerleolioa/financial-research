import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { authHeaders } from "../lib/auth";
import { formatPrice } from "../lib/formatters";
import type {
  ClosedPortfolioItem,
  TradeReviewDataQuality,
  TradeReviewHoldingSection,
  TradeReviewResponse,
  TradeReviewResultMetrics,
  TradeReviewSection,
} from "../lib/portfolioTypes";

type PeriodKey = "1d" | "1w" | "1m" | "1q" | "1y";
type CopyStatus = "idle" | "success" | "error";

interface PeriodOption {
  key: PeriodKey;
  label: string;
  days: number;
}

interface ClosedPortfolioGroup {
  position_group_id: string;
  symbol: string;
  entry_date: string;
  entry_price: number;
  totalClosedQuantity: number;
  totalRealizedPnl: number;
  exitBatchCount: number;
  items: ClosedPortfolioItem[];
}

interface ReviewModalProps {
  item: ClosedPortfolioItem;
  review: TradeReviewResponse | null;
  loading: boolean;
  error: string | null;
  copyStatus: CopyStatus;
  onCopyEvidence: () => void;
  onClose: () => void;
}

const PERIOD_OPTIONS: PeriodOption[] = [
  { key: "1d", label: "1天", days: 1 },
  { key: "1w", label: "1週", days: 7 },
  { key: "1m", label: "1月", days: 30 },
  { key: "1q", label: "1季", days: 90 },
  { key: "1y", label: "1年", days: 365 },
];

const SELECTED_PERIOD_STORAGE_KEY = "closedPortfolio.selectedPeriod";

function isValidPeriodKey(value: string | null): value is PeriodKey {
  return PERIOD_OPTIONS.some((option) => option.key === value);
}

function readStoredPeriod(): PeriodKey {
  try {
    const storedPeriod = window.localStorage.getItem(SELECTED_PERIOD_STORAGE_KEY);
    return isValidPeriodKey(storedPeriod) ? storedPeriod : "1m";
  } catch {
    return "1m";
  }
}

function writeStoredPeriod(period: PeriodKey): boolean {
  try {
    window.localStorage.setItem(SELECTED_PERIOD_STORAGE_KEY, period);
    return true;
  } catch {
    return false;
  }
}

function toLocalDate(value: string): Date | null {
  const [yearText, monthText, dayText] = value.slice(0, 10).split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return null;
  return new Date(year, month - 1, day);
}

function getToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function isWithinPeriod(exitDate: string, days: number, today: Date): boolean {
  const date = toLocalDate(exitDate);
  if (!date) return false;
  const start = new Date(today);
  start.setDate(today.getDate() - days + 1);
  return date >= start && date <= today;
}

function groupClosedItems(items: ClosedPortfolioItem[]): ClosedPortfolioGroup[] {
  const groups: ClosedPortfolioGroup[] = [];
  const groupMap = new Map<string, ClosedPortfolioGroup>();

  for (const item of items) {
    let group = groupMap.get(item.position_group_id);
    if (!group) {
      group = {
        position_group_id: item.position_group_id,
        symbol: item.symbol,
        entry_date: item.entry_date,
        entry_price: item.entry_price,
        totalClosedQuantity: 0,
        totalRealizedPnl: 0,
        exitBatchCount: 0,
        items: [],
      };
      groupMap.set(item.position_group_id, group);
      groups.push(group);
    }

    group.totalClosedQuantity += item.exit_quantity;
    group.totalRealizedPnl += item.realized_pnl;
    group.exitBatchCount += 1;
    group.items.push(item);
  }

  return groups;
}

function getSignedPriceText(value: number | null | undefined, symbol?: string): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${formatPrice(value, symbol)}`;
}

function getSignedPercentText(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatPlainValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") return Number.isFinite(value) ? new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 4 }).format(value) : "—";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  return JSON.stringify(value, null, 2);
}

function getStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function hasDataQualityPrompt(dataQuality: TradeReviewDataQuality | undefined): boolean {
  if (!dataQuality) return false;
  const notes = getStringArray(dataQuality.notes);
  const insufficientData = getStringArray(dataQuality.insufficient_data);
  return dataQuality.status === "insufficient" || notes.length > 0 || insufficientData.length > 0;
}

function ReviewMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
      <p className="text-xs text-text-faint">{label}</p>
      <p className="mt-1 font-mono text-sm font-medium text-text-primary">{value}</p>
    </div>
  );
}

function TradeResultSection({ metrics, symbol }: { metrics: TradeReviewResultMetrics | undefined; symbol: string }) {
  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-text-primary">交易結果</h3>
        <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">單筆出場批次</span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <ReviewMetric label="進場日期" value={formatPlainValue(metrics?.entry_date)} />
        <ReviewMetric label="出場日期" value={formatPlainValue(metrics?.exit_date)} />
        <ReviewMetric label="進場價格" value={formatPrice(metrics?.entry_price, symbol)} />
        <ReviewMetric label="出場價格" value={formatPrice(metrics?.exit_price, symbol)} />
        <ReviewMetric label="持有天數" value={metrics?.holding_days == null ? "—" : `${metrics.holding_days} 天`} />
        <ReviewMetric label="已實現損益" value={getSignedPriceText(metrics?.realized_pnl, symbol)} />
        <ReviewMetric label="已實現報酬" value={getSignedPercentText(metrics?.realized_return_pct)} />
        <ReviewMetric label="最高浮盈" value={getSignedPercentText(metrics?.max_profit_pct)} />
        <ReviewMetric label="最大回撤" value={getSignedPercentText(metrics?.max_drawdown_pct)} />
        <ReviewMetric label="獲利回吐" value={getSignedPercentText(metrics?.profit_giveback_pct)} />
      </div>
    </article>
  );
}

function ReviewSignalList({ label, values }: { label: string; values: string[] | undefined }) {
  if (!values || values.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium text-text-muted">{label}</p>
      <ul className="mt-1 space-y-1">
        {values.map((value, index) => (
          <li key={`${label}-${index}`} className="rounded-md bg-badge-neutral-bg px-2 py-1 text-xs leading-relaxed text-badge-neutral-text">
            {value}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DetectedEventsList({ events }: { events: Record<string, unknown>[] | undefined }) {
  if (!events || events.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium text-text-muted">偵測事件</p>
      <div className="mt-1 space-y-1.5">
        {events.map((event, index) => {
          const date = typeof event.date === "string" ? event.date : null;
          const type = typeof event.type === "string" ? event.type : "event";
          const description = typeof event.summary === "string"
            ? event.summary
            : typeof event.description === "string" ? event.description : null;
          return (
            <div key={`${type}-${date ?? index}`} className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-xs text-text-secondary">
              <div className="flex flex-wrap items-center gap-2">
                {date && <span className="font-mono text-text-faint">{date}</span>}
                <span className="font-medium text-text-primary">{type}</span>
              </div>
              {description && <p className="mt-1 leading-relaxed text-text-muted">{description}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReviewSectionCard({ title, section }: { title: string; section: TradeReviewSection | TradeReviewHoldingSection | undefined }) {
  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
        <div className="flex flex-wrap gap-1.5">
          {section?.classification && (
            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
              {section.classification}
            </span>
          )}
          {section?.confidence && (
            <span className="rounded-md bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
              信心 {section.confidence}
            </span>
          )}
          {section?.market_regime && (
            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
              {section.market_regime}
            </span>
          )}
        </div>
      </div>
      {section?.summary ? (
        <p className="text-sm leading-relaxed text-text-secondary">{section.summary}</p>
      ) : (
        <p className="text-sm text-text-faint">尚無摘要。</p>
      )}
      <div className="mt-3 space-y-3">
        <ReviewSignalList label="支持訊號" values={section?.supporting_signals} />
        <ReviewSignalList label="衝突訊號" values={section?.conflicting_signals} />
        <ReviewSignalList label="注意事項" values={section?.caveats} />
        <DetectedEventsList events={(section as TradeReviewHoldingSection | undefined)?.detected_events} />
      </div>
    </article>
  );
}

function DataQualitySection({ dataQuality }: { dataQuality: TradeReviewDataQuality }) {
  const notes = getStringArray(dataQuality.notes);
  const insufficientData = getStringArray(dataQuality.insufficient_data);
  return (
    <article className="rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm dark:border-amber-800 dark:bg-amber-950">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-amber-800 dark:text-amber-300">資料品質提示</h3>
        {dataQuality.status && <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-900 dark:text-amber-200">{dataQuality.status}</span>}
      </div>
      {notes.length > 0 && <ReviewSignalList label="提示" values={notes} />}
      {insufficientData.length > 0 && <ReviewSignalList label="資料不足欄位" values={insufficientData} />}
    </article>
  );
}

function ReviewModal({ item, review, loading, error, copyStatus, onCopyEvidence, onClose }: ReviewModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);

  function handleBackdropMouseDown(event: MouseEvent<HTMLDivElement>) {
    mouseDownOnBackdrop.current = event.target === backdropRef.current;
  }

  function handleBackdropClick(event: MouseEvent<HTMLDivElement>) {
    if (mouseDownOnBackdrop.current && event.target === backdropRef.current) onClose();
    mouseDownOnBackdrop.current = false;
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const result = review?.review_result;

  return (
    <div
      ref={backdropRef}
      onMouseDown={handleBackdropMouseDown}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-card shadow-xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border-subtle bg-card px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">{item.symbol} 檢討分析</p>
            <p className="mt-1 text-xs text-text-faint">
              {item.entry_date} → {item.exit_date} ｜ 出場 {item.exit_quantity} 股 ｜ {getSignedPriceText(item.realized_pnl, item.symbol)}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
            aria-label="關閉檢討分析"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          {loading && (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <div className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" />
              <p className="text-sm font-medium text-text-primary">載入檢討分析中</p>
              <p className="text-xs text-text-muted">若尚未產生，會建立這筆出場批次的檢討。</p>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </div>
          )}

          {review && result && (
            <>
              <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-xs font-medium text-text-muted">檢討版本</p>
                  <p className="mt-1 text-sm text-text-primary">{review.review_version}</p>
                </div>
                <button
                  type="button"
                  onClick={onCopyEvidence}
                  className="rounded-lg border border-indigo-500/40 px-4 py-2 text-sm font-medium text-indigo-700 transition hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:text-indigo-300 dark:hover:bg-indigo-950"
                >
                  {copyStatus === "success" ? "已複製指標資料" : copyStatus === "error" ? "複製失敗" : "複製指標資料"}
                </button>
              </div>

              <TradeResultSection metrics={result.trade_result} symbol={item.symbol} />
              <ReviewSectionCard title="進場檢討" section={result.entry_review} />
              <ReviewSectionCard title="持有路徑" section={result.holding_review} />
              <ReviewSectionCard title="出場檢討" section={result.exit_review} />
              <ReviewSectionCard title="下次規則" section={result.operation_review} />
              {hasDataQualityPrompt(result.data_quality) && result.data_quality && <DataQualitySection dataQuality={result.data_quality} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ClosedPortfolioPage() {
  const [items, setItems] = useState<ClosedPortfolioItem[]>([]);
  const [selectedPeriod, setSelectedPeriod] = useState<PeriodKey>(readStoredPeriod);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedReviewItem, setSelectedReviewItem] = useState<ClosedPortfolioItem | null>(null);
  const [reviewMap, setReviewMap] = useState<Record<number, TradeReviewResponse>>({});
  const [reviewLoading, setReviewLoading] = useState<Record<number, boolean>>({});
  const [reviewError, setReviewError] = useState<Record<number, string | null>>({});
  const [copyStatus, setCopyStatus] = useState<Record<number, CopyStatus>>({});

  useEffect(() => {
    writeStoredPeriod(selectedPeriod);
  }, [selectedPeriod]);

  useEffect(() => {
    let cancelled = false;

    async function loadClosedPortfolio() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio/closed`, {
          headers: authHeaders(),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: ClosedPortfolioItem[] = await res.json();
        if (!cancelled) setItems(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "已結案持股載入失敗");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadClosedPortfolio();
    return () => {
      cancelled = true;
    };
  }, []);

  const activePeriod = PERIOD_OPTIONS.find((option) => option.key === selectedPeriod) ?? PERIOD_OPTIONS[2];
  const filteredItems = useMemo(() => {
    const today = getToday();
    return items.filter((item) => isWithinPeriod(item.exit_date, activePeriod.days, today));
  }, [activePeriod.days, items]);

  const groupedItems = useMemo(() => groupClosedItems(filteredItems), [filteredItems]);

  const totalRealizedPnl = useMemo(
    () => filteredItems.reduce((total, item) => total + item.realized_pnl, 0),
    [filteredItems],
  );
  const totalClass = totalRealizedPnl >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";

  async function fetchReview(item: ClosedPortfolioItem): Promise<TradeReviewResponse> {
    const reviewUrl = `${import.meta.env.VITE_API_URL}/portfolio/${item.id}/review`;
    const getResponse = await fetch(reviewUrl, { headers: authHeaders() });
    if (getResponse.ok) return await getResponse.json() as TradeReviewResponse;
    if (getResponse.status !== 404) throw new Error(`HTTP ${getResponse.status}`);

    const postResponse = await fetch(reviewUrl, {
      method: "POST",
      headers: authHeaders(),
    });
    if (!postResponse.ok) throw new Error(`HTTP ${postResponse.status}`);
    return await postResponse.json() as TradeReviewResponse;
  }

  async function openReview(item: ClosedPortfolioItem): Promise<void> {
    setSelectedReviewItem(item);
    setCopyStatus((prev) => ({ ...prev, [item.id]: "idle" }));
    if (reviewMap[item.id]) return;

    setReviewLoading((prev) => ({ ...prev, [item.id]: true }));
    setReviewError((prev) => ({ ...prev, [item.id]: null }));
    try {
      const review = await fetchReview(item);
      setReviewMap((prev) => ({ ...prev, [item.id]: review }));
    } catch (err) {
      setReviewError((prev) => ({ ...prev, [item.id]: err instanceof Error ? err.message : "檢討分析載入失敗" }));
    } finally {
      setReviewLoading((prev) => ({ ...prev, [item.id]: false }));
    }
  }

  async function copyEvidence(): Promise<void> {
    if (!selectedReviewItem) return;
    const review = reviewMap[selectedReviewItem.id];
    if (!review) return;

    try {
      await navigator.clipboard.writeText(JSON.stringify(review.evidence_payload, null, 2));
      setCopyStatus((prev) => ({ ...prev, [selectedReviewItem.id]: "success" }));
    } catch {
      setCopyStatus((prev) => ({ ...prev, [selectedReviewItem.id]: "error" }));
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="h-3 w-20 animate-pulse rounded bg-border" />
          <div className="mt-3 h-8 w-40 animate-pulse rounded bg-border" />
        </div>
        {[1, 2].map((item) => (
          <div key={item} className="rounded-xl border border-border bg-card px-4 py-3 shadow-sm">
            <div className="h-5 w-24 animate-pulse rounded bg-border" />
            <div className="mt-2 h-3 w-48 animate-pulse rounded bg-border" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-xs font-medium text-text-faint">{activePeriod.label} 已實現損益</p>
              <p className={`mt-1 font-mono text-3xl font-semibold ${totalClass}`}>
                {totalRealizedPnl >= 0 ? "+" : ""}{formatPrice(totalRealizedPnl)}
              </p>
              <p className="mt-1 text-xs text-text-muted">篩選 {filteredItems.length} 筆已結案紀錄</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {PERIOD_OPTIONS.map((option) => {
                const isActive = option.key === selectedPeriod;
                return (
                  <button
                    key={option.key}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setSelectedPeriod(option.key)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${isActive
                      ? "bg-indigo-600 text-white"
                      : "border border-border bg-card text-text-muted hover:bg-card-hover"
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow-sm dark:border-red-900 dark:bg-red-950 dark:text-red-400">
            {error}
          </div>
        )}

        <section className="rounded-xl border border-border bg-card shadow-sm">
          <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
            <h2 className="text-sm font-semibold text-text-primary">已結案持股</h2>
            <span className="text-xs text-text-faint">共 {filteredItems.length} 筆</span>
          </div>

          {filteredItems.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-text-faint">
              此期間尚無已結案持股。
            </div>
          ) : (
            <div className="space-y-4 p-4">
              {groupedItems.map((group) => {
                const groupIsProfit = group.totalRealizedPnl >= 0;
                const groupResultClass = groupIsProfit ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";
                return (
                  <article key={group.position_group_id} className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
                    <div className="border-b border-border bg-surface px-4 py-4 sm:px-5">
                      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                        <div className="border-l-4 border-l-indigo-500 pl-3 dark:border-l-indigo-400">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-mono text-lg font-semibold text-text-primary">{group.symbol}</p>
                            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                              進場 {group.entry_date}
                            </span>
                            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                              成本 {formatPrice(group.entry_price, group.symbol)}
                            </span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-2 text-xs text-text-muted">
                            <span className="rounded-md border border-border-subtle bg-card px-2 py-1">總出場 {group.totalClosedQuantity} 股</span>
                            <span className="rounded-md border border-border-subtle bg-card px-2 py-1">出場批次 {group.exitBatchCount} 筆</span>
                            <span className="rounded-md border border-border-subtle bg-card px-2 py-1 font-mono text-text-faint">
                              Group {group.position_group_id.slice(0, 8)}
                            </span>
                          </div>
                        </div>
                        <div className="rounded-xl border border-border bg-card px-4 py-3 text-left shadow-sm md:text-right">
                          <p className="text-xs font-medium text-text-muted">股票總已實現損益</p>
                          <p className={`mt-1 font-mono text-lg font-semibold ${groupResultClass}`}>{getSignedPriceText(group.totalRealizedPnl, group.symbol)}</p>
                        </div>
                      </div>
                    </div>

                    <div className="bg-card p-3 sm:p-4">
                      <div className="space-y-2 border-l border-border-subtle pl-3 sm:pl-4">
                        {group.items.map((item) => {
                          const isProfit = item.realized_pnl >= 0;
                          const resultClass = isProfit ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";
                          const isReviewLoading = reviewLoading[item.id] ?? false;
                          return (
                            <div key={item.id} className="rounded-lg border border-border-subtle bg-surface px-3 py-3 shadow-sm sm:px-4">
                              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <p className="font-semibold text-text-primary">出場批次 #{item.id}</p>
                                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                                      {item.entry_date} → {item.exit_date}
                                    </span>
                                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                                      持有 {item.holding_days} 天
                                    </span>
                                  </div>
                                  <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs text-text-muted">
                                    <span>{formatPrice(item.entry_price, item.symbol)} → {formatPrice(item.exit_price, item.symbol)}</span>
                                    <span>出場 {item.exit_quantity} 股</span>
                                    <span>費稅 {formatPrice(item.exit_fees + item.exit_taxes, item.symbol)}</span>
                                  </div>
                                </div>
                                <div className="flex items-center justify-between gap-3 sm:justify-end">
                                  <div className="text-left sm:text-right">
                                    <p className={`font-mono text-sm font-semibold ${resultClass}`}>{getSignedPriceText(item.realized_pnl, item.symbol)}</p>
                                    <p className={`font-mono text-xs ${resultClass}`}>{getSignedPercentText(item.realized_return_pct)}</p>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => void openReview(item)}
                                    disabled={isReviewLoading}
                                    className="rounded-lg border border-indigo-500/40 px-3 py-2 text-xs font-medium text-indigo-700 transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-indigo-300 dark:hover:bg-indigo-950"
                                  >
                                    {isReviewLoading ? "載入中…" : "檢討分析"}
                                  </button>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>

      {selectedReviewItem && (
        <ReviewModal
          item={selectedReviewItem}
          review={reviewMap[selectedReviewItem.id] ?? null}
          loading={reviewLoading[selectedReviewItem.id] ?? false}
          error={reviewError[selectedReviewItem.id] ?? null}
          copyStatus={copyStatus[selectedReviewItem.id] ?? "idle"}
          onCopyEvidence={() => void copyEvidence()}
          onClose={() => setSelectedReviewItem(null)}
        />
      )}
    </>
  );
}
