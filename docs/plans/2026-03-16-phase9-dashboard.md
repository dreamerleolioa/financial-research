# Phase 9 前端復盤儀表板 Implementation Plan

> 狀態：待執行
> 預計執行日期：2026-03-16

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立前端復盤儀表板頁面，視覺化單股的信心分數時序趨勢與訊號轉向歷史，讓使用者能回顧過去 N 天的診斷演變。

**Architecture:** 後端新增 `GET /history/{symbol}` 端點，從 `stock_analysis_cache` 讀取指定股票的歷史診斷紀錄並回傳（不需要使用者有持倉，供即時分析視窗查看該股歷史趨勢）。依 review-spec v1.9，history payload 應額外暴露 `is_final`，讓前端區分盤中點與收盤定稿點，避免把未定稿資料當成復盤結論。另依最新 review 決策，n8n Workflow A 會在收盤後先更新 `stock_raw_data`，再重跑 final analysis 覆寫 `stock_analysis_cache` 與持倉使用者的 `daily_analysis_log`；因此 dashboard 的大多數日終歷史點應為 `is_final = true`，但前端仍需保留對盤中未定稿點的顯示能力。`GET /portfolio/{id}/history` 則查 `daily_analysis_log`（含 user_id，供持倉列表追蹤持倉診斷變化，已在 Phase 7 實作）。前端整合必須以目前既有的 `App.tsx` 為基礎：保留現有 `analyze` / `portfolio` 兩個 tab、既有登入頭部、盤中 banner 與加入持股 modal，另外新增第三個 `dashboard` tab 掛入 `DashboardPage`。資料請求沿用現有 `frontend/src/lib/auth.ts` 的 `authHeaders()`，不新增 token prop 傳遞鏈。

**Tech Stack:** FastAPI（新端點）、SQLAlchemy AsyncSession、React 19、TypeScript、Tailwind CSS v4、原生 SVG 折線圖

**前置依賴:** Phase 7 完成（`daily_analysis_log` 有實際數據）、使用者系統完成（`get_current_user` Depends 可用）、前端已完成 analyze / portfolio 雙 tab 與 `authHeaders()` API 呼叫模式、n8n Workflow A 收盤後定稿流程已上線或至少規格已凍結

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
  is_final:          bool
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
          is_final=          bool(log.is_final),
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
  date: string;
  confidence: number | null;
  actionTag: string | null;
  prevActionTag: string | null;
  isFinal: boolean;
}

const ACTION_COLOR: Record<string, string> = {
  Hold: "#22c55e", // green-500
  Trim: "#eab308", // yellow-500
  Exit: "#ef4444", // red-500
  Add: "#3b82f6", // blue-500
};

const ACTION_LABEL: Record<string, string> = {
  Hold: "續抱",
  Trim: "減碼",
  Exit: "出場",
  Add: "加碼",
};

interface Props {
  data: DataPoint[];
  height?: number;
}

export function ConfidenceChart({ data, height = 200 }: Props) {
  const validPoints = data.filter((d) => d.confidence !== null);
  if (validPoints.length === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">
        無歷史數據
      </div>
    );
  }

  const W = 600;
  const H = height;
  const PAD = { top: 16, right: 16, bottom: 32, left: 40 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const minY = 0;
  const maxY = 100;
  const xStep = innerW / Math.max(validPoints.length - 1, 1);

  const toX = (i: number) => PAD.left + i * xStep;
  const toY = (v: number) => PAD.top + innerH - ((v - minY) / (maxY - minY)) * innerH;

  // 折線 path
  const linePath = validPoints
    .map((d, i) => `${i === 0 ? "M" : "L"} ${toX(i)} ${toY(d.confidence!)}`)
    .join(" ");

  // Y 軸格線
  const gridLines = [25, 50, 75, 100];

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 320 }}>
        {/* 格線 */}
        {gridLines.map((y) => (
          <g key={y}>
            <line
              x1={PAD.left}
              y1={toY(y)}
              x2={W - PAD.right}
              y2={toY(y)}
              stroke="#e2e8f0"
              strokeWidth={1}
            />
            <text x={PAD.left - 6} y={toY(y) + 4} textAnchor="end" fontSize={10} fill="#94a3b8">
              {y}
            </text>
          </g>
        ))}

        {/* 折線 */}
        <path d={linePath} fill="none" stroke="#6366f1" strokeWidth={2} />

        {/* 各點標記 */}
        {validPoints.map((d, i) => {
          const x = toX(i);
          const y = toY(d.confidence!);
          const color = ACTION_COLOR[d.actionTag ?? ""] ?? "#94a3b8";
          const isSignalChange = d.prevActionTag && d.prevActionTag !== d.actionTag;
          const isIntraday = !d.isFinal;

          return (
            <g key={i}>
              {/* 訊號轉向時顯示垂直虛線 */}
              {isSignalChange && (
                <line
                  x1={x}
                  y1={PAD.top}
                  x2={x}
                  y2={H - PAD.bottom}
                  stroke={color}
                  strokeWidth={1}
                  strokeDasharray="4 2"
                  opacity={0.5}
                />
              )}
              {/* 資料點圓圈 */}
              <circle
                cx={x}
                cy={y}
                r={isSignalChange ? 6 : 4}
                fill={color}
                stroke="white"
                strokeWidth={1.5}
              />
              {isIntraday && (
                <circle
                  cx={x}
                  cy={y}
                  r={8}
                  fill="none"
                  stroke="#f59e0b"
                  strokeWidth={1.5}
                  strokeDasharray="3 2"
                />
              )}
              {/* 日期標籤（每隔 5 點顯示一個） */}
              {i % 5 === 0 && (
                <text x={x} y={H - PAD.bottom + 14} textAnchor="middle" fontSize={9} fill="#94a3b8">
                  {d.date.slice(5)} {/* MM-DD */}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* 圖例 */}
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-600">
        {Object.entries(ACTION_LABEL).map(([tag, label]) => (
          <span key={tag} className="flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: ACTION_COLOR[tag] }}
            />
            {label}
          </span>
        ))}
        <span className="flex items-center gap-1 text-slate-400">
          <span className="inline-block h-px w-4 border-t border-dashed border-slate-400" />
          訊號轉向
        </span>
        <span className="flex items-center gap-1 text-amber-600">
          <span className="inline-block h-2.5 w-2.5 rounded-full border border-dashed border-amber-500" />
          盤中未定稿
        </span>
      </div>
    </div>
  );
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

import { authHeaders } from "./auth";

export interface HistoryEntry {
  record_date: string;
  signal_confidence: number | null;
  action_tag: string | null;
  prev_action_tag: string | null;
  prev_confidence: number | null;
  is_final: boolean;
  indicators: Record<string, unknown> | null;
  final_verdict: string | null;
}

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export async function fetchSymbolHistory(
  symbol: string,
  days: number = 30,
): Promise<HistoryEntry[]> {
  const resp = await fetch(`${API_BASE}/history/${encodeURIComponent(symbol)}?days=${days}`, {
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error(`History fetch failed: ${resp.status}`);
  return resp.json();
}
```

**補充說明**

- 目前前端所有 API 請求都透過 `authHeaders()` 自動帶 JWT，Dashboard 不應另外引入 `token` prop。
- `historyApi.ts` 應與 `App.tsx`、`PortfolioPage.tsx` 維持同一個授權模式，降低介面耦合。

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
import { useState } from "react";
import { ConfidenceChart } from "../components/ConfidenceChart";
import { fetchSymbolHistory, type HistoryEntry } from "../lib/historyApi";

const ACTION_BADGE: Record<string, { label: string; cls: string }> = {
  Hold: { label: "續抱", cls: "bg-green-100 text-green-800" },
  Trim: { label: "減碼", cls: "bg-yellow-100 text-yellow-800" },
  Exit: { label: "出場", cls: "bg-red-100 text-red-800" },
  Add: { label: "加碼", cls: "bg-blue-100 text-blue-800" },
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
    isFinal: e.is_final,
  }));

  // 找出訊號轉向的紀錄
  const signalChanges = entries.filter(
    (e) => e.prev_action_tag && e.prev_action_tag !== e.action_tag,
  );

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

      {error && <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {/* 信心分數折線圖 */}
      {entries.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-medium text-slate-700">
            {symbol} 信心分數趨勢（近 {days} 天）
          </h2>
          {entries.some((e) => !e.is_final) && (
            <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              含盤中未定稿資料點；這類資料通常出現在盤中查詢或收盤後定稿工作流尚未完成前，虛線外圈標記僅供即時參考，不代表收盤定論。
            </p>
          )}
          <ConfidenceChart data={chartData} />
        </div>
      )}

      {/* 訊號轉向記錄 */}
      {signalChanges.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-medium text-slate-700">訊號轉向紀錄</h2>
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
                  className="rounded border border-slate-100 bg-slate-50 p-3"
                >
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
                      <span
                        className={`text-xs ${Number(delta) > 0 ? "text-red-600" : "text-green-600"}`}
                      >
                        ({Number(delta) > 0 ? "+" : ""}
                        {delta} 分)
                      </span>
                    )}
                  </div>
                  {e.final_verdict && (
                    <p className="mt-1.5 text-xs leading-relaxed text-slate-600 line-clamp-3">
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
        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <h2 className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700">
            診斷紀錄（共 {entries.length} 筆）
          </h2>
          <div className="divide-y divide-slate-100">
            {[...entries].reverse().map((e) => {
              const badge = ACTION_BADGE[e.action_tag ?? ""];
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
                  <span className="text-sm text-slate-700">信心 {e.signal_confidence ?? "—"}</span>
                  {e.indicators?.rsi_14 != null && (
                    <span className="text-xs text-slate-400">
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
        <p className="text-center text-sm text-slate-400">此股票尚無歷史診斷紀錄</p>
      )}
    </div>
  );
}
```

**Step 2: 在 App.tsx 以現有 tab 架構加入 Dashboard**

目前 `App.tsx` 已有：

- `useAuth()` 與使用者頭部區塊
- `activeTab: "analyze" | "portfolio"`
- `PortfolioPage` 與 `/analyze` 的既有 UI
- 加入持股 modal、盤中 banner、持倉狀態查詢

Phase 9 不應重寫這些結構，只做最小增量：

- import `DashboardPage`
- 將 `activeTab` 擴充為 `"analyze" | "portfolio" | "dashboard"`
- 在現有兩顆 tab button 後新增第三顆「復盤儀表板」
- 在主內容區新增 `activeTab === "dashboard"` 的渲染分支
- 保持既有 `analyze` / `portfolio` 分支邏輯不變

示意：

```tsx
import DashboardPage from "./pages/DashboardPage"

const [activeTab, setActiveTab] = useState<"analyze" | "portfolio" | "dashboard">("analyze")

<button
  onClick={() => setActiveTab("dashboard")}
  className={`rounded-lg px-4 py-2 text-sm font-medium transition ${activeTab === "dashboard"
      ? "bg-indigo-600 text-white"
      : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
    }`}
>
  復盤儀表板
</button>

{activeTab === "dashboard" && <DashboardPage />}
```

**Step 2 驗收重點**

- 不可破壞目前「個股分析」tab 的分析流程、盤中警示、加入持股 modal
- 不可破壞目前「我的持股」tab 的 PortfolioPage 清單與 history 展開邏輯
- Dashboard 新增後，三個 tab 之間切換應維持同一頁 SPA 體驗，不引入新 route 依賴

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
- 切回「個股分析」時，原本分析結果畫面與加入持股功能仍正常
- 切回「我的持股」時，持倉列表與歷史展開仍正常

**Step 5: Commit**

```bash
git add frontend/src/pages/DashboardPage.tsx frontend/src/App.tsx
git commit -m "feat: add dashboard page with confidence trend chart and signal history"
```

---

## 完成檢查清單

- [ ] `GET /history/{symbol}` 端點測試通過，且 payload 含 `is_final`
- [ ] 完整後端測試套件無回歸
- [ ] `ConfidenceChart` SVG 元件 TypeScript 無錯誤
- [ ] `DashboardPage` 正確顯示折線圖、訊號轉向紀錄，以及盤中未定稿提示
- [ ] 與 review-spec v1.9 對齊：dashboard 能正確處理收盤後已被 n8n 定稿覆寫的歷史點，且對少數 `is_final = false` 點仍保留警示顯示
- [ ] Dashboard API 呼叫沿用 `authHeaders()`，不新增 token prop 傳遞
- [ ] `App.tsx` 成功新增第三個 dashboard tab，且不影響既有 analyze / portfolio 流程
- [ ] `PUT /portfolio/{id}` 端點測試通過
- [ ] `DELETE /portfolio/{id}` 端點測試通過，確認 `daily_analysis_log` 同步刪除
- [ ] 前端編輯 Modal 儲存後顯示重新觸發分析提示
- [ ] 前端刪除確認彈窗顯示，確認後正確移除清單項目
- [ ] 前端 `pnpm build` 成功
- [ ] 本地手動驗證折線圖、轉向標記、列表三個區塊

---

## Task 5: 後端編輯持股端點

**Files:**

- Modify: `backend/src/ai_stock_sentinel/portfolio/router.py`
- Modify: `backend/tests/test_portfolio_api.py`（或新增測試）

**Step 1: 寫失敗測試**

```python
def test_update_portfolio_success():
    """PUT /portfolio/{id} 應更新持倉資料並回傳更新後的結果。"""
    ...

def test_update_portfolio_forbidden():
    """非持倉擁有者呼叫 PUT /portfolio/{id} 應回傳 403。"""
    ...
```

**Step 2: 實作端點**

```python
class UpdatePortfolioRequest(BaseModel):
    entry_price: float
    quantity: int
    entry_date: str
    notes: str | None = None

@router.put("/{portfolio_id}", response_model=PortfolioItemResponse)
def update_portfolio(
    portfolio_id: int,
    payload: UpdatePortfolioRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(UserPortfolio, portfolio_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="無權限")
    item.entry_price = payload.entry_price
    item.quantity = payload.quantity
    item.entry_date = payload.entry_date
    item.notes = payload.notes
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item
```

**Step 3: 執行測試並 Commit**

```bash
cd backend && pytest -v
git add backend/src/ai_stock_sentinel/portfolio/router.py
git commit -m "feat: add PUT /portfolio/{id} endpoint for editing portfolio items"
```

---

## Task 6: 後端刪除持股端點

**Files:**

- Modify: `backend/src/ai_stock_sentinel/portfolio/router.py`
- Modify: `backend/tests/test_portfolio_api.py`

**Step 1: 寫失敗測試**

```python
def test_delete_portfolio_success():
    """DELETE /portfolio/{id} 應同時刪除持倉與該使用者該股的 daily_analysis_log。"""
    ...

def test_delete_portfolio_forbidden():
    """非持倉擁有者呼叫 DELETE /portfolio/{id} 應回傳 403。"""
    ...
```

**Step 2: 實作端點**

```python
@router.delete("/{portfolio_id}", status_code=204)
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(UserPortfolio, portfolio_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="無權限")
    # 同一 transaction 內先刪 log，再刪持倉
    db.execute(
        text("DELETE FROM daily_analysis_log WHERE user_id = :uid AND symbol = :sym"),
        {"uid": current_user.id, "sym": item.symbol},
    )
    db.delete(item)
    db.commit()
```

**Step 3: 執行測試並 Commit**

```bash
cd backend && pytest -v
git add backend/src/ai_stock_sentinel/portfolio/router.py
git commit -m "feat: add DELETE /portfolio/{id} endpoint with cascade log deletion"
```

---

## Task 7: 前端編輯與刪除持股 UI

**Files:**

- Modify: `frontend/src/pages/PortfolioPage.tsx`

**Step 1: 新增編輯 Modal**

- 點擊「編輯」開啟 Modal，帶入目前 `entry_price`、`quantity`、`entry_date`、`notes`
- 儲存後呼叫 `PUT /portfolio/{id}`
- 成功後更新本地 `items` state，並顯示提示：「持倉資訊已更新。若成本價或日期有變更，建議重新觸發分析以確保數據正確。」

**Step 2: 新增刪除確認**

- 點擊「刪除」顯示 `window.confirm`（或自訂 confirm Modal）：
  「確定刪除 {symbol} 持股？此操作將同時移除所有歷史診斷紀錄，且無法復原。」
- 確認後呼叫 `DELETE /portfolio/{id}`
- 成功後從 `items` state 移除該筆

**Step 3: 確認 TypeScript 編譯並 Commit**

```bash
cd frontend && pnpm build 2>&1 | tail -20
git add frontend/src/pages/PortfolioPage.tsx
git commit -m "feat: add edit and delete portfolio UI in PortfolioPage"
```

---

_文件版本：v1.5 | 建立日期：2026-03-11 | 更新日期：2026-03-16 | 對應需求：`docs/ai-stock-sentinel-automation-review-spec.md` v1.9，已納入 n8n 收盤後定稿流程與 Task 5–7 編輯、刪除持股規格_
