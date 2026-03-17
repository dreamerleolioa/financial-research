// frontend/src/pages/DashboardPage.tsx
import { useState } from "react";
import { ConfidenceChart } from "../components/ConfidenceChart";
import { fetchSymbolHistory, type HistoryEntry } from "../lib/historyApi";

const ACTION_BADGE: Record<string, { label: string; cls: string }> = {
  Hold: { label: "續抱", cls: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" },
  Trim: { label: "減碼", cls: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-300" },
  Exit: { label: "出場", cls: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
  Add: { label: "加碼", cls: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" },
};

export default function DashboardPage() {
  const [symbol, setSymbol] = useState("");
  const [days, setDays] = useState(30);
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSearch() {
    if (!symbol.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSymbolHistory(symbol.trim(), days);
      setEntries(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "查詢失敗");
    } finally {
      setLoading(false);
    }
  }

  const chartData = entries.map((e) => ({
    date: e.record_date,
    confidence: e.signal_confidence,
    actionTag: e.action_tag,
    prevActionTag: e.prev_action_tag,
    isFinal: e.analysis_is_final,
  }));

  const signalChanges = entries.filter(
    (e) => e.prev_action_tag && e.prev_action_tag !== e.action_tag,
  );

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4">
      <h1 className="text-xl font-semibold text-text-primary">復盤儀表板</h1>

      {/* 查詢列 */}
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="股票代碼，例：2330.TW"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          className="flex-1 rounded border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary placeholder:text-text-faint focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded border border-input-border bg-input-bg px-2 py-2 text-sm text-text-primary"
        >
          <option value={14}>14 天</option>
          <option value={30}>30 天</option>
          <option value={60}>60 天</option>
          <option value={90}>90 天</option>
        </select>
        <button
          onClick={handleSearch}
          disabled={loading || !symbol.trim()}
          className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "查詢中…" : "查詢"}
        </button>
      </div>

      {error && <p className="rounded bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{error}</p>}

      {/* 信心分數折線圖 */}
      {entries.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-medium text-text-primary">
            {symbol} 信心分數趨勢（近 {days} 天）
          </h2>
          {entries.some((e) => !e.analysis_is_final) && (
            <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
              含未定稿分析點；這類資料可能來自盤中查詢，或該日雖然 raw data
              已收盤定稿，但分析尚未重跑，虛線外圈標記僅供參考，不代表收盤定論。
            </p>
          )}
          <ConfidenceChart data={chartData} />
        </div>
      )}

      {/* 訊號轉向記錄 */}
      {signalChanges.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-medium text-text-primary">訊號轉向紀錄</h2>
          <div className="space-y-3">
            {signalChanges.map((e) => {
              const prev = ACTION_BADGE[e.prev_action_tag ?? ""];
              const curr = ACTION_BADGE[e.action_tag ?? ""];
              const delta =
                e.signal_confidence != null && e.prev_confidence != null
                  ? (e.signal_confidence - e.prev_confidence).toFixed(1)
                  : null;
              return (
                <div
                  key={e.record_date}
                  className="rounded border border-border-subtle bg-surface p-3"
                >
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-text-muted">{e.record_date}</span>
                    {prev && (
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${prev.cls}`}>
                        {prev.label}
                      </span>
                    )}
                    <span className="text-text-faint">→</span>
                    {curr && (
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${curr.cls}`}>
                        {curr.label}
                      </span>
                    )}
                    {delta && (
                      <span
                        className={`text-xs ${Number(delta) > 0 ? "text-red-600 dark:text-red-400" : "text-green-600 dark:text-green-400"}`}
                      >
                        ({Number(delta) > 0 ? "+" : ""}
                        {delta} 分)
                      </span>
                    )}
                  </div>
                  {e.final_verdict && (
                    <p className="mt-1.5 text-xs leading-relaxed text-text-secondary line-clamp-3">
                      {e.final_verdict}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 完整歷史列表 */}
      {entries.length > 0 && (
        <div className="rounded-lg border border-border bg-card shadow-sm">
          <h2 className="border-b border-border-subtle px-4 py-3 text-sm font-medium text-text-primary">
            診斷紀錄（共 {entries.length} 筆）
          </h2>
          <div className="divide-y divide-border-subtle">
            {[...entries].reverse().map((e) => {
              const badge = ACTION_BADGE[e.action_tag ?? ""];
              return (
                <div key={e.record_date} className="flex items-center gap-3 px-4 py-3">
                  <span className="w-24 text-sm text-text-muted">{e.record_date}</span>
                  {badge ? (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
                      {badge.label}
                    </span>
                  ) : (
                    <span className="text-xs text-text-faint">—</span>
                  )}
                  <span className="text-sm text-text-primary">信心 {e.signal_confidence ?? "—"}</span>
                  {e.indicators?.rsi_14 != null && (
                    <span className="text-xs text-text-muted">
                      RSI {String(e.indicators.rsi_14)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!loading && entries.length === 0 && symbol && (
        <p className="text-center text-sm text-text-faint">此股票尚無歷史診斷紀錄</p>
      )}
    </div>
  );
}
