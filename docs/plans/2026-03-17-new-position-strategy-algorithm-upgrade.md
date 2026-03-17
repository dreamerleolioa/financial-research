# 新倉策略建議演算法優化 Implementation Plan

> 狀態：待執行
> 預計執行日期：2026-03-17

**Goal:** 提升 `/analyze` 新倉策略建議的可靠度、區辨力與可解釋性。將目前偏模板化的 `generate_strategy()` / `generate_action_plan()` 升級為 evidence-based 的 rule-based 決策引擎，讓輸出不只告訴使用者「做什麼」，也能說清楚「為什麼」、「何時升級」、「何時失效」。

**Architecture:** 變更集中於 `backend/src/ai_stock_sentinel/analysis/strategy_generator.py` 與 `backend/src/ai_stock_sentinel/graph/nodes.py` 的 strategy 分支；保留「最終策略由 deterministic Python rule-based 產生」原則，不改為由 LLM 直接輸出進場指令。前端暫以現有 Analyze 頁策略卡承接新欄位，若欄位擴充完成後再追加 UI 結構升級。

**Tech Stack:** Python 3.12, pytest, existing GraphState / AnalyzeResponse schema

**Precondition:**

- `/analyze` 的產品語義已正式定義為「新倉策略建議」
- `/analyze/position` 持股操作建議語義已與 `/analyze` 切開

**Non-Goals:**

- 不修改 `POST /analyze/position` 的 `recommended_action` / `exit_reason` 規則
- 不把策略決策改為 LLM 直接輸出
- 不在本次導入正式回測平台或資料庫 schema 變更

---

## 背景問題

目前新倉策略建議存在以下結構性問題：

1. `generate_strategy()` 主要依少量 if/else 規則在 `short_term` / `mid_term` / `defensive_wait` 三者間切換，預設過度收斂到 `defensive_wait`
2. `confidence_score` 雖傳入 `generate_action_plan()`，但對行動強弱的影響有限
3. 輸出以模板文字為主，缺少「主要理由 / 失效條件 / 升降級觸發」
4. 使用者看到的是策略結果，但系統沒有充分暴露證據結構，導致體感上不可靠

---

## 目標設計原則

1. **Deterministic first**：最終策略仍由 Python rule-based 產出
2. **Evidence-based**：策略結論應由多個明確特徵加權組合，而非單一條件直接決定
3. **Safe downgrade**：低信心、盤中、資料不足、訊號衝突時必須主動降級建議積極度
4. **Explainable output**：輸出必須能回答「為什麼這樣建議」與「什麼條件下失效」

---

## 影響範圍

**Files:**

- Modify: `backend/src/ai_stock_sentinel/analysis/strategy_generator.py`
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Modify: `backend/src/ai_stock_sentinel/api.py`（若回應欄位需擴充）
- Modify: `backend/tests/test_strategy_generator.py`
- Modify: `backend/tests/test_graph_nodes.py`
- Optional Modify: `frontend/src/pages/AnalyzePage.tsx`（若策略輸出欄位擴充後要先接）
- Modify: `docs/backend-api-technical-spec.md`
- Modify: `docs/ai-stock-sentinel-architecture-spec.md`

---

## Task 1：重構策略判斷核心為 Evidence-Based Scoring

**Goal:** 用多個證據分數取代目前過度簡化的三段式條件判斷，提高區辨力與穩定性。

### 現況

目前 `generate_strategy()` 主要依：

1. `bias > 10` → `defensive_wait`
2. `positive + distribution` → `defensive_wait`
3. `positive + rsi < 30` → `short_term`
4. `institutional_accumulation + close > ma5 > ma20` → `mid_term`

這會讓大量樣本落到預設觀望，且對中間態缺乏分層。

### 新設計

新增內部 evidence score，至少拆成四組：

1. `technical_evidence`
2. `flow_evidence`
3. `sentiment_evidence`
4. `risk_penalty`

#### 建議評分方向

1. `technical_evidence`
   - `close > ma5 > ma20` → +2
   - `rsi` 在健康動能區（例如 45–65）→ +1
   - `rsi < 30` 且非明顯弱勢結構 → 反彈候選 +1
   - `bias > 10` → -2
2. `flow_evidence`
   - `institutional_accumulation` → +2
   - `distribution` → -2
   - `neutral` → 0
3. `sentiment_evidence`
   - `positive` → +1
   - `negative` → -1
4. `risk_penalty`
   - 訊號衝突（例如 positive + distribution）→ -2
   - 價格過熱 / 過度乖離 → 額外扣分

### 輸出映射建議

1. 總分高且風險低 → `mid_term`
2. 總分中等但有短線反彈機會 → `short_term`
3. 總分低、訊號衝突或資料不足 → `defensive_wait`

### 驗收標準

1. `generate_strategy()` 不再只靠單一條件決策
2. 同類型但不同證據強度的案例可分出差異
3. 預設觀望比例下降，但不犧牲保守性

---

## Task 2：擴充 `action_plan` 為可解釋輸出

**Goal:** 讓策略結果更像決策卡，而不是模板片語。

### 建議新增欄位

在現有 `{ action, target_zone, defense_line, momentum_expectation, breakeven_note }` 基礎上，評估新增：

1. `conviction_level`: `low` / `medium` / `high`
2. `thesis_points`: `string[]`，列出 2–4 個主要支持理由
3. `upgrade_triggers`: `string[]`，什麼情況可升級策略積極度
4. `downgrade_triggers`: `string[]`，什麼情況需轉保守
5. `invalidation_conditions`: `string[]`，哪個條件出現就代表原判斷失效
6. `suggested_position_size`: 例如 `0%` / `10-20%` / `20-30%`

### 輸出示例方向

```json
{
  "action": "分批佈局（首筆 10-20%）",
  "conviction_level": "medium",
  "thesis_points": ["法人籌碼偏多", "均線仍維持多頭排列", "新聞情緒偏正向"],
  "upgrade_triggers": ["突破近 20 日壓力", "量能同步放大"],
  "downgrade_triggers": ["跌破 MA20", "法人轉賣超"],
  "invalidation_conditions": ["跌破近 20 日支撐", "RSI 快速轉弱且價格失守 MA20"],
  "suggested_position_size": "10-20%"
}
```

### 驗收標準

1. action_plan 可直接支撐前端顯示「理由 / 觸發條件 / 失效條件」
2. 不再只剩單行模板文字

---

## Task 3：加入安全降級規則

**Goal:** 當訊號不完整或環境不適合積極進場時，系統自動保守化。

### 建議 guardrails

1. `confidence_score < 60` → `conviction_level` 不得高於 `low`
2. `data_confidence < 60` → `suggested_position_size` 不得超過 `10-20%`
3. `is_final = false`（盤中）→ 最積極只能到 `medium`，禁止給過大首筆部位
4. 訊號衝突明顯時（如 positive + distribution）→ 強制 `defensive_wait` 或 `low conviction`
5. `bias` 過高且 `rsi` 偏熱 → 不得給追價型建議

### 驗收標準

1. 有明確的程式化安全欄杆
2. 低信心時不再產生過度積極的新倉建議

---

## Task 4：補齊測試案例

**Goal:** 讓策略優化不是感覺好，而是有穩定測試保護。

### 測試方向

1. 不同 evidence 組合對應不同 `strategy_type`
2. `conviction_level` 隨 `confidence_score` / `data_confidence` 正確降級
3. guardrails 生效時，`suggested_position_size` 與 `action` 不超界
4. `thesis_points` / `invalidation_conditions` 在關鍵案例中不為空
5. 舊有 `calculate_action_plan_tag()` 行為不回歸

### 可能檔案

1. `backend/tests/test_strategy_generator.py`
2. `backend/tests/test_graph_nodes.py`

---

## Task 5：文件與前端承接

**Goal:** 讓擴充後的策略結構能被文件與 UI 正確消化。

### Step 1: 文件同步

同步更新：

1. `docs/backend-api-technical-spec.md`
2. `docs/ai-stock-sentinel-architecture-spec.md`

### Step 2: 前端承接策略欄位

若本輪欄位已穩定，Analyze 頁可先做最小接法：

1. 顯示 `conviction_level`
2. 顯示 2–3 條 `thesis_points`
3. 顯示主要 `invalidation_conditions`

完整 UI 升級可另開下一張 plan。

---

## 建議執行順序

1. 先重構 `generate_strategy()` 的 evidence scoring
2. 再擴充 `generate_action_plan()` 的輸出結構
3. 補 guardrails
4. 補測試
5. 最後同步文件，視情況接前端最小顯示

---

## 驗收定義

1. `/analyze` 的新倉策略不再高度模板化
2. 使用者可從輸出看懂主要依據與失效條件
3. 低信心 / 盤中 / 訊號衝突案例下，系統會自動降級積極度
4. 測試覆蓋關鍵策略分支與安全欄杆

---

## 後續延伸

1. 新增歷史回測計劃，驗證新倉策略標籤與後續報酬/勝率的關聯
2. 將前端策略卡升級為「建議動作 / 理由 / 觸發條件 / 失效條件」四段式
3. 若未來需要，另開一份計劃處理策略校準與權重調優
