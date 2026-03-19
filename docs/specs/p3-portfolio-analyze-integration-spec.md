# P3：新倉/持股體驗整合 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-18-p2-p3-implementations.md`
> 對應 Roadmap：§5.8
> 前置依賴：P1 策略卡升級完成（策略語義清楚、輸出可解釋）
>
> ❌ **否決（2026-03-19）：** 背景假設有誤。使用者的持股必定是從 Analyze 頁手動加入，代表已看過新倉分析，「從 Portfolio 無法跳到 Analyze」不是實際痛點。本 spec 不實作。

---

## 1. 背景問題

目前 `/analyze`（新倉視角）與 `/portfolio`（持股視角）是兩個獨立頁面，沒有任何連結：

1. **從 Portfolio 無法跳到 Analyze**：使用者在 `/portfolio` 看到某檔持股，想查看「若我現在沒有這支，我還會買嗎？」無路可走，必須手動切頁、重新輸入代號
2. **從 Analyze 無法知道是否已在追蹤**：使用者在 `/analyze` 查詢一支股票，目前只有「加入我的持股」按鈕，但沒有顯示「這支我已有倉位」的提示
3. **兩個視角的語意雖已切開，但 UX 上沒有橋接**：「新倉決策 → 買入 → 持股管理」這條用戶路徑在產品上是斷裂的

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | Portfolio 頁每筆持股新增「查看新倉分析」入口，點擊跳至 Analyze 頁並預帶代號 |
| G2 | Analyze 頁查詢結果若與持股列表有重疊，顯示「已持有」提示（含成本價與損益） |
| G3 | 兩個視角在同一標的上的語意明確分欄，不混用語意 |

---

## 3. 範圍

### 範圍內

- `PortfolioPage.tsx`：每筆持股卡片新增「新倉分析」連結按鈕
- `AnalyzePage.tsx`：查詢結果顯示「已持有」狀態（若該代號存在於 portfolio）
- `App.tsx` 或路由層：`/portfolio → /analyze?symbol=XXXX` 的導航實作
- 兩個視角的 UI 用明確標題或標籤區分

### 範圍外

- 同一頁面同時顯示新倉與持股兩種分析（複雜度過高，留後續）
- Portfolio 頁內嵌 Analyze 輸出（不做，保持頁面職責清晰）
- 跨頁面共用狀態管理重構（只做最小導航，不重構 state 架構）
- 後端 API 修改

---

## 4. 功能需求

### F1：Portfolio → Analyze 導航

| 編號 | 需求 |
|---|---|
| F1-1 | 每筆持股卡片新增「查看新倉分析」按鈕（次要樣式，不搶主要操作位） |
| F1-2 | 點擊後導航至 `/analyze`，且 Analyze 頁搜尋框預填該持股代號並自動觸發查詢 |
| F1-3 | 導航使用 react-router-dom 的 `useNavigate` + query param（`/analyze?symbol=2330`） |
| F1-4 | 若 `AnalyzePage` 已支援 `useSearchParams` 讀取 `symbol`，補齊此邏輯；若無，在本次新增 |

### F2：Analyze 頁「已持有」提示

| 編號 | 需求 |
|---|---|
| F2-1 | `AnalyzePage` 在查詢前先從後端取得目前使用者的 portfolio 清單 |
| F2-2 | 查詢結果出來後，若當前 `symbol` 存在於 portfolio，顯示「已持有」提示橫幅 |
| F2-3 | 「已持有」橫幅顯示：成本價、現價、損益（%），使用已有的 portfolio item 資料計算 |
| F2-4 | 橫幅含「查看持股診斷」連結，點擊導航至 `/portfolio`（不需自動觸發診斷，只是跳頁） |
| F2-5 | portfolio 清單取得失敗（API 錯誤）時，靜默略過，不顯示橫幅，不影響分析功能 |

### F3：語意標題區分

| 編號 | 需求 |
|---|---|
| F3-1 | `AnalyzePage` 主標題維持「新倉策略建議」（已有，確認存在） |
| F3-2 | `PortfolioPage` 的持倉診斷結果標題維持「持倉診斷」語意（確認不混用「新倉」字樣） |
| F3-3 | 不在同一頁面中混用「新倉」與「持股」語意於同一標的 |

---

## 5. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | Portfolio 清單 fetch 為 lightweight（只需 `id`、`symbol`、`entry_price`），不影響 Analyze 頁首次載入速度 |
| NF2 | `useSearchParams` 讀取 `symbol` 為 Analyze 頁的新入口，不破壞現有使用者手動輸入的流程 |
| NF3 | 「已持有」橫幅為純前端邏輯（從已有 API 資料計算），不新增後端端點 |
| NF4 | Portfolio → Analyze 導航不影響 Portfolio 頁的任何現有功能 |

---

## 6. 資料流說明

### F1：Portfolio → Analyze 導航

```
PortfolioPage
  → 使用者點擊「查看新倉分析」
    → useNavigate("/analyze?symbol=2330")
      → AnalyzePage 掛載
        → useSearchParams().get("symbol") = "2330"
          → setSymbol("2330"); triggerSearch()
```

### F2：已持有提示

```
AnalyzePage 掛載
  → fetch GET /portfolio（取得使用者持股清單）
    → 儲存為 portfolioItems state
      → 使用者查詢 symbol="2330"，取得分析結果
        → portfolioItems.find(item => item.symbol === "2330")
          → 找到 → 顯示「已持有」橫幅（含 entry_price、current_price、損益）
          → 找不到 → 不顯示橫幅
```

---

## 7. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | 在 Portfolio 頁點擊持股的「查看新倉分析」，跳至 Analyze 頁且搜尋框有代號 | 瀏覽器目視 |
| AC2 | Analyze 頁自動執行查詢（不需手動點搜尋） | 瀏覽器目視 |
| AC3 | 查詢一支已持有的股票，出現「已持有」橫幅並顯示成本價與損益 | 瀏覽器目視 |
| AC4 | 查詢一支未持有的股票，不顯示橫幅 | 瀏覽器目視 |
| AC5 | 「已持有」橫幅的「查看持股診斷」連結點擊後跳至 `/portfolio` | 瀏覽器目視 |
| AC6 | 直接在 Analyze 頁手動輸入代號查詢，功能與原本一致（不受 searchParams 影響） | 瀏覽器目視 |
| AC7 | Portfolio API 失敗時，Analyze 頁仍可正常查詢，不顯示橫幅、不報錯 | 暫時關閉後端測試 |

---

## 8. 依賴

| 依賴項目 | 說明 |
|---|---|
| P1 策略卡升級完成 | 語意已明確，可安全整合 |
| `GET /portfolio` 端點存在且回傳 `symbol`、`entry_price` | 現有端點已有，確認格式 |
| `AnalyzePage` 已有 portfolio 追蹤邏輯 | 現有 `portfolioSymbols` state，需擴充為含 `entry_price` 的完整 item |
| react-router-dom `useNavigate`、`useSearchParams` 已在專案中使用 | 確認 router 版本（v6） |

---

## 9. 開放問題

| 問題 | 狀態 |
|---|---|
| 損益計算是否使用 `current_price`（需 snapshot）還是快取的最近收盤價？ | 決定：使用 `AnalyzeResponse.snapshot.current_price`（查詢後才顯示），不額外 fetch |
| 「查看新倉分析」按鈕是否放在持股卡片的展開狀態才可見？ | 決定：放在持股卡片主要操作區（不需展開），與「執行持倉診斷」同層，用次要樣式區分 |
