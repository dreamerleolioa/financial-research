# Analyze 頁「新倉策略建議」定位調整 Implementation Plan

> 狀態：待執行
> 預計執行日期：2026-03-17

**Goal:** 將個股即時分析頁底部策略區塊從模糊的「投資策略」重新定位為「新倉策略建議」，明確區分 `/analyze` 與 `/analyze/position` 的產品語義，降低使用者將其誤解為持股操作指令的風險。

**Scope:** 本次以前端 Analyze 頁與相關文案同步為主，不改動 Portfolio 頁內既有持股診斷邏輯；後端策略規則暫不重寫，只補語義對齊與必要文件同步。

**Non-Goals:**

- 不修改 `POST /analyze/position` 的 `recommended_action` / `exit_reason` 規則
- 不在本次導入新的策略評分引擎或重做 `strategy_generator.py`
- 不變更持股分析頁的卡片結構或命名

---

## 背景與決策

目前 Analyze 頁只有股票代碼，缺乏成本價、部位大小、持有期間與風險承受度，因此底部策略區塊若以「投資策略」或「當前操作」呈現，容易讓使用者誤解為持股中的即時買賣指令。

經需求討論後，正式決策如下：

1. `/analyze` 頁底部區塊改定位為「新倉策略建議」
2. `/analyze/position` 維持持股操作建議語義，繼續承接續抱 / 減碼 / 出場判斷
3. `action_plan` 仍屬 rule-based 新倉策略輸出，不視為 LLM 直接給出的買賣指令

---

## 影響範圍

**Files:**

- Modify: `frontend/src/pages/AnalyzePage.tsx`
- Optional Modify: `frontend/src/pages/PortfolioPage.tsx`（若有導向文案需同步）
- Verify Only: `frontend/src/pages/PortfolioPage.tsx`（持股診斷 modal / detail flow）
- Modify: `docs/ai-stock-sentinel-architecture-spec.md`
- Modify: `docs/backend-api-technical-spec.md`

---

## Task 1：調整 Analyze 頁策略卡文案

**Goal:** 讓使用者一眼知道這是新倉策略，而不是持股中的即時操作建議。

### Step 1: 修改卡片標題與輔助說明

在 `frontend/src/pages/AnalyzePage.tsx`：

1. 將底部區塊標題由「投資策略」改為「新倉策略建議」
2. 補一行說明文字，例如：
   - 「用於評估是否觀察、等待與分批建立新倉」
   - 或「本區塊不提供持股中的續抱 / 減碼 / 出場指令」

### Step 2: 檢查欄位名稱是否需同步微調

視版面決定是否同步調整下列標籤，使其更符合新倉語義：

1. `策略方向` → 保留或改為 `新倉方向`
2. `建議入場區間` → 保留
3. `防守底線（停損）` → 保留
4. `預期持股期間` → 保留

### 驗收標準

1. 使用者從頁面文案可清楚理解此區塊屬新倉評估
2. 不再出現容易被理解成持股操作的文案

---

## Task 2：確認 Portfolio 頁內持股診斷介面維持持股操作建議語義

**Goal:** 避免本次調整誤傷持股診斷介面。

### Step 1: 檢查 `frontend/src/pages/PortfolioPage.tsx` 的持股診斷流程

確認以下內容不變：

1. `recommended_action` 仍顯示為 `續抱 / 減碼 / 出場`
2. `exit_reason` 仍作為持股風險或出場警示
3. 卡片結構仍以持倉上下文為中心

### 驗收標準

1. Portfolio 頁內持股診斷介面不改成新倉語義
2. 持股操作建議語義保持清晰

---

## Task 3：同步文件與驗收敘述

**Goal:** 讓需求文件與 API 契約和產品行為一致，避免後續再次混淆。

### Step 1: 文件同步

確認下列描述已存在且一致：

1. `/analyze` = 新倉策略建議
2. `/analyze/position` = 持股操作建議
3. `action_plan` 為 rule-based 新倉策略，不是 LLM 直接產出的當前操作

### Step 2: 補驗收語句

可在 PR 描述或實作回報中使用：

1. Analyze 頁已明確標示為新倉策略建議
2. Portfolio 頁內持股診斷介面未受影響
3. 使用者不再容易把 `/analyze` 的策略卡解讀為持股中的出場訊號

---

## 建議執行順序

1. 先改 `AnalyzePage.tsx` 文案
2. 手動檢查 Analyze 頁與 Portfolio 頁內持股診斷介面的語義是否清楚
3. 最後回寫文件與進度追蹤

---

## 風險與後續

### 本次不處理的風險

目前新倉策略本身仍偏模板化；即使名稱修正後，若要提升可靠度，後續仍需重構 `strategy_generator.py`。

### 後續可延伸需求

1. 將 Analyze 頁策略卡改成「建議動作 / 主要理由 / 關鍵價位 / 失效條件」四段式
2. 將 `action_plan` 擴充為更明確的 conviction / triggers / invalidation 結構
3. 以歷史回測驗證新倉策略標籤的可用性
