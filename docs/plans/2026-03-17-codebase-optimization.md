# Codebase Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修復 code review 發現的 Critical / Important / Minor 問題，提升系統正確性、效能與可維護性。

**Architecture:** 依優先順序分為四批：(1) 靜默正確性 bug (2) 效能與安全 (3) Frontend UX (4) Minor cleanup。每批獨立可測試。

**Tech Stack:** Python 3.11+, FastAPI, LangGraph, React 18, TypeScript, PostgreSQL

---

## Batch 1：靜默正確性 Bug（最優先）

### Task 1：修復 `is_final` 未注入 GraphState — 盤中 conviction 降級失效

**背景：** `strategy_node` 呼叫 `state.get("is_final", True)`，但 `GraphState` TypedDict 沒有 `is_final` 欄位，永遠回傳 `True`，導致 `_determine_conviction_level` 的盤中上限保護（`high → medium`）從不執行。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/state.py:71-132`
- Modify: `backend/src/ai_stock_sentinel/api.py:548-585` (兩處 `initial_state`)
- Test: `backend/tests/test_strategy_generator.py`

**Step 1: 在 `GraphState` 新增 `is_final` 欄位**

在 `state.py` 的 `GraphState` class 最底部（`prev_context` 之後）加入：

```python
    # 分析時間戳記 — 由 API 層注入，strategy_node 讀取以套用盤中 guardrail
    is_final: bool
```

**Step 2: 在 `api.py` 的兩個 `initial_state` 注入 `is_final`**

在 `/analyze` endpoint（line ~548）的 `initial_state` dict 中，現有欄位最後加入：

```python
        "is_final": now_time >= MARKET_CLOSE,
```

在 `/analyze/position` endpoint（line ~798）的 `initial_state` dict 同樣加入：

```python
        "is_final": now_time >= MARKET_CLOSE,
```

**Step 3: 確認 `strategy_node` 讀法正確**

確認 `nodes.py:552` 目前是：
```python
is_final=state.get("is_final", True),
```
改為直接讀 TypedDict（移除 `.get` fallback）：
```python
is_final=state["is_final"],
```

**Step 4: 執行相關測試確認不壞**

```bash
cd backend
python -m pytest tests/test_strategy_generator.py -v
```
Expected: 全部 PASS

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/graph/state.py \
        backend/src/ai_stock_sentinel/api.py \
        backend/src/ai_stock_sentinel/graph/nodes.py
git commit -m "fix: inject is_final into GraphState so intraday conviction guardrail fires correctly"
```

---

### Task 2：修復 Dashboard 信心指數變化顏色邏輯相反

**背景：** `DashboardPage.tsx:129` — 信心分數上升顯示紅色、下降顯示綠色，邏輯相反。

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx:129`

**Step 1: 修正顏色邏輯**

找到 `DashboardPage.tsx` 約第 129 行：

```tsx
className={`text-xs ${Number(delta) > 0 ? "text-red-600 dark:text-red-400" : "text-green-600 dark:text-green-400"}`}
```

改為：

```tsx
className={`text-xs ${Number(delta) > 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}
```

**Step 2: 視覺確認**

在 Dashboard 頁面確認信心分數上升時顯示綠色（+X 分），下降時顯示紅色（-X 分）。

**Step 3: Commit**

```bash
git add frontend/src/pages/DashboardPage.tsx
git commit -m "fix: correct dashboard confidence delta color (positive=green, negative=red)"
```

---

## Batch 2：效能與安全

### Task 3：`get_graph()` 改為 module-level singleton，避免每次 request 重建

**背景：** `get_graph()` 是 FastAPI `Depends`，每個請求都重新執行 `build_graph_deps()`（初始化 LLM client 等）和 `build_graph()`（編譯 state machine），非常昂貴。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:377-379, 527-533, 775-781`

**Step 1: 將 graph 改為 module-level singleton**

找到 `api.py` 中 `get_graph()` 函式定義處（約 line 377），改為：

```python
# ─── Graph Singleton ─────────────────────────────────────────
def _build_graph_singleton():
    crawler, analyzer, rss_client, news_cleaner = build_graph_deps()
    return build_graph(crawler=crawler, analyzer=analyzer, rss_client=rss_client, news_cleaner=news_cleaner)

_graph = _build_graph_singleton()

def get_graph():
    return _graph
```

**Step 2: 確認兩個 endpoint 使用方式不變**

`Depends(get_graph)` 的使用方式不需改動，FastAPI 呼叫 `get_graph()` 時會直接回傳已建好的 singleton。

**Step 3: 測試 API 正常運作**

```bash
cd backend
python -m pytest tests/ -v -k "not slow"
```
Expected: 全部 PASS

若有整合測試可手動觸發一次 `/analyze` 確認回應正常。

**Step 4: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py
git commit -m "perf: build graph once at startup instead of per-request"
```

---

### Task 4：修復 Cache Schema Drift — `AnalyzeResponse(**full_result)` 加保護

**背景：** 快取的 `full_result` 是舊版 JSON，若 `AnalyzeResponse` schema 有更新，直接 `**full_result` unpacking 會拋 `ValidationError` 導致 HTTP 500。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:342-365`

**Step 1: 替換 `_build_response_from_cache` 中的 unpacking**

找到 `api.py` 中 `_build_response_from_cache` 函式（約 line 342），將：

```python
    if full_result:
        resp = AnalyzeResponse(**full_result)
        resp.is_final = hit.is_final
        resp.intraday_disclaimer = hit.intraday_disclaimer
        return resp
```

改為：

```python
    if full_result:
        try:
            resp = AnalyzeResponse.model_validate(full_result)
            resp.is_final = hit.is_final
            resp.intraday_disclaimer = hit.intraday_disclaimer
            return resp
        except Exception:
            # Schema drift — fallback to sparse fields from cache metadata
            pass
```

**Step 2: 確認 fallback path 還在**

確認函式後半的 fallback `return AnalyzeResponse(...)` 仍完整存在（用 `hit.final_verdict` 等精簡欄位組成回應）。

**Step 3: 執行測試**

```bash
cd backend
python -m pytest tests/ -v
```
Expected: 全部 PASS

**Step 4: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py
git commit -m "fix: guard against schema drift when deserializing cached full_result"
```

---

### Task 5：修復 `asyncio.get_event_loop()` — 改為 `get_running_loop()`

**背景：** Python 3.12 中 `asyncio.get_event_loop()` 在 running loop 內部行為不確定，應改用 `asyncio.get_running_loop()`。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py:67-74`

**Step 1: 替換 `_run` coroutine 內的呼叫**

找到 `nodes.py` 約 line 68，將：

```python
    async def _run() -> tuple[dict[str, Any], dict[str, Any]]:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            inst_future = loop.run_in_executor(pool, institutional_fetcher, symbol)
            fund_future = loop.run_in_executor(pool, fundamental_fetcher, symbol, current_price)
            return await asyncio.gather(inst_future, fund_future)
```

改為：

```python
    async def _run() -> tuple[dict[str, Any], dict[str, Any]]:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            inst_future = loop.run_in_executor(pool, institutional_fetcher, symbol)
            fund_future = loop.run_in_executor(pool, fundamental_fetcher, symbol, current_price)
            return await asyncio.gather(inst_future, fund_future)
```

**Step 2: 執行測試**

```bash
cd backend
python -m pytest tests/ -v
```
Expected: 全部 PASS

**Step 3: Commit**

```bash
git add backend/src/ai_stock_sentinel/graph/nodes.py
git commit -m "fix: use asyncio.get_running_loop() instead of deprecated get_event_loop()"
```

---

### Task 6：`INTERNAL_API_KEY` 空字串時 fail closed

**背景：** 若未設環境變數，`INTERNAL_API_KEY` 預設 `""`，傳入空 header 可能繞過保護。應在 startup 時 fail closed。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:100, 367-369`

**Step 1: 修改 `verify_internal_api_key`**

找到 `api.py` 約 line 367，將：

```python
def verify_internal_api_key(x_internal_api_key: str = Header(default=None)):
    if not INTERNAL_API_KEY or x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")
```

改為：

```python
def verify_internal_api_key(x_internal_api_key: str = Header(default=None)):
    if not INTERNAL_API_KEY:
        raise HTTPException(status_code=503, detail="Internal API key not configured")
    if x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")
```

**Step 2: 執行測試**

```bash
cd backend
python -m pytest tests/ -v
```
Expected: 全部 PASS

**Step 3: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py
git commit -m "fix: internal API key returns 503 when unconfigured instead of silently bypassing"
```

---

### Task 7：`PositionAnalyzeRequest.symbol` 補上 input validation

**背景：** `AnalyzeRequest.symbol` 有 `min_length=1`，但 `PositionAnalyzeRequest.symbol` 是裸 `str`，空字串或超長字串會直接打到外部 API。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:41-45`

**Step 1: 加上 Field validation**

找到 `api.py` 約 line 41-45：

```python
class PositionAnalyzeRequest(BaseModel):
    symbol: str
    entry_price: float
    entry_date: str | None = None
    quantity: int | None = None
```

改為：

```python
class PositionAnalyzeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    entry_price: float = Field(gt=0)
    entry_date: str | None = None
    quantity: int | None = None
```

注意：`Field` 已在 `api.py` 頂部 import，不需額外 import。

**Step 2: 執行測試**

```bash
cd backend
python -m pytest tests/ -v
```
Expected: 全部 PASS

**Step 3: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py
git commit -m "fix: add input validation to PositionAnalyzeRequest"
```

---

## Batch 3：Frontend UX

### Task 8：`PortfolioPage` 分析快取改為可重新觸發

**背景：** 目前 `analysisMap[item.id] !== undefined` 讓整個 session 內結果永不更新，使用者點「即時分析」卻看到舊資料。改為每次點擊都重新觸發分析。

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx:580-583`

**Step 1: 移除快取 early return**

找到 `openAnalysis` 函式（約 line 580），將：

```typescript
  async function openAnalysis(item: PortfolioItem) {
    setModalItem(item);
    // If already fetched, just open the modal
    if (analysisMap[item.id] !== undefined) return;
```

改為：

```typescript
  async function openAnalysis(item: PortfolioItem) {
    setModalItem(item);
```

**Step 2: 確認視覺行為**

每次點「即時分析」都應顯示「分析中，請稍候…」並重新 fetch，不再顯示舊資料。

**Step 3: Commit**

```bash
git add frontend/src/pages/PortfolioPage.tsx
git commit -m "fix: always re-fetch position analysis on open instead of showing stale cached result"
```

---

### Task 9：`AnalyzePage` 加 AbortController 避免 race condition

**背景：** 使用者快速切換股票代碼連續送出，舊的請求比新的晚回來時，畫面會顯示舊股票的結果。

**Files:**
- Modify: `frontend/src/pages/AnalyzePage.tsx:186-272`

**Step 1: 新增 `abortControllerRef`**

在 `AnalyzePage` function body 的 state 宣告區加入：

```typescript
  const abortControllerRef = useRef<AbortController | null>(null);
```

記得在頂部確認 `useRef` 已從 `react` import（目前已有）。

**Step 2: 修改 `handleAnalyze` 使用 AbortController**

找到 `handleAnalyze` 函式，改為：

```typescript
  async function handleAnalyze() {
    if (!symbol.trim()) return;

    // 取消上一個尚未完成的請求
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL}/analyze`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ symbol: symbol.trim() }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }
      const data: AnalyzeResponse = await res.json();
      setResult(data);
      await fetchPortfolio();
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return; // 使用者已送出新請求，忽略
      const message = err instanceof Error ? err.message : "無法連線後端，請確認伺服器已啟動。";
      setResult({
        snapshot: {}, analysis: "", analysis_detail: null, cleaned_news: null,
        cleaned_news_quality: null, news_display_items: [], confidence_score: null,
        cross_validation_note: null, strategy_type: null, entry_zone: null,
        stop_loss: null, holding_period: null, action_plan_tag: null, action_plan: null,
        institutional_flow_label: null, data_confidence: null,
        is_final: true, intraday_disclaimer: null,
        errors: [{ code: "NETWORK_ERROR", message }],
      });
    } finally {
      setLoading(false);
    }
  }
```

**Step 3: Commit**

```bash
git add frontend/src/pages/AnalyzePage.tsx
git commit -m "fix: abort in-flight analyze request when user submits a new one"
```

---

## Batch 4：Minor Cleanup

### Task 10：抽共用 utilities — `formatPrice`、`InsightText`

**背景：** `getTaiwanTickSize`、`decimalPlaces`、`formatPrice`、`InsightText` 在 `AnalyzePage.tsx` 和 `PortfolioPage.tsx` 各有一份，型別簽名也不一致。

**Files:**
- Create: `frontend/src/lib/formatters.ts`
- Create: `frontend/src/components/InsightText.tsx`
- Modify: `frontend/src/pages/AnalyzePage.tsx`
- Modify: `frontend/src/pages/PortfolioPage.tsx`

**Step 1: 建立 `frontend/src/lib/formatters.ts`**

```typescript
export function getTaiwanTickSize(price: number): number {
  if (price < 10) return 0.01;
  if (price < 50) return 0.05;
  if (price < 100) return 0.1;
  if (price < 500) return 0.5;
  if (price < 1000) return 1;
  return 5;
}

function decimalPlaces(step: number): number {
  const stepText = step.toString();
  const dotIndex = stepText.indexOf(".");
  return dotIndex === -1 ? 0 : stepText.length - dotIndex - 1;
}

export function formatPrice(value: number | null | undefined, symbol?: string): string {
  if (value == null || Number.isNaN(value)) return "—";
  const symbolText = (symbol ?? "").toUpperCase();
  const isTaiwanStock = symbolText.endsWith(".TW") || symbolText.endsWith(".TWO");
  if (isTaiwanStock && value > 0) {
    const tick = getTaiwanTickSize(value);
    const normalized = Math.round((value + Number.EPSILON) / tick) * tick;
    return normalized.toFixed(decimalPlaces(tick));
  }
  return new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: 6 }).format(value);
}

export function formatVolume(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("zh-TW").format(value);
}
```

**Step 2: 建立 `frontend/src/components/InsightText.tsx`**

```tsx
export function InsightText({ text }: { text: string | null | undefined }) {
  if (!text) return <p className="text-sm text-text-faint">請先執行分析。</p>;
  const sentences = text.split(/(?<=[。；！？：\n])/).map((s) => s.trim()).filter(Boolean);
  if (sentences.length <= 1)
    return <p className="text-sm leading-relaxed text-text-secondary">{text}</p>;
  return (
    <div className="space-y-1.5">
      {sentences.map((s, i) => (
        <p key={i} className="text-sm leading-relaxed text-text-secondary">{s}</p>
      ))}
    </div>
  );
}
```

注意：`PortfolioPage` 的 `InsightText` 在無資料時顯示 `"—"` 而非 `"請先執行分析。"`，可在 component 加一個 `emptyText` prop：

```tsx
export function InsightText({
  text,
  emptyText = "請先執行分析。",
}: {
  text: string | null | undefined;
  emptyText?: string;
}) {
  if (!text) return <p className="text-sm text-text-faint">{emptyText}</p>;
  // ... 其餘不變
}
```

**Step 3: 更新 `AnalyzePage.tsx`**

移除檔案頂部自有的 `getTaiwanTickSize`、`decimalPlaces`、`formatPrice`、`formatVolume`、`InsightText` 定義，改為 import：

```typescript
import { formatPrice, formatVolume } from "../lib/formatters";
import { InsightText } from "../components/InsightText";
```

**Step 4: 更新 `PortfolioPage.tsx`**

移除自有的 `getTaiwanTickSize`、`decimalPlaces`、`formatPrice` 定義，改為 import：

```typescript
import { formatPrice } from "../lib/formatters";
import { InsightText } from "../components/InsightText";
```

將 `PortfolioPage` 中所有 `<InsightText text={...} />` 加上 `emptyText="—"` prop。

**Step 5: 確認 TypeScript 無報錯**

```bash
cd frontend
npx tsc --noEmit
```
Expected: 無錯誤

**Step 6: Commit**

```bash
git add frontend/src/lib/formatters.ts \
        frontend/src/components/InsightText.tsx \
        frontend/src/pages/AnalyzePage.tsx \
        frontend/src/pages/PortfolioPage.tsx
git commit -m "refactor: extract formatPrice, formatVolume, InsightText to shared modules"
```

---

### Task 11：history table React key 改用 `record_date`

**背景：** `PortfolioPage.tsx` history table 用 array index 當 `key`，應改用 `record_date`。

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx` (history table rows)

**Step 1: 找到 history table rows**

找到 `PortfolioPage.tsx` 中 history table 的 `<tr key={idx}` 處（約 line 783），將：

```tsx
<tr key={idx} className="text-text-secondary">
```

改為：

```tsx
<tr key={row.record_date} className="text-text-secondary">
```

**Step 2: Commit**

```bash
git add frontend/src/pages/PortfolioPage.tsx
git commit -m "fix: use record_date as React key for history table rows"
```

---

### Task 12：`@app.on_event("startup")` 改為 lifespan

**背景：** FastAPI 0.93+ 已棄用 `@app.on_event`，應改用 `lifespan` context manager。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:382-396`

**Step 1: 替換 startup handler**

找到 `api.py` 約 line 382，將：

```python
app = FastAPI(title="AI Stock Sentinel API", version="v1")


@app.on_event("startup")
def run_migrations() -> None:
    import logging

    from alembic import command
    from alembic.config import Config

    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        logging.getLogger(__name__).error("Alembic migration failed: %s", exc, exc_info=True)
```

改為：

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    from alembic import command
    from alembic.config import Config
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        logging.getLogger(__name__).error("Alembic migration failed: %s", exc, exc_info=True)
    yield


app = FastAPI(title="AI Stock Sentinel API", version="v1", lifespan=lifespan)
```

注意：`asynccontextmanager` 要加在頂部 import 區（`from contextlib import asynccontextmanager`）。

**Step 2: 確認 server 啟動無 deprecation warning**

```bash
cd backend
uvicorn ai_stock_sentinel.api:app --reload
```

確認啟動 log 中沒有 `DeprecationWarning: on_event` 警告。

**Step 3: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py
git commit -m "chore: replace deprecated @app.on_event with lifespan context manager"
```

---

## 執行順序總覽

| Task | 分類 | 影響 | 預估難度 |
|------|------|------|----------|
| 1 | Critical bug | `is_final` guardrail 修復 | 低 |
| 2 | Critical bug | Dashboard 顏色修正 | 低 |
| 3 | Perf | Graph singleton | 低 |
| 4 | Safety | Cache schema drift 保護 | 低 |
| 5 | Safety | asyncio fix | 低 |
| 6 | Safety | Internal API key | 低 |
| 7 | Safety | Input validation | 低 |
| 8 | UX | Portfolio 快取策略 | 低 |
| 9 | UX | AbortController | 中 |
| 10 | Refactor | 共用 utilities | 中 |
| 11 | Minor | React key | 低 |
| 12 | Minor | lifespan migration | 低 |

---

## 未納入本計劃的項目（需較大架構調整，建議另開 sprint）

- **#5 `requires_fundamental_update` routing** — 需重新設計 builder.py graph 拓撲
- **#7 DB commit rollback** — 需引入 transaction scope，需仔細評估各 commit 的意圖
- **#8 N+1 fetch** — 需新增後端 bulk history endpoint
- **#12 外部資料重複 fetch in retry loop** — 需調整 graph 拓撲，將 `fetch_external_data` 移到 retry loop 外
- **#19 RSI 重複計算** — 優化幅度極小，不值得在此 sprint 調整
