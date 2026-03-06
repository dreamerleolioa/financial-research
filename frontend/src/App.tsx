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

interface CleanedNewsQuality {
  quality_score: number
  quality_flags: string[]
}

interface NewsDisplay {
  title: string
  date: string | null
  source_url: string | null
}

interface NewsDisplayItem {
  title: string
  date: string | null
  source_url: string | null
}

interface AnalyzeResponse {
  snapshot: Record<string, unknown>
  analysis: string
  analysis_detail: AnalysisDetail | null
  cleaned_news: Record<string, unknown> | null
  cleaned_news_quality: CleanedNewsQuality | null
  news_display: NewsDisplay | null
  news_display_items: NewsDisplayItem[]
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

const SENTIMENT_LABEL: Record<string, string> = {
  positive: '偏正向',
  neutral: '中性',
  negative: '偏負向',
}

const SENTIMENT_CLASS: Record<string, string> = {
  positive: 'bg-emerald-100 text-emerald-800',
  neutral: 'bg-slate-100 text-slate-700',
  negative: 'bg-rose-100 text-rose-800',
}

const ANALYSIS_PATH = [
  '接收查詢：輸入股票代碼',
  '抓取股價快照完成',
  '抓取新聞與資料清潔完成',
  '提取關鍵數字與情緒標籤完成',
  '輸出分析結果',
]

function formatVolume(value: unknown): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—'
  }
  return new Intl.NumberFormat('zh-TW').format(value)
}

function getTaiwanTickSize(price: number): number {
  if (price < 10) return 0.01
  if (price < 50) return 0.05
  if (price < 100) return 0.1
  if (price < 500) return 0.5
  if (price < 1000) return 1
  return 5
}

function decimalPlaces(step: number): number {
  const stepText = step.toString()
  const dotIndex = stepText.indexOf('.')
  return dotIndex === -1 ? 0 : stepText.length - dotIndex - 1
}

function formatPrice(value: unknown, symbol?: unknown): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—'
  }

  const symbolText = typeof symbol === 'string' ? symbol.toUpperCase() : ''
  const isTaiwanStock = symbolText.endsWith('.TW') || symbolText.endsWith('.TWO')

  if (isTaiwanStock && value > 0) {
    const tick = getTaiwanTickSize(value)
    const normalized = Math.round((value + Number.EPSILON) / tick) * tick
    const digits = decimalPlaces(tick)
    return normalized.toFixed(digits)
  }

  return new Intl.NumberFormat('zh-TW', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 6,
  }).format(value)
}

function mapVolumeSource(value: unknown): string {
  if (value === 'realtime') return '即時成交量'
  if (value === 'history_fallback') return '歷史資料回填'
  if (value === 'unavailable') return '暫無資料'
  return '未知來源'
}

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
        cleaned_news_quality: null,
        news_display: null,
        news_display_items: [],
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
          <h1 className="text-2xl font-semibold md:text-3xl">個股分析儀表板</h1>
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
                  <dd className="font-medium">{formatPrice(snapshot.current_price, snapshot.symbol)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">成交量</dt>
                  <dd className="font-medium">{formatVolume(snapshot.volume)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">成交量來源</dt>
                  <dd className="font-medium">{mapVolumeSource(snapshot.volume_source)}</dd>
                </div>
              </dl>
            ) : (
              <p className="mt-3 text-sm text-slate-400">請先執行分析。</p>
            )}
          </article>

          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-800">近期新聞</h2>
              {result?.cleaned_news?.sentiment_label && (
                <span
                  className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    SENTIMENT_CLASS[String(result.cleaned_news.sentiment_label)] ?? SENTIMENT_CLASS.neutral
                  }`}
                >
                  {SENTIMENT_LABEL[String(result.cleaned_news.sentiment_label)] ?? '中性'}
                </span>
              )}
            </div>

            {result?.cleaned_news_quality != null &&
              (result.cleaned_news_quality.quality_score < 60 ||
                result.cleaned_news_quality.quality_flags.length > 0) && (
                <p className="mt-2 rounded-md bg-slate-100 px-3 py-1.5 text-xs text-slate-500">
                  摘要品質受限
                </p>
              )}

            {result ? (
              result.news_display_items.length > 0 ? (
                <ul className="mt-3 divide-y divide-slate-100">
                  {result.news_display_items.map((item, idx) => (
                    <li key={idx} className="py-2.5">
                      {item.source_url ? (
                        <a
                          href={item.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="block text-sm text-slate-800 hover:text-indigo-600 hover:underline"
                        >
                          {item.title}
                        </a>
                      ) : (
                        <p className="text-sm text-slate-800">{item.title}</p>
                      )}
                      {item.date && (
                        <p className="mt-0.5 text-xs text-slate-400">{item.date}</p>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-sm text-slate-400">本次無新聞資料。</p>
              )
            ) : (
              <p className="mt-3 text-sm text-slate-400">請先執行分析。</p>
            )}

            <p className="mt-3 text-xs text-slate-400">
              以上為市場情緒參考新聞。財報數字請參閱
              <a
                href="https://mops.twse.com.tw"
                target="_blank"
                rel="noopener noreferrer"
                className="ml-0.5 text-indigo-500 hover:underline"
              >
                公開資訊觀測站
              </a>。
            </p>
          </article>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
          <h2 className="mb-3 text-sm font-semibold text-slate-800">分析報告</h2>
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
              <p className="text-sm text-slate-400">本次無分析結果。</p>
            )
          ) : (
            <p className="text-sm text-slate-400">請先執行分析。</p>
          )}
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
          <h2 className="mb-4 text-sm font-semibold text-slate-800">投資策略</h2>
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
