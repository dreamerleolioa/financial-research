# 計劃：Deep Analysis Upgrade（技術語義化 + 策略具體化）

> 日期：2026-03-05
> 目標：解決「技術名詞難懂」與「策略建議不夠具體」兩大痛點
> 原則：完成即補測試（Code Complete ≠ Task Complete；需附對應測試與驗收證據）
> 執行前提：Claude 額度於 18:00 重置後啟動 Session 1

---

## 明日執行重點

1. 先穩定籌碼資料來源（避免後續分析建立在不穩定資料上）
2. 再做技術指標語義化（讓 LLM 與前端皆讀得懂）
3. 再升級策略模板（確保輸出具體價格與時間窗）
4. 最後做前端友善呈現（紅綠燈標籤）

---

## Session 1：籌碼 Provider 落地

### 範圍
- 實作 `FinMindProvider` 與 `TwseOpenApiProvider`
- 建立 provider router（Primary → Fallback）
- 確保 `2330.TW` 可穩定抓取三大法人與融資融券欄位

### DoD（完成定義）
- `fetch_institutional_flow("2330.TW", days=5)` 可穩定回傳核心欄位（僅作連通性 smoke test；正式分析視窗至少 `days>=20`，建議 `days=60`）
- 當 `FinMindProvider` 失敗時，自動切到 `TwseOpenApiProvider`
- 回傳資料已完成 schema mapping（不同來源欄位統一命名）
- 失敗情境回傳可追蹤錯誤碼（`INSTITUTIONAL_FETCH_ERROR`），不中斷主流程

### 預計測試案例
- `test_fetch_institutional_flow_returns_required_fields_for_2330_tw`
- `test_provider_router_fallbacks_to_twse_when_finmind_fails`
- `test_provider_schema_mapping_normalizes_finmind_and_twse_fields`
- `test_institutional_flow_returns_traceable_error_code_on_total_failure`

---

## Session 2：技術指標語義化（Task P3-2）

### 範圍
- 在 `preprocess_node` 實作 `quantify_to_narrative`
- 將 RSI / BIAS / MA / Institutional Flow 轉成中文可讀敘事
- 將敘事結果傳遞給分析 Agent（`analyze_node`）

### DoD（完成定義）
- `preprocess_node` 輸出包含 `technical_context` 與 `institutional_context`
- 縮寫術語都有固定映射（例如：RSI→買賣氣場、BIAS→股價位階）
- 敘事產出為純 Python rule-based，無 LLM 依賴，可重現
- `analyze_node` 已改讀語義化內容，而非僅裸數值

### 預計測試案例
- `test_quantify_to_narrative_maps_rsi_bias_ma_to_plain_chinese`
- `test_quantify_to_narrative_handles_rsi_overbought_and_oversold`
- `test_preprocess_node_emits_technical_and_institutional_context`
- `test_analyze_node_receives_semantic_context_fields`

---

## Session 3：策略模板升級（具體價位）

### 範圍
- 修改 `analyze_node` Prompt，強制填寫具體 `Entry_Zone` 與 `Exit_Point/Stop_Loss`
- 由 Python 計算支撐/壓力位、20 日低點與 MA60，再提供給 LLM 套模板
- 策略欄位必填：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
- 補上邊界 fallback：若 `low_20d`/`ma60` 計算失敗（新股資料不足或資料斷層），改回傳降級策略而非中斷

### DoD（完成定義）
- `entry_zone` 為具體數值區間（非「逢低布局」等抽象語句）
- `stop_loss` 為具體價位，且包含計算依據（例：近 20 日低點 -3%）
- `holding_period` 為可執行時間窗（例：7-10 交易日）
- 缺少價格計算值時，系統明確回傳風險提示，不允許虛構數值
- fallback 模式下，`entry_zone` 回傳「資料不足，建議參考現價 +/- 5%」，並在 `risks` 或 `cross_validation_note` 標註「20日位階資料不足」

### 預計測試案例
- `test_strategy_template_contains_numeric_entry_zone`
- `test_strategy_template_contains_numeric_stop_loss_with_basis`
- `test_strategy_template_contains_concrete_holding_period_window`
- `test_strategy_generation_fails_safe_when_price_levels_missing`
- `test_strategy_fallback_uses_close_plus_minus_5pct_when_low20d_unavailable`

---

## Session 4：前端 UI 友善化（Action Plan 紅綠燈）

### 範圍
- 在 Action Plan 卡片加入標籤：`🔵 中性 / 🔴 過熱 / 🟢 機會`
- 依 `technical_signal` + `confidence_score`（與必要時 `institutional_flow`）映射顯示
- 保持既有版面，不重構整體 UI
- 以固定規則實作燈號映射：
  - 🟢 機會：`rsi < 30` + `institutional_flow=institutional_accumulation` + `confidence_score > 70`
  - 🔴 過熱/風險：`rsi > 70` + `institutional_flow=distribution`
  - 🔵 中性：其餘狀況

### DoD（完成定義）
- Action Plan 卡片可顯示紅綠燈標籤與對應文字
- 標籤映射規則固定且可測試（非寫死單一案例）
- 後端無資料時有明確 fallback 顯示（不造成 UI 崩潰）
- 不影響既有分析報告、信心指數與錯誤訊息區塊
- 前端燈號與後端語義敘事使用同一套 rule-based 輸出，不出現互相矛盾

### 預計測試案例
- `test_action_plan_shows_green_for_bullish_high_confidence`
- `test_action_plan_shows_red_for_overheated_signal`
- `test_action_plan_shows_blue_for_sideways_or_low_conflict_signal`
- `test_action_plan_tag_fallback_when_strategy_fields_missing`
- `test_action_plan_tag_rule_matches_backend_semantic_output`

---

## 執行節奏與驗收紀律

- 每個 Session 結束前，必做：
  1. 單元測試（針對該 Session 新增/修改點）
  2. 受影響模組整合測試（最小範圍）
  3. 回寫交接快照（已完成、阻塞點、下一步）
- 若 Session 超時：優先保留「可驗證輸出 + 測試」，未完成項目順延，不犧牲測試品質

## 建議當日收斂標準

- 最低交付：Session 1 + Session 2 全完成（含測試）
- 理想交付：Session 1~3 完成，Session 4 至少完成標籤映射與基本顯示
