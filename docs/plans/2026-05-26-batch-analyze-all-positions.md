# Batch Analyze All Positions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在持倉頁頂部加一個「全部分析」按鈕，以 concurrency=2 依序分析所有持股，並以 banner 顯示即時進度，失敗自動重試一次。

**Architecture:** 純前端實作，不需要新的後端 API。複用現有 `openAnalysis` 的 fetch 邏輯，抽出一個 `runPositionAnalysis(item)` 函式供單次呼叫和批次呼叫共用。批次執行以 controlled concurrency queue（最多同時 2 個請求）實作，banner 狀態機為 `idle → running → done/partialError → (3s) → idle`。

**Tech Stack:** React, TypeScript, existing `fetch` + `authHeaders()`

---

### Task 1: 抽出 `runPositionAnalysis` 共用函式

目前 `openAnalysis` 把「發 API 請求 + 更新 state + 開 Modal」混在一起。需要把「發 API 請求 + 更新 state」拆成獨立函式，讓批次呼叫也能使用。

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx:532-575`

**Step 1: 在 `openAnalysis` 上方新增 `runPositionAnalysis` 函式**

這個函式只負責打 API + 更新 `analysisMap` / `analysisLoading` / `analysisError` / `latestMap` / `historyMap`，不打開 Modal，失敗時 throw error（讓呼叫方決定是否重試）。

```typescript
async function runPositionAnalysis(item: PortfolioItem): Promise<void> {
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
    const msg = err instanceof Error ? err.message : "請求失敗";
    setAnalysisError((prev) => ({ ...prev, [item.id]: msg }));
    throw err; // re-throw so batch runner knows it failed
  } finally {
    setAnalysisLoading((prev) => ({ ...prev, [item.id]: false }));
  }
}
```

**Step 2: 改寫 `openAnalysis` 使用 `runPositionAnalysis`**

```typescript
async function openAnalysis(item: PortfolioItem) {
  setModalItem(item);
  await runPositionAnalysis(item);
}
```

**Step 3: 驗證現有功能不受影響**

手動點單一持股的「即時分析」按鈕，確認 Modal 正常開啟、結果正常顯示。

---

### Task 2: 加入批次分析 state

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx:466-482`（state 宣告區）

**Step 1: 新增 batch banner 相關 state**

在現有 modal state 宣告區（約第 475 行）後方加入：

```typescript
// Batch analysis state
type BatchStatus = "idle" | "running" | "done" | "partialError";
const [batchStatus, setBatchStatus] = useState<BatchStatus>("idle");
const [batchProgress, setBatchProgress] = useState({ done: 0, total: 0 });
const [batchFailedSymbols, setBatchFailedSymbols] = useState<string[]>([]);
```

---

### Task 3: 實作 `runBatchAnalysis` 函式

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx`（在 `openAnalysis` 下方新增）

**Step 1: 新增 controlled concurrency 批次函式**

```typescript
async function runBatchAnalysis() {
  if (batchStatus === "running" || items.length === 0) return;

  setBatchStatus("running");
  setBatchProgress({ done: 0, total: items.length });
  setBatchFailedSymbols([]);

  const CONCURRENCY = 2;
  let index = 0;
  let done = 0;
  const failed: string[] = [];

  async function runOne(item: PortfolioItem) {
    try {
      await runPositionAnalysis(item);
    } catch {
      // retry once
      try {
        await runPositionAnalysis(item);
      } catch {
        failed.push(item.symbol);
      }
    }
    done += 1;
    setBatchProgress({ done, total: items.length });
  }

  async function worker() {
    while (index < items.length) {
      const item = items[index];
      index += 1;
      await runOne(item);
    }
  }

  const workers = Array.from({ length: Math.min(CONCURRENCY, items.length) }, () => worker());
  await Promise.all(workers);

  setBatchFailedSymbols(failed);
  setBatchStatus(failed.length > 0 ? "partialError" : "done");

  setTimeout(() => {
    setBatchStatus("idle");
    setBatchProgress({ done: 0, total: 0 });
    setBatchFailedSymbols([]);
  }, 3000);
}
```

**Step 2: 確認 TypeScript 無型別錯誤**

```bash
cd /Users/leo/Documents/work/financial-research/frontend
npx tsc --noEmit
```

Expected: 無錯誤輸出

---

### Task 4: 加入 banner UI 元件

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx`（return JSX 區段）

**Step 1: 在持股列表 `<div className="space-y-4">` 前加入 banner**

找到約第 611 行的 `<div className="space-y-4">` 區塊，在它**上方**插入 banner（banner 在 `<>` fragment 的最頂部）：

```tsx
{batchStatus !== "idle" && (
  <div className={`rounded-xl border px-4 py-3 text-sm ${
    batchStatus === "running"
      ? "border-indigo-200 bg-indigo-50 dark:border-indigo-800 dark:bg-indigo-950"
      : batchStatus === "done"
        ? "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950"
        : "border-yellow-200 bg-yellow-50 dark:border-yellow-800 dark:bg-yellow-950"
  }`}>
    <div className="flex items-center justify-between gap-3">
      <span className={`font-medium ${
        batchStatus === "running" ? "text-indigo-700 dark:text-indigo-300"
        : batchStatus === "done" ? "text-green-700 dark:text-green-300"
        : "text-yellow-700 dark:text-yellow-300"
      }`}>
        {batchStatus === "running" && `分析中 ${batchProgress.done}/${batchProgress.total}…`}
        {batchStatus === "done" && `✓ 已更新 ${batchProgress.total} 筆分析結果`}
        {batchStatus === "partialError" && `完成 ${batchProgress.total - batchFailedSymbols.length}/${batchProgress.total}，失敗：${batchFailedSymbols.join("、")}`}
      </span>
      {batchStatus === "running" && (
        <div className="h-1.5 w-32 overflow-hidden rounded-full bg-indigo-100 dark:bg-indigo-900">
          <div
            className="h-full rounded-full bg-indigo-500 transition-all duration-300"
            style={{ width: `${batchProgress.total > 0 ? (batchProgress.done / batchProgress.total) * 100 : 0}%` }}
          />
        </div>
      )}
    </div>
  </div>
)}
```

**Step 2: 在 `<h2>我的持股</h2>` 旁邊加「全部分析」按鈕**

找到約第 612–615 行的 header row：

```tsx
<div className="flex items-center justify-between">
  <h2 className="text-sm font-semibold text-text-primary">我的持股</h2>
  <span className="text-xs text-text-faint">共 {items.length} 筆</span>
</div>
```

改成：

```tsx
<div className="flex items-center justify-between">
  <h2 className="text-sm font-semibold text-text-primary">我的持股</h2>
  <div className="flex items-center gap-3">
    <button
      onClick={runBatchAnalysis}
      disabled={batchStatus === "running"}
      className="rounded-lg bg-indigo-500 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {batchStatus === "running" ? "分析中…" : "全部分析"}
    </button>
    <span className="text-xs text-text-faint">共 {items.length} 筆</span>
  </div>
</div>
```

**Step 3: TypeScript 檢查**

```bash
cd /Users/leo/Documents/work/financial-research/frontend
npx tsc --noEmit
```

Expected: 無錯誤

---

### Task 5: 手動測試與收尾

**Step 1: 啟動 dev server**

```bash
cd /Users/leo/Documents/work/financial-research/frontend
npm run dev
```

**Step 2: 測試情境**

1. 有多筆持股時按「全部分析」→ banner 出現、進度數字遞增、progress bar 更新
2. 全部完成後 banner 變綠色「✓ 已更新 N 筆」→ 3 秒後消失
3. 分析中再按「全部分析」按鈕 → disabled，不能重複觸發
4. 各 card 的「即時分析」按鈕仍正常運作（單一 modal flow 不受影響）

**Step 3: Commit**

```bash
git add frontend/src/pages/PortfolioPage.tsx
git commit -m "feat: 新增一鍵全部分析功能（concurrency=2，失敗自動重試）"
```
