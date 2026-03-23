import { useEffect, useRef, useState } from "react";
import { authHeaders } from "../lib/auth";
import { formatPrice } from "../lib/formatters";
import { InsightText } from "../components/InsightText";

interface PortfolioItem {
  id: number;
  symbol: string;
  entry_price: number;
  quantity: number;
  entry_date: string;
  notes: string | null;
}

interface HistoryEntry {
  record_date: string;
  action_tag: string | null;
  signal_confidence: number | null;
  recommended_action: string | null;
  indicators: { close_price?: number | null } | null;
}

interface PositionAnalysis {
  entry_price: number;
  profit_loss_pct: number | null;
  position_status: "profitable_safe" | "at_risk" | "under_water" | null;
  position_narrative: string | null;
  recommended_action: "Hold" | "Trim" | "Exit" | null;
  trailing_stop: number | null;
  trailing_stop_reason: string | null;
  exit_reason: string | null;
}

interface PositionResult {
  snapshot: { current_price?: number;[key: string]: unknown };
  position_analysis: PositionAnalysis | null;
  confidence_score: number | null;
  analysis_detail: {
    technical_signal: string;
    institutional_flow: string | null;
    tech_insight: string | null;
    inst_insight: string | null;
    news_insight: string | null;
    final_verdict: string | null;
    summary: string;
  } | null;
}

interface PortfolioPageProps {
  onNavigateAnalyze: (symbol: string) => void;
}

// ─── Edit Modal ───────────────────────────────────────────────

interface EditPortfolioModalProps {
  item: PortfolioItem;
  onClose: () => void;
  onSaved: (updated: PortfolioItem) => void;
}

function EditPortfolioModal({ item, onClose, onSaved }: EditPortfolioModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);
  const [entryPrice, setEntryPrice] = useState(String(item.entry_price));
  const [quantity, setQuantity] = useState(String(item.quantity));
  const [entryDate, setEntryDate] = useState(item.entry_date);
  const [notes, setNotes] = useState(item.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio/${item.id}`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({
          entry_price: parseFloat(entryPrice),
          quantity: parseInt(quantity, 10),
          entry_date: entryDate,
          notes: notes.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated: PortfolioItem = await res.json();
      onSaved(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "儲存失敗");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      ref={backdropRef}
      onMouseDown={(e) => { mouseDownOnBackdrop.current = e.target === backdropRef.current; }}
      onClick={(e) => { if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose(); mouseDownOnBackdrop.current = false; }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="w-full max-w-md rounded-2xl bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <p className="font-semibold text-text-primary">編輯持股 · {item.symbol}</p>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          {saved ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
              持倉資訊已更新。若成本價或日期有變更，建議重新觸發分析以確保診斷數據正確。
            </div>
          ) : (
            <>
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
              {error && (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">{error}</p>
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
              {saving ? "儲存中…" : "儲存"}
            </button>
          )}
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
      const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio/${item.id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
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
      onMouseDown={(e) => { mouseDownOnBackdrop.current = e.target === backdropRef.current; }}
      onClick={(e) => { if (mouseDownOnBackdrop.current && e.target === backdropRef.current) onClose(); mouseDownOnBackdrop.current = false; }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="w-full max-w-sm rounded-2xl bg-card shadow-xl">
        <div className="p-5">
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-red-100 dark:bg-red-950">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-red-600 dark:text-red-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
          </div>
          <p className="font-semibold text-text-primary">刪除 {item.symbol} 持股？</p>
          <p className="mt-1.5 text-sm text-text-muted">
            此操作將同時移除所有歷史診斷紀錄，且<span className="font-medium text-red-600 dark:text-red-400">無法復原</span>。
          </p>
          {error && (
            <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">{error}</p>
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
  profitable_safe: { label: "獲利安全區", color: "text-green-700 dark:text-green-400", bg: "bg-green-50 border-green-200 dark:bg-green-950 dark:border-green-800", dot: "🟢" },
  at_risk: { label: "成本邊緣", color: "text-yellow-700 dark:text-yellow-400", bg: "bg-yellow-50 border-yellow-200 dark:bg-yellow-950 dark:border-yellow-800", dot: "🟡" },
  under_water: { label: "套牢防守", color: "text-red-700 dark:text-red-400", bg: "bg-red-50 border-red-200 dark:bg-red-950 dark:border-red-800", dot: "🔴" },
} as const;

const ACTION_CONFIG = {
  Hold: { label: "續抱", color: "text-green-700 dark:text-green-400", bg: "bg-green-50 border-green-200 dark:bg-green-950 dark:border-green-800" },
  Trim: { label: "減碼", color: "text-yellow-700 dark:text-yellow-400", bg: "bg-yellow-50 border-yellow-200 dark:bg-yellow-950 dark:border-yellow-800" },
  Exit: { label: "出場", color: "text-red-700 dark:text-red-400", bg: "bg-red-50 border-red-200 dark:bg-red-950 dark:border-red-800" },
} as const;

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
            <p className="font-semibold text-text-primary">{item.symbol} 持股診斷</p>
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
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 p-5">
          {loading && (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <div className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" style={{ animationDuration: "1s" }} />
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
              {pa.exit_reason && (
                <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950">
                  <div className="mb-1 font-semibold text-red-700 dark:text-red-400">出場警示</div>
                  <div className="text-sm text-red-700 dark:text-red-400">{pa.exit_reason}</div>
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
                    {pa.position_narrative && (
                      <p className="text-sm text-text-secondary">{pa.position_narrative}</p>
                    )}
                    <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                      <div className="text-center">
                        <div className="text-text-faint">成本</div>
                        <div className="font-mono font-medium text-text-secondary">{formatPrice(pa.entry_price, item.symbol)}</div>
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
                          className={`font-mono font-medium ${pa.profit_loss_pct != null && pa.profit_loss_pct >= 0
                            ? "text-green-600"
                            : "text-red-600"
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

                {pa.recommended_action && (
                  <article className={`rounded-xl border p-4 shadow-sm ${ACTION_CONFIG[pa.recommended_action].bg}`}>
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs font-semibold text-text-muted">操作建議</span>
                      <span className={`text-xl font-bold ${ACTION_CONFIG[pa.recommended_action].color}`}>
                        {ACTION_CONFIG[pa.recommended_action].label}
                      </span>
                    </div>
                    <div className="flex justify-between text-xs text-text-muted">
                      <span>防守位</span>
                      <span className="font-mono font-medium text-orange-600 dark:text-orange-400">
                        {formatPrice(pa.trailing_stop, item.symbol)}
                      </span>
                    </div>
                    {pa.trailing_stop_reason && (
                      <p className="mt-2 text-xs leading-relaxed text-text-secondary">{pa.trailing_stop_reason}</p>
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
              ].filter(({ content }) => content).map(({ label, content }) => (
                <article key={label} className="rounded-xl border border-border bg-card p-4 shadow-sm">
                  <div className="mb-2 text-xs font-semibold text-text-muted">{label}</div>
                  <InsightText text={content} emptyText="—" />
                </article>
              ))}

              <p className="text-center text-xs text-text-faint">
                本診斷結果僅供參考，不構成投資建議。
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PortfolioPage({ onNavigateAnalyze: _onNavigateAnalyze }: PortfolioPageProps) {
  const [items, setItems] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [latestMap, setLatestMap] = useState<Record<string, HistoryEntry | null>>({});
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
  const [deleteItem, setDeleteItem] = useState<PortfolioItem | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio`, {
          headers: authHeaders(),
        });
        if (!res.ok) return;
        const data: PortfolioItem[] = await res.json();
        setItems(data);

        try {
          const r = await fetch(
            `${import.meta.env.VITE_API_URL}/portfolio/latest-history`,
            { headers: authHeaders() },
          );
          if (r.ok) {
            const latestData: Record<string, HistoryEntry | null> = await r.json();
            setLatestMap(latestData);
          }
        } catch { /* ignore */ }
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function toggleHistory(id: number) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (historyMap[id]) return;
    setHistoryLoading((prev) => ({ ...prev, [id]: true }));
    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_URL}/portfolio/${id}/history?limit=20`,
        { headers: authHeaders() },
      );
      if (!res.ok) return;
      const body: { records: HistoryEntry[] } = await res.json();
      setHistoryMap((prev) => ({ ...prev, [id]: body.records }));
    } finally {
      setHistoryLoading((prev) => ({ ...prev, [id]: false }));
    }
  }

  async function openAnalysis(item: PortfolioItem) {
    setModalItem(item);

    setAnalysisLoading((prev) => ({ ...prev, [item.id]: true }));
    setAnalysisError((prev) => ({ ...prev, [item.id]: null }));
    try {
      const body: Record<string, unknown> = {
        symbol: item.symbol,
        entry_price: item.entry_price,
      };
      if (item.entry_date) body.entry_date = item.entry_date;
      if (item.quantity > 0) body.quantity = item.quantity;

      const res = await fetch(`${import.meta.env.VITE_API_URL}/analyze/position`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: PositionResult = await res.json();
      setAnalysisMap((prev) => ({ ...prev, [item.id]: data }));

      // Refresh latest card entry + history panel (if expanded)
      setHistoryMap((prev) => { const next = { ...prev }; delete next[item.id]; return next; });
      try {
        const r = await fetch(
          `${import.meta.env.VITE_API_URL}/portfolio/${item.id}/history?limit=20`,
          { headers: authHeaders() },
        );
        if (r.ok) {
          const hBody: { records: HistoryEntry[] } = await r.json();
          setLatestMap((prev) => ({ ...prev, [item.id]: hBody.records[0] ?? null }));
          setHistoryMap((prev) => ({ ...prev, [item.id]: hBody.records }));
        }
      } catch { /* ignore */ }
    } catch (err) {
      setAnalysisError((prev) => ({
        ...prev,
        [item.id]: err instanceof Error ? err.message : "請求失敗",
      }));
    } finally {
      setAnalysisLoading((prev) => ({ ...prev, [item.id]: false }));
    }
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

  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center text-sm text-text-faint shadow-sm">
        尚無追蹤中的持股。請至「個股分析」頁新增。
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">我的持股</h2>
          <span className="text-xs text-text-faint">共 {items.length} 筆</span>
        </div>

        {items.map((item) => {
          const latest = latestMap[String(item.id)];
          const history = historyMap[item.id];
          const isExpanded = expandedId === item.id;
          const isAnalyzing = analysisLoading[item.id];

          return (
            <article key={item.id} className="rounded-xl border border-border bg-card shadow-sm">
              <div className="p-4">
                {/* Info row */}
                <div className="flex items-start justify-between gap-2">
                  <p className="font-semibold text-text-primary">{item.symbol}</p>
                  {/* P/L badge — top-right, only when available */}
                  {(() => {
                    const closePrice = latest?.indicators?.close_price;
                    const plPct = closePrice != null
                      ? ((closePrice - item.entry_price) / item.entry_price) * 100
                      : null;
                    return plPct != null ? (
                      <span className={`rounded-md px-2 py-0.5 text-xs font-mono font-medium ${plPct >= 0
                        ? "bg-green-50 text-green-600 border border-green-200"
                        : "bg-red-50 text-red-600 border border-red-200"
                        }`}>
                        {plPct > 0 ? "+" : ""}{plPct.toFixed(2)}%
                      </span>
                    ) : null;
                  })()}
                </div>

                {/* Meta badges */}
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                    成本 <span className="font-medium text-text-primary">{formatPrice(item.entry_price, item.symbol)}</span>
                  </span>
                  {item.quantity > 0 && (
                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                      <span className="font-medium text-text-primary">{item.quantity}</span> 股
                    </span>
                  )}
                  <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                    {item.entry_date}
                  </span>
                </div>

                {/* Last analysis row */}
                {latest && (() => {
                  const action = latest.recommended_action;
                  const actionLabel =
                    action === "Exit" ? "出場" :
                      action === "Trim" ? "減碼" :
                        action === "Hold" ? "續抱" :
                          action ?? null;
                  const actionBadge =
                    action === "Exit" ? "bg-red-50 text-red-600 border border-red-200" :
                      action === "Trim" ? "bg-yellow-50 text-yellow-600 border border-yellow-200" :
                        action === "Hold" ? "bg-green-50 text-green-600 border border-green-200" :
                          "bg-badge-neutral-bg text-badge-neutral-text";
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
                  <div className="ml-auto flex items-center gap-1">
                    <button
                      onClick={() => setEditItem(item)}
                      title="編輯持股"
                      className="rounded-lg p-2 text-text-faint hover:bg-card-hover hover:text-text-secondary"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                      </svg>
                    </button>
                    <button
                      onClick={() => setDeleteItem(item)}
                      title="刪除持股"
                      className="rounded-lg p-2 text-text-faint hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-950 dark:hover:text-red-400"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
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
                          <th className="pb-1 font-medium">操作建議</th>
                          <th className="pb-1 font-medium text-right">當時損益</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border-subtle">
                        {history.map((row) => {
                          const action = row.recommended_action;
                          const actionColor =
                            action === "Exit" ? "text-red-600 font-semibold" :
                              action === "Trim" ? "text-yellow-600 font-semibold" :
                                action === "Hold" ? "text-green-600 font-semibold" :
                                  "text-text-secondary";
                          const actionLabel =
                            action === "Exit" ? "出場" :
                              action === "Trim" ? "減碼" :
                                action === "Hold" ? "續抱" :
                                  action ?? "—";
                          const closePrice = row.indicators?.close_price;
                          const plPct = closePrice != null
                            ? ((closePrice - item.entry_price) / item.entry_price) * 100
                            : null;
                          return (
                            <tr key={row.record_date} className="text-text-secondary">
                              <td className="py-1">{row.record_date}</td>
                              <td className={`py-1 ${actionColor}`}>{actionLabel}</td>
                              <td className={`py-1 text-right font-mono text-xs ${plPct == null ? "text-text-faint" :
                                plPct >= 0 ? "text-green-600" : "text-red-600"
                                }`}>
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
          onClose={() => setEditItem(null)}
          onSaved={(updated) => {
            setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
          }}
        />
      )}

      {deleteItem && (
        <DeleteConfirmModal
          item={deleteItem}
          onClose={() => setDeleteItem(null)}
          onDeleted={(id) => {
            setItems((prev) => prev.filter((i) => i.id !== id));
            setDeleteItem(null);
          }}
        />
      )}
    </>
  );
}
