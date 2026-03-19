# P1：盤中 vs 收盤策略分流 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-19-p1-implementations.md`
> 對應 Roadmap：§5.4

---

## 1. 背景問題

目前系統雖然有 `is_final` 標誌，且 `_determine_conviction_level()` 已有盤中降級邏輯（`is_final=False` 時 `high → medium`），但這個保護不完整：

1. **建議首筆部位上限未限制**：盤中時 `suggested_position_size` 仍可輸出「積極」部位規模（如「全倉 30%」）
2. **盤中 `conviction_level` 上限只降一級**：`medium` 本身仍代表有一定可信度，對使用者的誤導程度可能被低估
3. **`is_final` 標誌未真正納入策略 guardrails**：目前只在 `_determine_conviction_level` 做了一處降級，`action` 文字與 `momentum_expectation` 的積極度不受 `is_final` 影響
4. **免責聲明是唯一的安全網**：若使用者忽略免責聲明，盤中訊號與收盤訊號在策略卡上幾乎無法區分

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 盤中時 `conviction_level` 上限為 `medium`（現有，確認保留） |
| G2 | 盤中時 `suggested_position_size` 限制為保守規模，不輸出積極建議 |
| G3 | 盤中時策略卡有明確的視覺標示（標籤或邊框），區別於收盤版 |
| G4 | `is_final=False` 的條件貫穿整個 `generate_action_plan()` 的 guardrails |

---

## 3. 範圍

### 範圍內

- `strategy_generator.py`：`generate_action_plan()` 新增盤中 guardrail（`suggested_position_size` 限制）
- `strategy_generator.py`：確認 `_determine_conviction_level()` 的 `is_final` 邏輯正確
- `AnalyzePage.tsx`：盤中策略卡視覺標示（標籤）

### 範圍外

- 盤中版與收盤版分開快取或分開路由（複雜度太高，P2+）
- `action` 文字內容根據 `is_final` 改寫（LLM 文字邏輯，留 P2）
- 盤中模式限制查詢頻率

---

## 4. 功能需求

### F1：盤中 conviction_level 上限

| 編號 | 需求 |
|---|---|
| F1-1 | `is_final=False` 時，`conviction_level` 最高為 `medium`（現有邏輯，驗證保留） |
| F1-2 | `is_final=False` 且原始計算為 `high` 時，降至 `medium` |
| F1-3 | `is_final=False` 且原始計算為 `low` 時，保持 `low`（不往上調） |

### F2：盤中 suggested_position_size 限制

| 編號 | 需求 |
|---|---|
| F2-1 | `generate_action_plan()` 傳入 `is_final=False` 時，`suggested_position_size` 固定輸出保守描述（如「盤中觀察，暫不建議建立部位」或「小試水溫（5%以下）」） |
| F2-2 | `is_final=False` 時，不輸出「全倉 XX%」或「積極建倉」等積極文字 |
| F2-3 | `is_final=True` 時，`suggested_position_size` 輸出邏輯不變 |

### F3：前端盤中視覺標示

| 編號 | 需求 |
|---|---|
| F3-1 | `is_final=False` 時，策略卡標題區顯示「盤中版」標籤（pill 樣式，amber/yellow 色系） |
| F3-2 | 「盤中版」標籤與現有 `conviction_level` 徽章並列顯示 |
| F3-3 | `is_final=True` 時，不顯示「盤中版」標籤 |
| F3-4 | 現有盤中免責聲明（`intraday_disclaimer`）保留，不移除 |

---

## 5. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | `is_final=True` 的所有輸出邏輯不受本次修改影響 |
| NF2 | 後端修改不影響 API response 的 schema 結構（不新增欄位） |
| NF3 | 前端標籤為純視覺層，不影響任何邏輯或路由 |

---

## 6. 資料欄位說明

### `is_final` 流向

```
api.py: is_final = (now_time >= MARKET_CLOSE)  # 13:30 台北時間
  → GraphState.is_final
    → strategy_node 傳給 generate_action_plan(is_final=is_final)
      → _determine_conviction_level(is_final=is_final)  [現有]
      → suggested_position_size 邏輯 (is_final=is_final)  [新增]
        → AnalyzeResponse.is_final
          → AnalyzePage.tsx 前端標示
```

### `suggested_position_size` 盤中輸出規則

| 情況 | 輸出文字 |
|---|---|
| `is_final=False`，任何策略類型 | `"盤中觀察，建議等待收盤確認後再評估部位"` |
| `is_final=True`，defensive_wait | `"建議暫不建立新倉"` |
| `is_final=True`，low conviction | `"小試水溫（5% 以下）"` |
| `is_final=True`，medium conviction | `"輕倉試探（10-15%）"` |
| `is_final=True`，high conviction | `"標準部位（20-30%）"` |

---

## 7. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | 呼叫 `generate_action_plan(is_final=False, conviction_level_base="high")` → `conviction_level` 輸出 `"medium"` | 單元測試 |
| AC2 | 呼叫 `generate_action_plan(is_final=False, ...)` → `suggested_position_size` 包含「盤中」或「收盤確認」字樣 | 單元測試 |
| AC3 | 呼叫 `generate_action_plan(is_final=True, ...)` → `suggested_position_size` 輸出與修改前一致 | 單元測試 |
| AC4 | 前端 `is_final=false` 時策略卡顯示「盤中版」amber 標籤 | 瀏覽器目視 / mock data |
| AC5 | 前端 `is_final=true` 時不顯示「盤中版」標籤 | 瀏覽器目視 |

---

## 8. 依賴

| 依賴項目 | 說明 |
|---|---|
| `is_final` 已正確傳入 `generate_action_plan()` | 現有 `strategy_node` 已傳入，確認不需修改傳參 |
| `AnalyzePage.tsx` 已接收 `is_final` | 現有型別定義已含 `is_final: boolean` |

---

## 9. 開放問題

| 問題 | 狀態 |
|---|---|
| 盤中時 `conviction_level` 是否應降至 `low`（而非 `medium`）？ | 決定：維持 `medium` 上限，`low` 保留給真正低分情況，`medium` 已足以傳達不確定性 |
| `suggested_position_size` 盤中文字是否要完全移除（返回 null）？ | 決定：保留文字說明，讓使用者理解原因，而非直接消失 |
