import { useMemo, useState } from 'react'

interface ErrorDetail {
  code: string
  message: string
}

interface AnalysisDetail {
  summary: string
  risks: string[]
  technical_signal: 'bullish' | 'bearish' | 'sideways'
}

interface AnalyzeResponse {
  snapshot: Record<string, unknown>
  analysis: string
  analysis_detail: AnalysisDetail | null
  cleaned_news: Record<string, unknown> | null
  confidence_score: number | null
  cross_validation_note: string | null
  strategy_type: 'short_term' | 'mid_term' | 'defensive_wait' | null
  entry_zone: string | null
  stop_loss: string | null
  holding_period: string | null
  errors: ErrorDetail[]
}

const STRATEGY_LABEL: Record<string, string> = {
  short_term: '短線操作',
  mid_term: '中線佈局',
  defensive_wait: '防守觀望',
}

const SIGNAL_LABEL: Record<string, string> = {
  bullish: '看多',
  bearish: '看空',
  sideways: '盤整',
}

const SIGNAL_CLASS: Record<string, string> = {
  bullish: 'bg-emerald-100 text-emerald-800',
  bearish: 'bg-red-100 text-red-800',
  sideways: 'bg-slate-100 text-slate-700',
}

const ANALYSIS_PATH = [
  '接收查詢：輸入股票代碼',
  '抓取股價快照完成',
  '抓取新聞與資料清潔完成',
  '提取關鍵數字與情緒標籤完成',
  '輸出分析結果',
]

function App() {
  const [symbol, setSymbol] = useState('2330.TW')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AnalyzeResponse | null>(null)

  const confidenceScore = result?.confidence_score ?? null
  const circumference = 2 * Math.PI * 52
  const dashOffset = useMemo(
    () => (confidenceScore != null ? circumference * (1 - confidenceScore / 100) : circumference),
    [circumference, confidenceScore],
  )

  async function handleAnalyze() {
    if (!symbol.trim()) return
    setLoading(true)
    try {
      const res = await fetch('http://localhost:8000/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol.trim() }),
      })
      const data: AnalyzeResponse = await res.json()
      setResult(data)
    } catch {
      setResult({
        snapshot: {},
        analysis: '',
        analysis_detail: null,
        cleaned_news: null,
        confidence_score: null,
        cross_validation_note: null,
        strategy_type: null,
        entry_zone: null,
        stop_loss: null,
        holding_period: null,
        errors: [{ code: 'NETWORK_ERROR', message: '無法連線後端，請確認伺服器已啟動。' }],
      })
    } finally {
      setLoading(false)
    }
  }

  const firstError = result?.errors?.[0]
  const snapshot = result?.snapshot ?? {}

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 md:px-6">
        <header className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold md:text-3xl">AI Stock Sentinel Dashboard</h1>
          <p className="text-sm text-slate-600">輸入股票代碼，查看 AI 分析信心、雜訊過濾結果與流程路徑。</p>
        </header>

        {firstError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <span className="font-semibold">[{firstError.code}]</span> {firstError.message}
          </div>
        )}

        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
          <label htmlFor="symbol" className="mb-2 block text-sm font-medium text-slate-700">
            股票代碼
          </label>
          <div className="flex flex-col gap-3 md:flex-row">
            <input
              id="symbol"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !loading && handleAnalyze()}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-indigo-200 transition focus:ring-2 md:max-w-sm"
              placeholder="例如 2330.TW"
              disabled={loading}
            />
            <button
              onClick={handleAnalyze}
              disabled={loading}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? '分析中...' : '開始分析'}
            </button>
          </div>
          <p className="mt-2 text-xs text-slate-500">目前查詢代碼：{symbol || '未輸入'}</p>
        </section>

        <section className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
            <h2 className="text-sm font-semibold text-slate-800">信心指數</h2>
            <div className="relative mt-4 flex items-center justify-center">
              <svg width="140" height="140" viewBox="0 0 140 140" className="-rotate-90">
                <circle cx="70" cy="70" r="52" strokeWidth="12" className="fill-none stroke-slate-200" />
                <circle
                  cx="70"
                  cy="70"
                  r="52"
                  strokeWidth="12"
                  strokeLinecap="round"
                  className="fill-none stroke-indigo-600"
                  strokeDasharray={circumference}
                  strokeDashoffset={dashOffset}
                  style={{ transition: 'stroke-dashoffset 0.5s ease' }}
                />
              </svg>
              <div className="absolute text-center">
                <div className="text-2xl font-semibold">
                  {confidenceScore != null ? `${confidenceScore}%` : '—'}
                </div>
                <div className="text-xs text-slate-500">Confidence</div>
              </div>
            </div>
            {result?.cross_validation_note && (
              <p className="mt-3 text-xs text-slate-500 text-center">{result.cross_validation_note}</p>
            )}
          </article>

          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6 lg:col-span-2">
            <h2 className="text-sm font-semibold text-slate-800">分析路徑圖</h2>
            <ol className="mt-4 space-y-3">
              {ANALYSIS_PATH.map((step, index) => (
                <li key={step} className="flex items-start gap-3">
                  <span className="mt-0.5 inline-flex h-6 w-6 items-center justify-center rounded-full bg-indigo-100 text-xs font-semibold text-indigo-700">
                    {index + 1}
                  </span>
                  <p className="text-sm text-slate-700">{step}</p>
                </li>
              ))}
            </ol>
          </article>
        </section>

        <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
            <h2 className="text-sm font-semibold text-slate-800">快照資訊</h2>
            {result ? (
              <dl className="mt-3 space-y-2 text-sm text-slate-700">
                <div className="flex justify-between">
                  <dt className="text-slate-500">代碼</dt>
                  <dd className="font-medium">{String(snapshot.symbol ?? '—')}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">現價</dt>
                  <dd className="font-medium">
                    {snapshot.current_price != null ? `${snapshot.current_price}` : '—'}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">成交量</dt>
                  <dd className="font-medium">
                    {snapshot.volume != null ? `${snapshot.volume}` : '—'}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="mt-3 text-sm text-slate-400">請先執行分析。</p>
            )}
          </article>

          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
            <h2 className="text-sm font-semibold text-slate-800">AI 萃取純數據摘要</h2>
            {result ? (
              result.cleaned_news ? (
                <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
                  {JSON.stringify(result.cleaned_news, null, 2)}
                </pre>
              ) : (
                <p className="mt-3 text-sm text-slate-400">本次無新聞資料可萃取。</p>
              )
            ) : (
              <p className="mt-3 text-sm text-slate-400">請先執行分析。</p>
            )}
          </article>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
          <h2 className="mb-3 text-sm font-semibold text-slate-800">LLM 分析報告</h2>
          {result ? (
            result.analysis_detail ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${SIGNAL_CLASS[result.analysis_detail.technical_signal] ?? SIGNAL_CLASS.sideways}`}
                  >
                    {SIGNAL_LABEL[result.analysis_detail.technical_signal] ?? '盤整'}
                  </span>
                </div>
                <p className="text-sm text-slate-700 leading-relaxed">{result.analysis_detail.summary}</p>
                {result.analysis_detail.risks.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-slate-500 mb-1">風險提示</p>
                    <ul className="list-disc list-inside space-y-1">
                      {result.analysis_detail.risks.map((risk, i) => (
                        <li key={i} className="text-sm text-slate-700">{risk}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : result.analysis ? (
              <pre className="whitespace-pre-wrap wrap-break-word text-sm text-slate-700 leading-relaxed">{result.analysis}</pre>
            ) : (
              <p className="text-sm text-slate-400">本次無 LLM 分析結果。</p>
            )
          ) : (
            <p className="text-sm text-slate-400">請先執行分析。</p>
          )}
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
          <h2 className="mb-4 text-sm font-semibold text-slate-800">戰術行動（Action Plan）</h2>
          {result ? (
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-lg bg-slate-50 p-3">
                <dt className="text-xs text-slate-500">策略方向</dt>
                <dd className="mt-1 text-sm font-medium text-slate-800">
                  {result.strategy_type ? (STRATEGY_LABEL[result.strategy_type] ?? result.strategy_type) : '—'}
                </dd>
              </div>
              <div className="rounded-lg bg-slate-50 p-3">
                <dt className="text-xs text-slate-500">建議入場區間</dt>
                <dd className="mt-1 text-sm font-medium text-slate-800">{result.entry_zone ?? '—'}</dd>
              </div>
              <div className="rounded-lg bg-slate-50 p-3">
                <dt className="text-xs text-slate-500">防守底線（停損）</dt>
                <dd className="mt-1 text-sm font-medium text-slate-800">{result.stop_loss ?? '—'}</dd>
              </div>
              <div className="rounded-lg bg-slate-50 p-3">
                <dt className="text-xs text-slate-500">預期持股期間</dt>
                <dd className="mt-1 text-sm font-medium text-slate-800">{result.holding_period ?? '—'}</dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-slate-400">請先執行分析。</p>
          )}
        </section>
      </div>
    </main>
  )
}

export default App
