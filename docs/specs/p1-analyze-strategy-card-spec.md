# P1：Analyze 頁策略卡升級 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-19-p1-implementations.md`
> 對應 Roadmap：§5.3

---

## 1. 背景問題

後端 `generate_action_plan()` 已輸出 `thesis_points`、`invalidation_conditions`、`conviction_level` 等欄位，但前端目前只渲染部分欄位：

- `conviction_level` 徽章：已顯示
- `thesis_points`：已顯示
- `invalidation_conditions`：已顯示
- `upgrade_triggers` / `downgrade_triggers`：**接收但未渲染**
- `suggested_position_size`：**接收但未渲染**

此外，策略卡的視覺結構缺乏清晰的段落分層，「事實性價位」（entry_zone、defense_line）與「推論性判斷」（thesis_points、invalidation_conditions）混在同一個區塊，可讀性不足。

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 策略卡採用四段式結構：建議動作、主要理由、關鍵價位、失效條件 |
| G2 | `suggested_position_size` 在前端正確顯示 |
| G3 | `upgrade_triggers` 與 `downgrade_triggers` 在前端渲染（可收合） |
| G4 | 「事實性價位」與「推論性判斷」視覺上有明確區分 |
| G5 | 盤中免責聲明保留且位置不影響主要資訊閱讀 |

---

## 3. 範圍

### 範圍內

- `AnalyzePage.tsx` 策略卡區塊重構（視覺結構調整）
- `suggested_position_size` 顯示
- `upgrade_triggers` / `downgrade_triggers` 顯示（可收合區塊）
- `conviction_level`、`thesis_points`、`invalidation_conditions` 確保渲染正確

### 範圍外

- 後端 `generate_action_plan()` 輸出欄位不變（現有欄位已足夠）
- 持股診斷頁（`/analyze/position`）不在本次範圍
- 策略卡資料來源邏輯（路由、快取）不修改
- 新增任何後端欄位

---

## 4. 功能需求

### F1：四段式策略卡結構

策略卡應分為以下四段，各段視覺上有分隔：

| 段落 | 內容 | 對應欄位 |
|---|---|---|
| 建議動作 | 行動建議文字 + conviction_level 徽章 | `action`、`conviction_level` |
| 主要理由 | thesis_points 列表（含數字或點） | `thesis_points` |
| 關鍵價位 | 進場區間、停損位、動能預期 | `target_zone`、`defense_line`、`momentum_expectation`、`suggested_position_size` |
| 失效條件 | invalidation_conditions 列表 | `invalidation_conditions` |

### F2：suggested_position_size 顯示

| 編號 | 需求 |
|---|---|
| F2-1 | `suggested_position_size` 有值時顯示於「關鍵價位」段落末端 |
| F2-2 | `suggested_position_size` 為 null 或空字串時不渲染該列 |

### F3：upgrade_triggers / downgrade_triggers 顯示

| 編號 | 需求 |
|---|---|
| F3-1 | `upgrade_triggers` 與 `downgrade_triggers` 放在可收合的「條件變化」區塊 |
| F3-2 | 預設收合，使用者點擊可展開 |
| F3-3 | 兩個陣列皆為空（或 null）時，不渲染該區塊 |
| F3-4 | 只有其中一個有值時，只顯示有值的部分 |

### F4：事實性 vs 推論性視覺區分

| 編號 | 需求 |
|---|---|
| F4-1 | 「關鍵價位」段落（target_zone、defense_line）使用帶底色或邊框的卡片呈現，與文字列表段落視覺上不同 |
| F4-2 | 「主要理由」與「失效條件」使用列表格式，前者用中性圖示，後者保留警示圖示（⚠ 或類似） |

---

## 5. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | `action_plan` 為 null 時，策略卡不渲染（現有行為不變） |
| NF2 | 收合/展開狀態為 local UI state，不持久化 |
| NF3 | 盤中免責聲明（`intraday_disclaimer`）位置移至策略卡底部，不遮蔽主要內容 |
| NF4 | 所有欄位缺值時優雅降級（不顯示該段落），不報 JS error |

---

## 6. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | 策略卡呈現四段式結構，各段有視覺分隔 | 瀏覽器 /analyze 頁目視確認 |
| AC2 | `suggested_position_size` 有值時顯示於「關鍵價位」段 | 瀏覽器目視 / mock data |
| AC3 | `upgrade_triggers` / `downgrade_triggers` 有值時可展開查看 | 瀏覽器目視 / mock data |
| AC4 | 兩組 triggers 皆空時，「條件變化」區塊不顯示 | 瀏覽器目視 |
| AC5 | `action_plan` 為 null 時，策略卡區塊不渲染，不報錯 | 瀏覽器 console |
| AC6 | 盤中免責聲明顯示在策略卡底部 | 瀏覽器目視 |

---

## 7. 依賴

| 依賴項目 | 說明 |
|---|---|
| `action_plan` 結構已含所有欄位 | 後端已輸出 `thesis_points`、`invalidation_conditions`、`suggested_position_size`、`upgrade_triggers`、`downgrade_triggers` |
| `AnalyzePage.tsx` 現有型別定義 | `AnalyzeResponse.action_plan` 型別需確認已包含 `upgrade_triggers`、`downgrade_triggers`、`suggested_position_size` |

---

## 8. 開放問題

| 問題 | 狀態 |
|---|---|
| `upgrade_triggers` / `downgrade_triggers` 預設展開還是收合？ | 決定：預設收合，降低初次閱讀認知負荷 |
| `suggested_position_size` 是否加說明 label（如「建議部位規模」）？ | 決定：加 label，與 target_zone / defense_line 格式一致 |
