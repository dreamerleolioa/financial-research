# Phase 9 前端復盤儀表板 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立前端復盤儀表板頁面，視覺化單股的信心分數時序趨勢與訊號轉向歷史，讓使用者能回顧過去 N 天的診斷演變。

**Architecture:** 後端新增 `GET /history/{symbol}` 端點，從 `stock_analysis_cache` 讀取指定股票的歷史診斷紀錄並回傳（不需要使用者有持倉，供即時分析視窗查看該股歷史趨勢）。`GET /portfolio/{id}/history` 則查 `daily_analysis_log`（含 user_id，供持倉列表追蹤持倉診斷變化，已在 Phase 7 實作）。前端新增 `/history` 路由頁面，以折線圖呈現信心分數時序，搭配 action_tag 色塊標記訊號轉向點，使用原生 SVG 繪圖（無需引入 chart library）。

**Tech Stack:** FastAPI（新端點）、SQLAlchemy AsyncSession、React 19、TypeScript、Tailwind CSS v4、原生 SVG 折線圖

**前置依賴:** Phase 7 完成（`daily_analysis_log` 有實際數據）、使用者系統完成（`get_current_user` Depends 可用）

---

## Task 1: 後端 `GET /history/{symbol}` 端點

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Create: `backend/tests/test_history_api.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/test_history_api.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from ai_stock_sentinel import api


def _make_mock_log(record_date: str, action_tag: str, confidence: float):
    log = MagicMock()
    log.record_date = record_date
    log.signal_confidence = confidence
    log.action_tag = action_tag
    log.prev_action_tag = None
    log.prev_confidence = None
    log.indicators = {"rsi_14": 65.0}
    log.final_verdict = "測試診斷結論"
    return log


def test_history_returns_list():
    """GET /history/{symbol} 應回傳 list 格式。
    fetch_symbol_history 查 stock_analysis_cache（mock 欄位與 DailyAnalysisLog 相同）。
    """
    mock_logs = [
        _make_mock_log("2026-03-01", "Hold", 61.5),
        _make_mock_log("2026-03-04", "Trim", 74.0),
    ]

    with patch("ai_stock_sentinel.api.fetch_symbol_history", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_logs
        client = TestClient(api.app)
        resp = client.get("/history/2330.TW?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["action_tag"] == "Hold"
        assert data[1]["signal_confidence"] == 74.0


def test_history_defaults_to_30_days():
    """未指定 days 參數時預設查詢 30 天。"""
    with patch("ai_stock_sentinel.api.fetch_symbol_history", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []
        client = TestClient(api.app)
        resp = client.get("/history/2330.TW")
        assert resp.status_code == 200
        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("days", 30) == 30
```

**Step 2: 新增 fetch_symbol_history 函式與端點**

在 `api.py` 新增：

```python
from sqlalchemy import select as sa_select

class HistoryEntry(BaseModel):
    record_date:       str
    signal_confidence: float | None
    action_tag:        str | None
    prev_action_tag:   str | None
    prev_confidence:   float | None
    indicators:        dict[str, Any] | None
    final_verdict:     str | None


async def fetch_symbol_history(
    symbol: str,
    db: AsyncSession,
    *,
    days: int = 30,
) -> list:
    """從 stock_analysis_cache 讀取指定股票的歷史診斷紀錄。

    查 stock_analysis_cache（不含 user_id，跨使用者共用），供即時分析視窗
    查看該股歷史趨勢，不需要使用者有持倉。
    若需查詢持倉診斷變化，請使用 GET /portfolio/{id}/history
    （查 daily_analysis_log，含 user_id，已在 Phase 7 實作）。
    """
    from datetime import date, timedelta
    from ai_stock_sentinel.db.models import StockAnalysisCache  # 查 stock_analysis_cache，非 daily_analysis_log
    since = date.today() - timedelta(days=days)
    result = await db.execute(
        sa_select(StockAnalysisCache)          # 查 stock_analysis_cache
        .where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date >= since,
        )
        .order_by(StockAnalysisCache.record_date)
    )
    return list(result.scalars().all())


@app.get("/history/{symbol}", response_model=list[HistoryEntry])
async def get_symbol_history(
    symbol: str,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[HistoryEntry]:
    logs = await fetch_symbol_history(symbol, db, days=days)
    return [
        HistoryEntry(
            record_date=       str(log.record_date),
            signal_confidence= float(log.signal_confidence) if log.signal_confidence else None,
            action_tag=        log.action_tag,
            prev_action_tag=   log.prev_action_tag,
            prev_confidence=   float(log.prev_confidence) if log.prev_confidence else None,
            indicators=        log.indicators,
            final_verdict=     log.final_verdict,
        )
        for log in logs
    ]
```

**Step 3: 執行測試**

```bash
cd backend && pytest tests/test_history_api.py -v
```

Expected: 2 tests PASSED

**Step 4: 執行完整測試套件確認無回歸**

```bash
cd backend && pytest -v
```

Expected: 全部 PASSED

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_history_api.py
git commit -m "feat: add GET /history/{symbol} endpoint for dashboard data"
```

---

## Task 2: 前端 SVG 折線圖元件

**Files:**
- Create: `frontend/src/components/ConfidenceChart.tsx`

**Step 1: 建立元件**

```tsx
// frontend/src/components/ConfidenceChart.tsx
interface DataPoint {
  date: string
  confidence: number | null
  actionTag: string | null
  prevActionTag: string | null
}

const ACTION_COLOR: Record<string, string> = {
  Hold: "#22c55e",   // green-500
  Trim: "#eab308",   // yellow-500
  Exit: "#ef4444",   // red-500
  Add:  "#3b82f6",   // blue-500
}

const ACTION_LABEL: Record<string, string> = {
  Hold: "續抱",
  Trim: "減碼",
  Exit: "出場",
  Add:  "加碼",
}

interface Props {
  data: DataPoint[]
  height?: number
}

export function ConfidenceChart({ data, height = 200 }: Props) {
  const validPoints = data.filter((d) => d.confidence !== null)
  if (validPoints.length === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">
        無歷史數據
      </div>
    )
  }

  const W = 600
  const H = height
  const PAD = { top: 16, right: 16, bottom: 32, left: 40 }
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom

  const minY = 0
  const maxY = 100
  const xStep = innerW / Math.max(validPoints.length - 1, 1)

  const toX = (i: number) => PAD.left + i * xStep
  const toY = (v: number) => PAD.top + innerH - ((v - minY) / (maxY - minY)) * innerH

  // 折線 path
  const linePath = validPoints
    .map((d, i) => `${i === 0 ? "M" : "L"} ${toX(i)} ${toY(d.confidence!)}`)
    .join(" ")

  // Y 軸格線
  const gridLines = [25, 50, 75, 100]

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 320 }}>
        {/* 格線 */}
        {gridLines.map((y) => (
          <g key={y}>
            <line
              x1={PAD.left} y1={toY(y)}
              x2={W - PAD.right} y2={toY(y)}
              stroke="#e2e8f0" strokeWidth={1}
            />
            <text x={PAD.left - 6} y={toY(y) + 4} textAnchor="end"
              fontSize={10} fill="#94a3b8">{y}</text>
          </g>
        ))}

        {/* 折線 */}
        <path d={linePath} fill="none" stroke="#6366f1" strokeWidth={2} />

        {/* 各點標記 */}
        {validPoints.map((d, i) => {
          const x = toX(i)
          const y = toY(d.confidence!)
          const color = ACTION_COLOR[d.actionTag ?? ""] ?? "#94a3b8"
          const isSignalChange = d.prevActionTag && d.prevActionTag !== d.actionTag

          return (
            <g key={i}>
              {/* 訊號轉向時顯示垂直虛線 */}
              {isSignalChange && (
                <line
                  x1={x} y1={PAD.top}
                  x2={x} y2={H - PAD.bottom}
                  stroke={color} strokeWidth={1} strokeDasharray="4 2" opacity={0.5}
                />
              )}
              {/* 資料點圓圈 */}
              <circle cx={x} cy={y} r={isSignalChange ? 6 : 4}
                fill={color} stroke="white" strokeWidth={1.5} />
              {/* 日期標籤（每隔 5 點顯示一個） */}
              {i % 5 === 0 && (
                <text x={x} y={H - PAD.bottom + 14} textAnchor="middle"
                  fontSize={9} fill="#94a3b8">
                  {d.date.slice(5)}  {/* MM-DD */}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      {/* 圖例 */}
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-600">
        {Object.entries(ACTION_LABEL).map(([tag, label]) => (
          <span key={tag} className="flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: ACTION_COLOR[tag] }} />
            {label}
          </span>
        ))}
        <span className="flex items-center gap-1 text-slate-400">
          <span className="inline-block h-px w-4 border-t border-dashed border-slate-400" />
          訊號轉向
        </span>
      </div>
    </div>
  )
}
```

**Step 2: 確認 TypeScript 編譯無錯誤**

```bash
cd frontend && pnpm build 2>&1 | tail -20
```

Expected: 無 TypeScript 錯誤

**Step 3: Commit**

```bash
git add frontend/src/components/ConfidenceChart.tsx
git commit -m "feat: add SVG ConfidenceChart component for dashboard"
```

---

## Task 3: 前端歷史查詢 API 函式

**Files:**
- Create: `frontend/src/lib/historyApi.ts`

**Step 1: 建立 API 函式**

```typescript
// frontend/src/lib/historyApi.ts

export interface HistoryEntry {
  record_date: string
  signal_confidence: number | null
  action_tag: string | null
  prev_action_tag: string | null
  prev_confidence: number | null
  indicators: Record<string, unknown> | null
  final_verdict: string | null
}

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

export async function fetchSymbolHistory(
  symbol: string,
  days: number = 30,
  token: string,
): Promise<HistoryEntry[]> {
  const resp = await fetch(
    `${API_BASE}/history/${encodeURIComponent(symbol)}?days=${days}`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    },
  )
  if (!resp.ok) throw new Error(`History fetch failed: ${resp.status}`)
  return resp.json()
}
```

**Step 2: 確認 TypeScript 編譯無錯誤**

```bash
cd frontend && pnpm build 2>&1 | tail -20
```

Expected: 無錯誤

**Step 3: Commit**

```bash
git add frontend/src/lib/historyApi.ts
git commit -m "feat: add fetchSymbolHistory API helper"
```

---

## Task 4: 前端復盤儀表板頁面

**Files:**
- Create: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/App.tsx`

**Step 1: 建立 DashboardPage.tsx**

```tsx
// frontend/src/pages/DashboardPage.tsx
import { useState } from "react"
import { ConfidenceChart } from "../components/ConfidenceChart"
import { fetchSymbolHistory, type HistoryEntry } from "../lib/historyApi"

const ACTION_BADGE: Record<string, { label: string; cls: string }> = {
  Hold: { label: "續抱", cls: "bg-green-100 text-green-800" },
  Trim: { label: "減碼", cls: "bg-yellow-100 text-yellow-800" },
  Exit: { label: "出場", cls: "bg-red-100 text-red-800" },
  Add:  { label: "加碼", cls: "bg-blue-100 text-blue-800" },
}

interface Props {
  token: string
}

export default function DashboardPage({ token }: Props) {
  const [symbol, setSymbol]     = useState("")
  const [days, setDays]         = useState(30)
  const [entries, setEntries]   = useState<HistoryEntry[]>([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)

  async function handleSearch() {
    if (!symbol.trim()) return
    setLoading(true)
    setError(null)
    try {
      const data = await fetchSymbolHistory(symbol.trim(), days, token)
      setEntries(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : "查詢失敗")
    } finally {
      setLoading(false)
    }
  }

  const chartData = entries.map((e) => ({
    date:          e.record_date,
    confidence:    e.signal_confidence,
    actionTag:     e.action_tag,
    prevActionTag: e.prev_action_tag,
  }))

  // 找出訊號轉向的紀錄
  const signalChanges = entries.filter(
    (e) => e.prev_action_tag && e.prev_action_tag !== e.action_tag,
  )

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4">
      <h1 className="text-xl font-semibold text-slate-800">復盤儀表板</h1>

      {/* 查詢列 */}
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="股票代碼，例：2330.TW"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded border border-slate-300 px-2 py-2 text-sm"
        >
          <option value={14}>14 天</option>
          <option value={30}>30 天</option>
          <option value={60}>60 天</option>
          <option value={90}>90 天</option>
        </select>
        <button
          onClick={handleSearch}
          disabled={loading}
          className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {loading ? "查詢中…" : "查詢"}
        </button>
      </div>

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      {/* 信心分數折線圖 */}
      {entries.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-medium text-slate-700">
            {symbol} 信心分數趨勢（近 {days} 天）
          </h2>
          <ConfidenceChart data={chartData} />
        </div>
      )}

      {/* 訊號轉向記錄 */}
      {signalChanges.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-medium text-slate-700">訊號轉向紀錄</h2>
          <div className="space-y-3">
            {signalChanges.map((e) => {
              const prev = ACTION_BADGE[e.prev_action_tag ?? ""]
              const curr = ACTION_BADGE[e.action_tag ?? ""]
              const delta = e.signal_confidence != null && e.prev_confidence != null
                ? (e.signal_confidence - e.prev_confidence).toFixed(1)
                : null
              return (
                <div key={e.record_date} className="rounded border border-slate-100 bg-slate-50 p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-slate-500">{e.record_date}</span>
                    {prev && (
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${prev.cls}`}>
                        {prev.label}
                      </span>
                    )}
                    <span className="text-slate-400">→</span>
                    {curr && (
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${curr.cls}`}>
                        {curr.label}
                      </span>
                    )}
                    {delta && (
                      <span className={`text-xs ${Number(delta) > 0 ? "text-red-600" : "text-green-600"}`}>
                        ({Number(delta) > 0 ? "+" : ""}{delta} 分)
                      </span>
                    )}
                  </div>
                  {e.final_verdict && (
                    <p className="mt-1.5 text-xs leading-relaxed text-slate-600 line-clamp-3">
                      {e.final_verdict}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 完整歷史列表 */}
      {entries.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <h2 className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700">
            診斷紀錄（共 {entries.length} 筆）
          </h2>
          <div className="divide-y divide-slate-100">
            {[...entries].reverse().map((e) => {
              const badge = ACTION_BADGE[e.action_tag ?? ""]
              return (
                <div key={e.record_date} className="flex items-center gap-3 px-4 py-3">
                  <span className="w-24 text-sm text-slate-500">{e.record_date}</span>
                  {badge ? (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
                      {badge.label}
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">—</span>
                  )}
                  <span className="text-sm text-slate-700">
                    信心 {e.signal_confidence ?? "—"}
                  </span>
                  {e.indicators?.rsi_14 != null && (
                    <span className="text-xs text-slate-400">
                      RSI {String(e.indicators.rsi_14)}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {!loading && entries.length === 0 && symbol && (
        <p className="text-center text-sm text-slate-400">此股票尚無歷史診斷紀錄</p>
      )}
    </div>
  )
}
```

**Step 2: 在 App.tsx 加入 Dashboard tab**

在 `frontend/src/App.tsx` 的 tab 區塊加入：

```tsx
// 在現有 tab 旁邊新增
import DashboardPage from "./pages/DashboardPage"

// tabs 陣列加入
{ id: "dashboard", label: "復盤儀表板" }

// tab 內容加入
{activeTab === "dashboard" && <DashboardPage token={token} />}
```

**Step 3: 確認 TypeScript 編譯無錯誤**

```bash
cd frontend && pnpm build 2>&1 | tail -30
```

Expected: `built in Xs` 無錯誤

**Step 4: 本地驗證**

```bash
# Terminal 1
cd backend && uvicorn ai_stock_sentinel.api:app --reload

# Terminal 2
cd frontend && pnpm dev
```

開啟 `http://localhost:5173`，切換到「復盤儀表板」tab，輸入有歷史數據的股票代碼，確認：
- 折線圖正確顯示信心分數趨勢
- 訊號轉向紀錄正確標記轉向點
- 歷史列表依日期逆序顯示

**Step 5: Commit**

```bash
git add frontend/src/pages/DashboardPage.tsx frontend/src/App.tsx
git commit -m "feat: add dashboard page with confidence trend chart and signal history"
```

---

## 完成檢查清單

- [ ] `GET /history/{symbol}` 端點測試通過
- [ ] 完整後端測試套件無回歸
- [ ] `ConfidenceChart` SVG 元件 TypeScript 無錯誤
- [ ] `DashboardPage` 正確顯示折線圖與訊號轉向紀錄
- [ ] 前端 `pnpm build` 成功
- [ ] 本地手動驗證折線圖、轉向標記、列表三個區塊

---

*文件版本：v1.1 | 建立日期：2026-03-11 | 更新日期：2026-03-12 | 對應需求：`docs/ai-stock-sentinel-automation-review-spec.md` Phase 9*
