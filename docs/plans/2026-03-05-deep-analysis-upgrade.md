# 計劃：Deep Analysis Upgrade（技術語義化 + 策略具體化）

> 日期：2026-03-05
> 狀態：歷史計劃（主要項目已完成，供回溯參考）
> 目標：解決「技術名詞難懂」與「策略建議不夠具體」兩大痛點
> 原則：完成即補測試（Code Complete ≠ Task Complete；需附對應測試與驗收證據）
> 執行前提：Claude 額度於 18:00 重置後啟動 Session 1

## 使用說明（避免排程混淆）

- 本文件對應的核心任務（Provider Router、語義化 preprocess、策略模板、Action Plan 基礎呈現）已在後續開發中完成。
- 若要規劃「下一步優化」，請優先執行：`docs/plans/2026-03-05-news-summary-quality.md`。
- 本文件建議作為「設計脈絡與驗收思路」參考，不作為明日第一優先執行清單。

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

> **架構決策（定案）**：燈號由後端 rule-based Python 計算並回傳 `action_plan_tag` 欄位，前端僅做 enum → emoji/文字的純顯示映射，不含任何條件判斷邏輯。

### 範圍

**後端（新增）**：
- 在 `strategy_node`（或新增 `tag_node`）計算 `action_plan_tag`
- 輸入：`technical` block 的 `rsi14`、`institutional_flow`（`flow_label`）、`confidence_score`
- 輸出 enum：`opportunity` | `overheated` | `neutral`（前端映射：🟢 機會 / 🔴 過熱 / 🔵 中性）
- 判斷規則（rule-based，固定優先序）：
  - `opportunity`：`rsi14 < 30` + `flow_label = institutional_accumulation` + `confidence_score > 70`
  - `overheated`：`rsi14 > 70` + `flow_label = distribution`
  - `neutral`：其餘狀況（含任一條件不滿足）
- `GraphState` 新增 `action_plan_tag` 欄位；`AnalyzeResponse` 新增 `action_plan_tag: str | null`
- `institutional_flow`（`flow_label`）需作為頂層欄位回傳至 API（目前埋在 `institutional` block，需提升為 `institutional_flow: str | null`）

**前端（純顯示）**：
- 在 Action Plan 卡片標題旁顯示對應標籤（enum → emoji + 中文）
- `opportunity` → `🟢 機會`；`overheated` → `🔴 過熱`；`neutral` → `🔵 中性`
- `action_plan_tag` 為 null 時不顯示標籤，不崩潰
- 保持既有版面，不重構整體 UI

### DoD（完成定義）
- `calculate_action_plan_tag(rsi14, flow_label, confidence_score)` 純 Python，有獨立單元測試
- `AnalyzeResponse` 回傳 `action_plan_tag`（三值 enum + null）
- 前端 Action Plan 卡片可正確顯示三種燈號（含 null fallback）
- 後端無資料（`rsi14 = null` 或 `flow_label = null`）時安全降級回 `neutral`
- 不影響既有分析報告、信心指數與錯誤訊息區塊
- 規則邏輯 100% 在後端，前端**不含**任何 `rsi14 < 30` 之類的條件判斷

### 預計測試案例
- `test_calculate_action_plan_tag_returns_opportunity_when_all_conditions_met`
- `test_calculate_action_plan_tag_returns_overheated_when_rsi_high_and_distribution`
- `test_calculate_action_plan_tag_returns_neutral_for_partial_match`
- `test_calculate_action_plan_tag_falls_back_to_neutral_when_inputs_none`
- `test_api_response_includes_action_plan_tag_field`

---

## Session 5：信心分數可靠性優化（Confidence Score Reliability）

> **問題背景**：多數查詢固定輸出 50（中性基準），核心原因為三個訊號同時退化為預設值：
> 1. `_derive_technical_signal` 判斷 `bullish` 需三條件同時成立，條件過嚴，多數退化 `sideways`
> 2. 機構 Provider 無 API key / 限流時 `flow_label` 固定 `neutral`
> 3. 四條規則為精確命中才調分，三訊號均為 `neutral/sideways` 時 adjustment = 0

### 範圍

**後端**：
- CS-1：`_derive_technical_signal` 改為多因子加權（RSI 位置 / BIAS / 均線排列各自獨立貢獻分量）
- CS-2：`adjust_confidence_by_divergence` 改為多維加權模型（partial match 可得部分調分；引入 `rsi14`、`bias_ma20` 數值直接計算貢獻）
- CS-3：機構資料缺失時以 `unknown` 旗標排除該維度，由剩餘維度計算（調整置信幅度，避免強拉分數至 50）
- CS-4：回傳結構拆分 `data_confidence`（資料完整度，0–100）與 `signal_confidence`（訊號強度，0–100）；前端可顯示「資料不足」提示而非假裝中性

**API / 前端**：
- `AnalyzeResponse` 新增 `data_confidence: int | null`、`signal_confidence: int | null`
- 前端信心指數卡片：`data_confidence < 60` 時卡片下方顯示「資料不足，分數僅供參考」灰色提示

### DoD（完成定義）
- 對技術指標方向明確的股票，分數應偏離 50（至少 ±10）
- 機構資料缺失時分數不因此固定停在 50
- `data_confidence` 與 `signal_confidence` 作為 API 新欄位穩定回傳
- 既有四情境信心分數測試全數通過（回歸保護）
- 新增規則覆蓋測試（partial match / 單維度可用 / None 安全）

### 預計測試案例
- `test_derive_technical_signal_bullish_with_partial_conditions`
- `test_adjust_confidence_partial_match_gives_nonzero_adjustment`
- `test_confidence_excludes_institutional_dimension_when_unknown`
- `test_api_response_includes_data_confidence_and_signal_confidence`
- 原有四情境全數保留（回歸）

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
- Session 5 可獨立排程，不 blocking Session 4；建議在新聞品質優化之後執行
