import { useState } from "react";
import { authHeaders } from "../lib/auth";

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

interface PositionResponse {
  symbol: string;
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

function InsightText({ text }: { text: string | null | undefined }) {
  if (!text) return <p className="text-sm text-slate-400">—</p>;
  const sentences = text
    .split(/(?<=[。；！？：\n])/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (sentences.length <= 1)
    return <p className="text-sm leading-relaxed text-slate-700">{text}</p>;
  return (
    <div className="space-y-1.5">
      {sentences.map((s, i) => (
        <p key={i} className="text-sm leading-relaxed text-slate-700">
          {s}
        </p>
      ))}
    </div>
  );
}

const STATUS_CONFIG = {
  profitable_safe: { label: "獲利安全區", color: "text-green-700", bg: "bg-green-50 border-green-200", dot: "🟢" },
  at_risk: { label: "成本邊緣", color: "text-yellow-700", bg: "bg-yellow-50 border-yellow-200", dot: "🟡" },
  under_water: { label: "套牢防守", color: "text-red-700", bg: "bg-red-50 border-red-200", dot: "🔴" },
} as const;

const ACTION_CONFIG = {
  Hold: { label: "續抱", color: "text-green-700", bg: "bg-green-50 border-green-200" },
  Trim: { label: "減碼", color: "text-yellow-700", bg: "bg-yellow-50 border-yellow-200" },
  Exit: { label: "出場", color: "text-red-700", bg: "bg-red-50 border-red-200" },
} as const;

export default function PositionPage() {
  const [symbol, setSymbol] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [entryDate, setEntryDate] = useState("");
  const [quantity, setQuantity] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PositionResponse | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol || !entryPrice) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const body: Record<string, unknown> = {
        symbol,
        entry_price: parseFloat(entryPrice),
      };
      if (entryDate) body.entry_date = entryDate;
      if (quantity) body.quantity = parseInt(quantity);

      const res = await fetch(`${import.meta.env.VITE_API_URL}/analyze/position`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "請求失敗");
    } finally {
      setLoading(false);
    }
  }

  const pa = result?.position_analysis;

  return (
    <div className="space-y-6">
      {/* Input Form */}
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
        <h2 className="mb-4 text-sm font-semibold text-slate-800">持股診斷</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">股票代碼 *</label>
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="2330.TW 或 6488.TWO"
                required
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-indigo-200 transition focus:ring-2"
              />
              <p className="mt-1 text-xs text-slate-500">上市股票請用 .TW，上櫃股票請用 .TWO。</p>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">購入成本價 *</label>
              <input
                type="number"
                value={entryPrice}
                onChange={(e) => setEntryPrice(e.target.value)}
                placeholder="980"
                required
                min="0.01"
                step="0.01"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-indigo-200 transition focus:ring-2"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">購入日期（選填）</label>
              <input
                type="date"
                value={entryDate}
                onChange={(e) => setEntryDate(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-indigo-200 transition focus:ring-2"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">持有數量（選填）</label>
              <input
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder="1000"
                min="1"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-indigo-200 transition focus:ring-2"
              />
            </div>
          </div>
          <button
            type="submit"
            disabled={loading || !symbol || !entryPrice}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "診斷中..." : "開始診斷"}
          </button>
        </form>
      </section>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Results */}
      {result && pa && (
        <div className="space-y-4">
          {/* Exit Warning */}
          {pa.exit_reason && (
            <div className="rounded-xl border border-red-200 bg-red-50 p-4">
              <div className="mb-1 font-semibold text-red-700">出場警示</div>
              <div className="text-sm text-red-700">{pa.exit_reason}</div>
            </div>
          )}

          {/* Position Status + Action: side by side */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* Position Status Card */}
            {pa.position_status && (
              <article className={`rounded-xl border p-5 shadow-sm ${STATUS_CONFIG[pa.position_status].bg}`}>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-500">倉位狀態</span>
                  <span className={`font-bold ${STATUS_CONFIG[pa.position_status].color}`}>
                    {STATUS_CONFIG[pa.position_status].dot} {STATUS_CONFIG[pa.position_status].label}
                  </span>
                </div>
                <p className="text-sm text-slate-600">{pa.position_narrative}</p>
                <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                  <div className="text-center">
                    <div className="text-slate-400">成本價</div>
                    <div className="font-mono font-medium text-slate-700">{pa.entry_price}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-slate-400">現價</div>
                    <div className="font-mono font-medium text-slate-700">
                      {typeof result.snapshot?.current_price === "number"
                        ? result.snapshot.current_price
                        : "—"}
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-slate-400">損益</div>
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

            {/* Action Card */}
            {pa.recommended_action && (
              <article className={`rounded-xl border p-5 shadow-sm ${ACTION_CONFIG[pa.recommended_action].bg}`}>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-500">操作建議</span>
                  <span className={`text-xl font-bold ${ACTION_CONFIG[pa.recommended_action].color}`}>
                    {ACTION_CONFIG[pa.recommended_action].label}
                  </span>
                </div>
                <div className="mt-2 space-y-1 text-xs text-slate-500">
                  <div className="flex justify-between">
                    <span>防守位</span>
                    <span className="font-mono font-medium text-orange-600">
                      {pa.trailing_stop ?? "—"}
                    </span>
                  </div>
                </div>
                {pa.trailing_stop_reason && (
                  <div className="mt-3">
                    <InsightText text={pa.trailing_stop_reason} />
                  </div>
                )}
              </article>
            )}
          </div>

          {/* Insight Cards */}
          <div className="space-y-3">
            {[
              { label: "技術面防守", content: result.analysis_detail?.tech_insight },
              { label: "主力動向", content: result.analysis_detail?.inst_insight },
              { label: "消息面風險", content: result.analysis_detail?.news_insight },
            ].map(({ label, content }) => (
              <article
                key={label}
                className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
              >
                <div className="mb-2 text-xs font-semibold text-slate-600">{label}</div>
                <InsightText text={content} />
              </article>
            ))}
          </div>

          {/* Final Verdict */}
          {result.analysis_detail?.final_verdict && (
            <article className="rounded-xl border border-indigo-100 bg-indigo-50 p-5 shadow-sm">
              <div className="mb-2 text-xs font-semibold text-indigo-700">綜合研判</div>
              <InsightText text={result.analysis_detail.final_verdict} />
            </article>
          )}

          {/* Disclaimer */}
          <p className="text-center text-xs text-slate-400">
            本診斷結果僅供參考，不構成投資建議。防守位每次查詢動態計算，非靜態設定。
          </p>
        </div>
      )}
    </div>
  );
}
