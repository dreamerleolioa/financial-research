import { useEffect, useState } from "react";
import { authHeaders } from "../lib/auth";

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
}

interface PortfolioPageProps {
  onNavigateAnalyze: (symbol: string) => void;
}

export default function PortfolioPage({ onNavigateAnalyze }: PortfolioPageProps) {
  const [items, setItems] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [latestMap, setLatestMap] = useState<Record<number, HistoryEntry | null>>({});
  const [historyMap, setHistoryMap] = useState<Record<number, HistoryEntry[]>>({});
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [historyLoading, setHistoryLoading] = useState<Record<number, boolean>>({});

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio`, {
          headers: authHeaders(),
        });
        if (!res.ok) return;
        const data: PortfolioItem[] = await res.json();
        setItems(data);

        // fetch latest entry for each item
        const entries = await Promise.all(
          data.map(async (item) => {
            try {
              const r = await fetch(
                `${import.meta.env.VITE_API_URL}/portfolio/${item.id}/history?limit=1`,
                { headers: authHeaders() },
              );
              if (!r.ok) return [item.id, null] as const;
              const body: { records: HistoryEntry[] } = await r.json();
              return [item.id, body.records[0] ?? null] as const;
            } catch {
              return [item.id, null] as const;
            }
          }),
        );
        setLatestMap(Object.fromEntries(entries));
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

  if (loading) {
    return <p className="text-sm text-slate-400">載入中…</p>;
  }

  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-400 shadow-sm">
        尚無追蹤中的持股。請至「個股分析」頁新增。
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-800">我的持股</h2>
        <span className="text-xs text-slate-400">共 {items.length} 筆</span>
      </div>

      {items.map((item) => {
        const latest = latestMap[item.id];
        const history = historyMap[item.id];
        const isExpanded = expandedId === item.id;

        return (
          <article key={item.id} className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-slate-800">{item.symbol}</p>
                  <p className="mt-0.5 text-xs text-slate-400">
                    成本 {item.entry_price}
                    {item.quantity > 0 && ` ｜ ${item.quantity} 股`}
                    {" ｜ "}
                    {item.entry_date}
                  </p>
                  {latest && (
                    <p className="mt-1 text-xs text-slate-600">
                      最新：
                      {latest.action_tag && (
                        <span className="mr-1 font-medium">{latest.action_tag}</span>
                      )}
                      {latest.signal_confidence != null && (
                        <span className="text-indigo-600">信心 {latest.signal_confidence}</span>
                      )}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    onClick={() => toggleHistory(item.id)}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                  >
                    {isExpanded ? "收起歷史" : "查看歷史"}
                  </button>
                  <button
                    onClick={() => onNavigateAnalyze(item.symbol)}
                    className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
                  >
                    即時分析
                  </button>
                </div>
              </div>
            </div>

            {isExpanded && (
              <div className="border-t border-slate-100 px-4 pb-4 pt-3">
                {historyLoading[item.id] ? (
                  <p className="text-xs text-slate-400">載入中…</p>
                ) : history && history.length > 0 ? (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-slate-400">
                        <th className="pb-1 font-medium">日期</th>
                        <th className="pb-1 font-medium">建議</th>
                        <th className="pb-1 font-medium">信心</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                      {history.map((row, idx) => (
                        <tr key={idx} className="text-slate-700">
                          <td className="py-1">{row.record_date}</td>
                          <td className="py-1">{row.action_tag ?? "—"}</td>
                          <td className="py-1">{row.signal_confidence ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="text-xs text-slate-400">尚無診斷紀錄。</p>
                )}
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}
