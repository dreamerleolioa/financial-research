import { useMemo, useState } from 'react'

function App() {
  const [symbol, setSymbol] = useState('2330.TW')

  const confidenceScore = 72
  const circumference = 2 * Math.PI * 52
  const dashOffset = useMemo(
    () => circumference * (1 - confidenceScore / 100),
    [circumference, confidenceScore],
  )

  const rawNews =
    '2026-03-03 台積電法說會後市場情緒升溫，外資報告稱股價有望起飛，Q1 營收 5,000 億元，EPS 12.3，股價上漲 3.5%。'

  const cleanSummary = {
    date: '2026-03-03',
    title: '台積電法說會後市場反應',
    mentioned_numbers: ['5,000', '12.3', '3.5%'],
    sentiment_label: 'positive',
  }

  const analysisPath = [
    '接收查詢：2330.TW',
    '抓取股價快照完成',
    '抓取新聞與資料清潔完成',
    '提取關鍵數字與情緒標籤完成',
    '輸出分析結果',
  ]

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 md:px-6">
        <header className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold md:text-3xl">AI Stock Sentinel Dashboard</h1>
          <p className="text-sm text-slate-600">輸入股票代碼，查看 AI 分析信心、雜訊過濾結果與流程路徑。</p>
        </header>

        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
          <label htmlFor="symbol" className="mb-2 block text-sm font-medium text-slate-700">
            股票代碼
          </label>
          <div className="flex flex-col gap-3 md:flex-row">
            <input
              id="symbol"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-indigo-200 transition focus:ring-2 md:max-w-sm"
              placeholder="例如 2330.TW"
            />
            <button className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700">
              開始分析
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
                />
              </svg>
              <div className="absolute text-center">
                <div className="text-2xl font-semibold">{confidenceScore}%</div>
                <div className="text-xs text-slate-500">Confidence</div>
              </div>
            </div>
          </article>

          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6 lg:col-span-2">
            <h2 className="text-sm font-semibold text-slate-800">分析路徑圖</h2>
            <ol className="mt-4 space-y-3">
              {analysisPath.map((step, index) => (
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
            <h2 className="text-sm font-semibold text-slate-800">原始新聞（雜訊）</h2>
            <p className="mt-3 text-sm leading-relaxed text-slate-700">{rawNews}</p>
          </article>

          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
            <h2 className="text-sm font-semibold text-slate-800">AI 萃取純數據摘要</h2>
            <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
              {JSON.stringify(cleanSummary, null, 2)}
            </pre>
          </article>
        </section>
      </div>
    </main>
  )
}

export default App
