# AI Stock Sentinel 後端 API 技術規格（v5）

> 類型：技術文件（Technical Doc）
> 更新日期：2026-06-11
> 更新摘要：同步技術面、持股診斷、個人持股上限與 LLM input 穩定化完成狀態；`technical_indicators` 對外欄位新增 KD / ADX / OBV / ATR / MFI / Donchian Channel；籌碼資料新增連續買賣超、主導買賣方、融資融券、借券、外資持股與大戶/散戶結構欄位；`position_analysis` 新增防守線距離、支撐距離、未實現損益與持有天數；個人 active 持股上限調整為 8 筆；更新 `/analyze`、`/analyze/position` 與 `/portfolio` contract；補充 `signal_summary` 為內部 LLM input contract，不屬於 API response；新增 Daily Radar 內部執行與公開讀取 API contract；同步 Daily Radar v2 Phase 1 已穩定的 multi-track universe、market regime、relative strength、version trace、replayable evidence、calibration workflow 與 request budget contract；新增 Daily Radar Phase 2A shared background context cache、chip-context updater endpoint 與背景排程 contract；新增 Daily Radar Phase 2B `background_context_labels` API/detail trace contract；新增 Phase 2C `/analyze` 與 `/analyze/position` 的 shared context read/reference contract；新增 Phase 2D portfolio diagnosis 與 lifecycle review shared context reference / point-in-time contract；Phase 2E release gate 已確認 shared context 只作 evidence/caveat/data quality，不改 Daily Radar ranking、`/analyze/position` rule-based fields、portfolio action 或 lifecycle verdict/classification；新增 Single Trade Review `/portfolio/{portfolio_id}/review` contract、closed portfolio `position_group_id` 欄位與 `review_result.user_readable_conclusion` 使用者可讀結論；新增 group-level Position Lifecycle Review `/portfolio/groups/{position_group_id}/lifecycle-review` contract；補入 Entry Record Optimization Phase A-E 已穩定的 entry context、add-entry、lifecycle plan backfill、decision-context status 與 lifecycle fixed-option review contract；Phase 6 release gate 已建立 rule governance、copy allowlist、forward-validation determinism、portfolio risk data-gap 與 frontend build verifier。

## 1) 目的

本文件定義目前後端 API 的實作契約與錯誤碼，供前後端串接、測試與除錯使用。

---

## 2) 服務啟動

```bash
cd backend
make run-api
```

預設位址：`http://127.0.0.1:8000`

---

## 3) Endpoint 契約

### `GET /health`

- **用途**：健康檢查
- **Response 200**

```json
{
  "status": "ok"
}
```

### `POST /analyze`

- **用途**：執行股票分析流程（LangGraph 回圈：crawl → fetch_technical → fetch_institutional → judge → [data_sufficient/retry_limit: clean | requires_news_refresh: fetch_news → increment_retry → crawl | else: increment_retry → crawl] → clean → analyze）
- **產品語義**：此端點對應 Analyze 頁的「新倉策略建議」，用於評估是否值得觀察、等待與建立新倉；**不是**持股中的續抱 / 減碼 / 出場指令端點

- **Request Body**

```json
{
  "symbol": "2330.TW",
  "news_text": "2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%"
}
```

- **欄位說明**
  - `symbol`：股票代碼，必填，最小長度 1
  - `news_text`：新聞文字，選填
  - `skip_ai`：是否跳過 AI 分析，選填，預設為 false。若為 true，僅撈取並計算 raw data (技術指標/籌碼)，不執行 LLM 推理，以節省成本。

- **Response 200（成功/可降級成功）**

```json
{
  "snapshot": {
    "symbol": "2330.TW",
    "currency": "TWD",
    "current_price": 925.0,
    "previous_close": 920.0,
    "day_open": 921.0,
    "day_high": 928.0,
    "day_low": 918.5,
    "volume": 28450000,
    "recent_closes": [910.0, 915.0, 920.0, 925.0],
    "fetched_at": "2026-03-03T00:00:00+00:00",
    "support_20d": 900.0,
    "resistance_20d": 950.0
  },
  "analysis": "全文分析自然語詞結果（LLM Skeptic Mode 四步驟輸出）",
  "cleaned_news": {
    "date": "2026-03-03",
    "title": "台積電 2 月營收年增",
    "mentioned_numbers": ["2,600", "18.2%"],
    "sentiment_label": "positive"
  },
  "news_display": {
    "title": "台積電 2 月營收年增 20%",
    "date": "2026-03-03",
    "source_url": "https://news.google.com/..."
  },
  "cleaned_news_quality": {
    "quality_score": 100,
    "quality_flags": []
  },
  "data_confidence": 67,
  "signal_confidence": 72,
  "confidence_score": 78,
  "cross_validation_note": "三維共振，信心偏高",
  "analysis_detail": {
    "summary": "台積電技術面維持偏多結構，MACD 與 OBV 仍有量價確認，但布林上緣與 KD 高檔區顯示短線追價風險升高。",
    "risks": [
      "價格靠近布林上軌且 KD 位於高檔區，若 MACD 柱狀體收斂或 OBV 轉弱，短線需留意拉回壓力"
    ],
    "technical_signal": "bullish",
    "institutional_flow": "institutional_accumulation",
    "sentiment_label": "positive",
    "tech_insight": "均線維持多頭排列，MACD 偏多且 OBV 顯示量價確認，ADX 顯示趨勢明確；但 KD 位於高檔區且股價貼近布林上軌，短線續強同時追價風險升高。",
    "inst_insight": "外資近 5 日累計買超 12,500 張，籌碼持續沉澱，機構資金流向偏多。",
    "news_insight": "法說會利多消息帶動市場情緒正面，事件時效性已驗證（日期明確）。",
    "final_verdict": "三維訊號共振：技術面健康、籌碼面偏多、消息面正面，訊號一致性偏高；仍需留意短線追價風險。"
  },
  "technical_indicators": {
    "bollinger_upper": 932.41,
    "bollinger_mid": 905.2,
    "bollinger_lower": 878.0,
    "bollinger_bandwidth": 0.06,
    "bollinger_position": "near_upper",
    "macd_line": 4.213,
    "macd_signal": 3.105,
    "macd_hist": 1.108,
    "macd_bias": "bullish",
    "kd_k": 84.6,
    "kd_d": 78.2,
    "kd_signal": "neutral",
    "kd_zone": "overbought",
    "adx": 28.4,
    "adx_trend_strength": "strong",
    "adx_trend_direction": "bullish",
    "obv": 42850000.0,
    "obv_signal": "price_volume_confirm"
  },
  "sentiment_label": "positive",
  "action_plan": {
    "action": "分批佈局（首筆 20-30%）",
    "target_zone": "900.0–915.0（support_20d ~ MA20）",
    "defense_line": "880.5（近20日低點×0.97）或跌破 MA60",
    "momentum_expectation": "強（法人集結中）；若突破 950.0 壓力則動能轉強",
    "breakeven_note": "當帳面獲利達 5% 時，建議停損位上移至入場成本價",
    "conviction_level": "high",
    "thesis_points": [
      "法人籌碼偏多（持續吸籌）",
      "均線維持多頭排列（close > MA5 > MA20）",
      "新聞情緒偏正向"
    ],
    "upgrade_triggers": ["突破近 20 日壓力（950.0）且量能同步放大"],
    "downgrade_triggers": ["跌破 MA20（915.0）", "法人轉賣超（出貨訊號出現）"],
    "invalidation_conditions": [
      "跌破近 20 日支撐（900.0）",
      "RSI 快速轉弱且價格失守 MA20（915.0）",
      "法人由買超轉為持續賣超"
    ],
    "suggested_position_size": "20-30%"
  },
  "data_sources": ["google-news-rss", "yfinance", "twse-openapi"],
  "institutional_flow_label": "institutional_accumulation",
  "strategy_type": "mid_term",
  "entry_zone": "現價附近分批買進",
  "stop_loss": "近20日低點 - 3% 或跌破 MA60",
  "holding_period": "1-3 個月",
  "action_plan_tag": "opportunity",
  "risk_state": "setup_observation",
  "risk_state_label": "可觀察 setup",
  "discipline_triggers": [
    "跌破近 20 日支撐（900.0）",
    "RSI 快速轉弱且價格失守 MA20（915.0）",
    "法人由買超轉為持續賣超"
  ],
  "observation_conditions": [
    "法人籌碼偏多（持續吸籌）",
    "均線維持多頭排列（close > MA5 > MA20）",
    "新聞情緒偏正向",
    "突破近 20 日壓力（950.0）且量能同步放大"
  ],
  "risk_control_reference": {
    "reference": "880.5（近20日低點×0.97）或跌破 MA60",
    "reference_type": "setup_risk_control_reference"
  },
  "command_language_deprecated": {
    "entry_zone": "現價附近分批買進",
    "stop_loss": "近20日低點 - 3% 或跌破 MA60",
    "action_plan_action": "分批佈局（首筆 20-30%）",
    "target_zone": "900.0–915.0（support_20d ~ MA20）",
    "suggested_position_size": "20-30%"
  },
  "errors": []
}
```

- **欄位說明**

  | 欄位                       | 類型           | 說明                                                                                                                                                                                                                                                                                            |
  | -------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `snapshot`                 | object         | yfinance 即時快照                                                                                                                                                                                                                                                                               |
  | `analysis`                 | string         | LLM Skeptic Mode 四步驟完整分析文字                                                                                                                                                                                                                                                             |
  | `cleaned_news`             | object \| null | LLM pipeline 消費用的新聞結構（`sentiment_label`、`mentioned_numbers` 等）；無新聞時為 null                                                                                                                                                                                                     |
  | `symbol_name`              | string \| null | 股票名稱，僅供前端顯示；新鮮分析由 `snapshot.name` 浮出，舊快取可由 symbol metadata resolver 補齊，查不到時為 `null`                                                                                                                                                                                |
  | `news_display`             | object \| null | 前端顯示用的新聞資料（乾淨 RSS 標題、ISO 日期、來源 URL）；無新聞時為 null                                                                                                                                                                                                                      |
  | `cleaned_news_quality`     | object \| null | 新聞摘要品質評估（`quality_score: 0-100`、`quality_flags: string[]`）；無新聞時為 null                                                                                                                                                                                                          |
  | `data_confidence`          | int \| null    | 0–100，資料完整度（成功取得的維度數量，CS-4 新增）；前端預設應轉成資料品質提示                                                                                                                                                                                                                   |
  | `signal_confidence`        | int \| null    | 0–100，內部訊號強度（CS-4 新增；`confidence_score` 為向後相容別名），用於 guardrail、校準與 trace                                                                                                                                                                                                 |
  | `confidence_score`         | int \| null    | 0–100，內部三維訊號一致性（= `signal_confidence`，向後相容）；不應作為預設前台 headline                                                                                                                                                                                                          |
  | `cross_validation_note`    | string \| null | 三維交叉驗證結論簡述（rule-based 固定字串）                                                                                                                                                                                                                                                     |
  | `strategy_type`            | enum \| null   | `short_term` / `mid_term` / `defensive_wait`                                                                                                                                                                                                                                                    |
  | `entry_zone`               | string \| null | 建議入場區間（rule-based）                                                                                                                                                                                                                                                                      |
  | `stop_loss`                | string \| null | 防守底線／停損條件（rule-based）                                                                                                                                                                                                                                                                |
  | `holding_period`           | string \| null | 預期持股期間（rule-based）                                                                                                                                                                                                                                                                      |
  | `analysis_detail`          | object \| null | LLM 結構化分析輸出，包含 `summary` / `risks` / `technical_signal` / `institutional_flow` / `sentiment_label` / `tech_insight` / `inst_insight` / `news_insight` / `final_verdict`（Session 8 新增分維度欄位）                                                                                   |
  | `technical_indicators`     | object \| null | 技術指標顯性輸出，包含布林通道、MACD、KD、ADX、OBV、ATR、MFI、Donchian Channel 數值與標籤（詳見下方 `technical_indicators` 欄位說明）                                                                                                                                                           |
  | `sentiment_label`          | string \| null | 新聞情緒標籤（從 `cleaned_news.sentiment_label` 浮出）：`positive` / `negative` / `neutral`                                                                                                                                                                                                     |
  | `action_plan`              | object \| null | rule-based 新倉戰術行動計劃（含 `action` / `target_zone` / `defense_line` / `momentum_expectation` / `breakeven_note` / `conviction_level` / `thesis_points` / `upgrade_triggers` / `downgrade_triggers` / `invalidation_conditions` / `suggested_position_size`）；前端主要呈現應改用 risk-language 欄位 |
  | `shared_context`           | object \| null | Phase 2C shared background context read payload；只作 evidence/caveat 與資料完整度 trace，不參與 LLM 數值計算、ranking、bucket、`action_plan` 或 rule-based 欄位覆寫 |
  | `data_sources`             | array          | 本次實際成功取得資料的來源列表（如 `["google-news-rss", "yfinance", "twse-openapi"]`）                                                                                                                                                                                                          |
  | `institutional_flow_label` | enum \| null   | 籌碼歸屬標籤：`institutional_accumulation` / `retail_chasing` / `distribution` / `neutral`                                                                                                                                                                                                      |
  | `action_plan_tag`          | enum \| null   | 燈號標籤（rule-based，後端計算）：`opportunity` / `overheated` / `neutral`；前端僅做顯示映射                                                                                                                                                                                                    |
  | `risk_state`               | string \| null | 研究/紀律語言的 setup 或風險狀態；前端 primary copy 使用                                                                                                                                                                         |
  | `risk_state_label`         | string \| null | `risk_state` 的可讀標籤                                                                                                                                                                                                           |
  | `discipline_triggers`      | array          | 紀律觸發條件；前端 primary copy 使用                                                                                                                                                                                             |
  | `observation_conditions`   | array          | 觀察條件；前端 primary copy 使用                                                                                                                                                                                                 |
  | `risk_control_reference`   | object \| null | 風險控制參考線或參考條件                                                                                                                                                                                                          |
  | `command_language_deprecated` | object       | legacy/internal compatibility 欄位集合；不得作為 primary user-facing copy                                                                                                                                                        |
  | `errors`                   | array          | 錯誤碼陣列                                                                                                                                                                                                                                                                                      |

> **策略產生邊界（`POST /analyze`）**：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`、`action_plan`、`action_plan_tag` 皆由後端 Python rule-based 邏輯產出；LLM 可參與分析文字、新聞情緒或綜合敘事生成，但**不得直接輸出最終進場指令**。Phase 4 後，primary user-facing copy 應使用 `risk_state`、`discipline_triggers`、`observation_conditions` 與 `risk_control_reference`；`entry_zone`、`stop_loss` 與 `action_plan.action` 保留為相容/trace 欄位。

> **Shared context read contract（Phase 2C）**：`shared_context` 由 `shared_background_contexts` cache 以 selected symbol 批次/單檔讀取產生，欄位包含 `version`（目前 `shared-context-read-v1`）、`symbol`、`consumer`、`contexts[]`、`caveats[]` 與 `data_quality`。`contexts[]`/`caveats[]` 使用 consumer-neutral 欄位：`context_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key`、`applicable_consumers`；read path 會尊重 `applicable_consumers`，若 cache row 不適用目標 consumer，會回傳 non-blocking `context_not_applicable_to_consumer` caveat。資料缺漏或 stale 時以 caveat 呈現且 `data_quality.blocking=false`。此 payload 在 response 組裝階段附加，不進入 LangGraph initial state 或 LLM prompt，不觸發 weekly major holders、lending、full margin 的即時逐檔昂貴查詢。

> **`analysis_detail` 分維度欄位**（Session 8，2026-03-09）：
>
> - `tech_insight`：技術面獨立分析段落；可引用均線、RSI、布林通道、MACD、KD、ADX、OBV、ATR、MFI、Donchian Channel、支撐壓力位；禁止提及法人買賣超或新聞事件
> - `inst_insight`：籌碼面獨立分析段落；可引用三大法人、連續買賣超、主導買賣方、融資融券、借券、外資持股與大戶/散戶結構；禁止提及均線數值、RSI、新聞事件
> - `news_insight`：消息面獨立分析段落；禁止提及具體技術指標數值
> - `final_verdict`：三維整合仲裁段落；允許跨維度推論
>   以上四欄位若 LLM 未回傳或回傳空字串，均 fallback 為 `null`，不崩潰。

> **`technical_indicators` 顯性輸出**（2026-05-25）：
>
> - 此欄位為 API 與前端技術指標卡片的正式資料來源。
> - 布林通道與 MACD 數值由 Python 根據 `snapshot.recent_closes` 計算；KD / ADX / ATR / Donchian Channel 由 `recent_closes` + `recent_highs` + `recent_lows` 計算；OBV 由 `recent_closes` + `recent_volumes` 計算；MFI 由 `recent_closes` + `recent_highs` + `recent_lows` + `recent_volumes` 計算。不由 LLM 推算。
> - 資料不足時對應欄位回傳 `null`，不影響主分析流程。
>
> | 欄位                                                    | 類型           | 說明                                                                                                   |
> | ------------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------ |
> | `bollinger_upper` / `bollinger_mid` / `bollinger_lower` | number \| null | 布林通道上中下軌                                                                                       |
> | `bollinger_bandwidth`                                   | number \| null | 布林通道寬度                                                                                           |
> | `bollinger_position`                                    | string \| null | `near_upper` / `above_mid` / `below_mid` / `near_lower` / `flat`                                       |
> | `macd_line` / `macd_signal` / `macd_hist`               | number \| null | MACD 線、訊號線、柱狀體                                                                                |
> | `macd_bias`                                             | string \| null | `bullish` / `bearish` / `neutral`                                                                      |
> | `kd_k` / `kd_d`                                         | number \| null | KD 隨機指標 K、D 值                                                                                    |
> | `kd_signal`                                             | string \| null | `bullish_cross` / `bearish_cross` / `neutral`                                                          |
> | `kd_zone`                                               | string \| null | `oversold` / `overbought` / `neutral`                                                                  |
> | `adx`                                                   | number \| null | ADX 趨勢強度數值                                                                                       |
> | `adx_trend_strength`                                    | string \| null | `strong` / `neutral` / `weak`                                                                          |
> | `adx_trend_direction`                                   | string \| null | `bullish` / `bearish` / `neutral`                                                                      |
> | `obv`                                                   | number \| null | OBV 能量潮累積值                                                                                       |
> | `obv_signal`                                            | string \| null | `price_volume_confirm` / `bearish_divergence` / `bullish_divergence` / `price_volume_weak` / `neutral` |
> | `atr` / `atr_pct`                                       | number \| null | ATR 平均真實波幅與占收盤價百分比                                                                       |
> | `volatility_level`                                      | string \| null | `high` / `medium` / `low` / `unknown`                                                                  |
> | `mfi`                                                   | number \| null | MFI 資金流量指標                                                                                       |
> | `mfi_signal`                                            | string \| null | `overbought` / `oversold` / `bullish_flow` / `bearish_flow` / `neutral`                                |
> | `donchian_upper` / `donchian_lower` / `donchian_mid`    | number \| null | Donchian Channel 20 日區間上緣、下緣、中線                                                             |
> | `donchian_width_pct`                                    | number \| null | Donchian 區間寬度百分比                                                                                |
> | `donchian_position`                                     | string \| null | `breakout_up` / `breakdown_down` / `near_upper` / `near_lower` / `upper_half` / `lower_half` / `flat`  |

> **LLM input contract：`signal_summary`（內部欄位，非 API response）**
>
> - `analyze_node` 在呼叫 LLM 前會建立 compact `signal_summary`，放在 prompt 最前段。
> - 內容包含 rule-based labels（`technical_signal` / `institutional_flow` / `sentiment_label` / `confidence_score` / `cross_validation_note`）、技術證據（布林、MACD、KD、ADX、OBV、ATR、MFI、Donchian、RSI、支撐壓力）、籌碼證據（連續買賣超、主導買賣方、融資融券、借券、持股結構）、消息面聚合與策略標籤。
> - LLM 只能解釋 `signal_summary`，不得改寫 labels 或重新計算分數；此欄位不會出現在 `/analyze` 或 `/analyze/position` response。

---

### `POST /analyze/position`

- **用途**：持股診斷——以使用者購入成本價為錨點，評估當前倉位健康度、動態風險控制參考、紀律觸發與觀察條件（詳見 [持股診斷系統技術規格](./ai-stock-sentinel-position-diagnosis-spec.md)）
- **產品語義**：此端點的 primary user-facing 語言為研究/紀律診斷。`risk_state` / `discipline_triggers` / `observation_conditions` / `risk_control_reference` 是前端與 API consumer 的主要呈現欄位；`recommended_action` / `trailing_stop` / `exit_reason` 仍保留為 legacy/internal compatibility 欄位，不可刪除，但不得作為 primary UI copy。

- **Request Body**

```json
{
  "symbol": "2330.TW",
  "entry_price": 980.0,
  "entry_date": "2026-01-15",
  "quantity": 1000
}
```

- **欄位說明**
  - `symbol`：股票代碼，必填，最小長度 1
  - `entry_price`：購入成本價，必填，正浮點數
  - `entry_date`：購入日期（ISO 8601），選填
  - `quantity`：持有數量，選填，正整數

- **Response 200（成功/可降級成功）**

```json
{
  "snapshot": {
    "symbol": "2330.TW",
    "currency": "TWD",
    "current_price": 1105.0,
    "previous_close": 1098.0,
    "day_open": 1100.0,
    "day_high": 1110.0,
    "day_low": 1095.0,
    "volume": 31200000,
    "recent_closes": [1090.0, 1095.0, 1100.0, 1105.0],
    "fetched_at": "2026-03-09T00:00:00+00:00",
    "support_20d": 1040.0,
    "resistance_20d": 1120.0
  },
  "position_analysis": {
    "entry_price": 980.0,
    "profit_loss_pct": 12.76,
    "position_status": "profitable_safe",
    "position_narrative": "目前獲利已脫離成本區，持股安全緩衝充足。",
    "risk_state": "stable",
    "risk_state_label": "風險狀態穩定",
    "discipline_triggers": ["收盤價需持續對照風險控制參考價 980。"],
    "observation_conditions": ["目前獲利已脫離成本區，持股安全緩衝充足。", "目前相對成本報酬約 12.76%。"],
    "risk_control_reference": {
      "reference_price": 980.0,
      "reference_type": "dynamic_defense_reference",
      "reason": "獲利超過 5%，風險控制參考上移至成本價保本"
    },
    "command_language_deprecated": {
      "recommended_action": "Hold",
      "trailing_stop": 980.0,
      "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價保本",
      "exit_reason": null
    },
    "recommended_action": "Hold",
    "trailing_stop": 980.0,
    "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價保本",
    "exit_reason": null,
    "distance_to_trailing_stop_pct": 12.76,
    "distance_to_support_pct": 6.25,
    "unrealized_pnl": 125000.0,
    "holding_days": 130
  },
  "data_confidence": 100,
  "signal_confidence": 79,
  "confidence_score": 79,
  "cross_validation_note": "三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高",
  "analysis_detail": {
    "summary": "台積電維持多頭結構，ADX 與 OBV 支持趨勢延續，但 KD 高檔與布林上緣提示短線須守住防守節奏。",
    "risks": ["短線若跌回布林中軌下方，或 OBV 由量價確認轉為背離，持股節奏將轉保守"],
    "technical_signal": "bullish",
    "institutional_flow": "institutional_accumulation",
    "sentiment_label": "positive",
    "tech_insight": "均線維持多頭排列，MACD 偏多且 ADX 顯示趨勢明確，OBV 仍呈量價確認；但 KD 已接近高檔，持股可續抱但需觀察是否跌回布林中軌。",
    "inst_insight": "外資近 5 日累計買超 18,500 張，籌碼持續沉澱。",
    "news_insight": "法說會消息偏正向，事件時效性已驗證。",
    "final_verdict": "三維訊號共振，持股健康，目前無出場訊號。"
  },
  "technical_indicators": {
    "bollinger_upper": 1123.84,
    "bollinger_mid": 1055.2,
    "bollinger_lower": 986.56,
    "bollinger_bandwidth": 0.13,
    "bollinger_position": "above_mid",
    "macd_line": 6.842,
    "macd_signal": 5.774,
    "macd_hist": 1.068,
    "macd_bias": "bullish",
    "kd_k": 82.1,
    "kd_d": 76.4,
    "kd_signal": "neutral",
    "kd_zone": "overbought",
    "adx": 31.7,
    "adx_trend_strength": "strong",
    "adx_trend_direction": "bullish",
    "obv": 58320000.0,
    "obv_signal": "price_volume_confirm"
  },
  "institutional_flow_label": "institutional_accumulation",
  "action_plan": {
    "action": "續抱",
    "target_zone": null,
    "defense_line": "980.0（成本保本線）",
    "momentum_expectation": "法人持續買超，動能延續"
  },
  "action_plan_tag": "opportunity",
  "risk_state": "setup_observation",
  "risk_state_label": "可觀察 setup",
  "discipline_triggers": [],
  "observation_conditions": [],
  "risk_control_reference": {
    "reference": "980.0（成本保本線）",
    "reference_type": "setup_risk_control_reference"
  },
  "command_language_deprecated": {
    "entry_zone": null,
    "stop_loss": null,
    "action_plan_action": "續抱",
    "target_zone": null,
    "suggested_position_size": null
  },
  "data_sources": ["google-news-rss", "yfinance", "finmind"],
  "errors": []
}
```

- **欄位說明**

  | 欄位                       | 類型           | 說明                                                                                             |
  | -------------------------- | -------------- | ------------------------------------------------------------------------------------------------ |
  | `snapshot`                 | object         | yfinance 即時快照（與 `/analyze` 相同）                                                          |
  | `position_analysis`        | object         | **持股診斷專屬**——見下方欄位細節                                                                 |
  | `data_confidence`          | int \| null    | 0–100，資料完整度；前端預設應轉成資料品質提示                                                     |
  | `signal_confidence`        | int \| null    | 0–100，內部訊號強度，用於 guardrail、校準與 trace                                                  |
  | `confidence_score`         | int \| null    | = `signal_confidence`，向後相容；不應作為預設前台 headline                                        |
  | `cross_validation_note`    | string \| null | 三維交叉驗證結論（rule-based 固定字串）                                                          |
  | `analysis_detail`          | object \| null | LLM 結構化分析輸出（持股版 context + `signal_summary`，強化持股健康度與風險脈絡解釋）            |
  | `technical_indicators`     | object \| null | 技術指標顯性輸出（與 `/analyze` 相同，包含布林通道、MACD、KD、ADX、OBV，供前端技術指標卡片使用） |
  | `shared_context`           | object \| null | Phase 2C shared background context read payload；只作持股風險 caveat 與資料完整度 trace，不覆寫 `recommended_action`、`trailing_stop` 或 `exit_reason` |
  | `institutional_flow_label` | enum \| null   | `institutional_accumulation` / `retail_chasing` / `distribution` / `neutral`                     |
  | `action_plan`              | object \| null | legacy/internal 行動欄位，前端主要呈現應改用 risk-language 欄位                                  |
  | `action_plan_tag`          | enum \| null   | `opportunity` / `overheated` / `neutral`                                                         |
  | `risk_state`               | string \| null | 研究/紀律語言的 setup 或風險狀態；前端 primary copy 使用                                         |
  | `risk_state_label`         | string \| null | `risk_state` 的可讀標籤                                                                           |
  | `discipline_triggers`      | array          | 紀律觸發條件；前端 primary copy 使用                                                             |
  | `observation_conditions`   | array          | 觀察條件；前端 primary copy 使用                                                                 |
  | `risk_control_reference`   | object \| null | 風險控制參考線或參考條件                                                                          |
  | `command_language_deprecated` | object       | legacy/internal compatibility 欄位集合；不得作為 primary user-facing copy                        |
  | `data_sources`             | array          | 本次成功取得資料的來源列表                                                                       |
  | `errors`                   | array          | 錯誤碼陣列                                                                                       |

- **`position_analysis` 欄位細節**

  | 欄位                            | 類型           | 說明                                                                          |
  | ------------------------------- | -------------- | ----------------------------------------------------------------------------- |
  | `entry_price`                   | float          | 購入成本價（回傳確認）                                                        |
  | `profit_loss_pct`               | float          | 當前損益百分比（rule-based Python 計算）                                      |
  | `position_status`               | string         | `profitable_safe` / `at_risk` / `under_water`                                 |
  | `position_narrative`            | string         | 倉位狀態敘事（rule-based，供 LLM 讀取）                                       |
  | `risk_state`                    | string         | `stable` / `watch` / `elevated` / `critical`，primary user-facing risk state  |
  | `risk_state_label`              | string         | 風險狀態可讀標籤                                                              |
  | `discipline_triggers`           | array          | 紀律觸發條件，primary user-facing copy                                        |
  | `observation_conditions`        | array          | 觀察條件，primary user-facing copy                                            |
  | `risk_control_reference`        | object         | 風險控制參考價與原因                                                          |
  | `command_language_deprecated`   | object         | legacy/internal compatibility 欄位集合                                        |
  | `recommended_action`            | string         | `Hold` / `Trim` / `Exit`（rule-based，LLM 不得覆寫；secondary compatibility） |
  | `trailing_stop`                 | float          | 動態防守價位（rule-based Python 計算；secondary compatibility）               |
  | `trailing_stop_reason`          | string         | 舊停利/停損邏輯說明；primary UI 應改用 `risk_control_reference.reason`         |
  | `exit_reason`                   | string \| null | 舊出場/減碼理由；primary UI 應改用 `discipline_triggers`                       |
  | `distance_to_trailing_stop_pct` | float \| null  | 現價距離動態防守位百分比；正值代表仍在防守位上方                              |
  | `distance_to_support_pct`       | float \| null  | 現價距離 20 日支撐位百分比；正值代表仍在支撐上方                              |
  | `unrealized_pnl`                | float \| null  | 若 request 有 `quantity`，回傳未實現損益金額；未提供數量時為 `null`           |
  | `holding_days`                  | int \| null    | 若 request 有 `entry_date`，回傳持有天數；未提供或日期格式無法解析時為 `null` |

> **`recommended_action` 相容欄位判斷規則（rule-based，後端計算）**：
>
> - `flow_label = distribution` 且 `profit_loss_pct > 0` → `Trim`
> - `flow_label = distribution` 且 `profit_loss_pct <= 0` → `Exit`
> - `technical_signal = bearish` 且 `close < trailing_stop` → `Exit`
> - `close < trailing_stop` 且 OBV / MACD / KD 動能轉弱 → `Exit`
> - `position_status = under_water` 且 `profit_loss_pct < -10%` → `Exit`
> - 獲利中且 `obv_signal` 為 `bearish_divergence` / `price_volume_weak` → `Trim`
> - 獲利達 10% 且 `kd_zone = overbought`、`bollinger_position = near_upper`，但 ADX/OBV/MACD 未形成強趨勢續航 → `Trim`
> - 其他 → `Hold`

> **持股診斷 LLM 邊界**：`/analyze/position` 與 `/analyze` 共用 LangGraph 分析流程與 `signal_summary`；差異是 request 內含 `entry_price` 時，`analyze_node` 會額外建立 `position_context`，讓 LLM 以成本價、損益百分比、動態防守價、距離防守線、距離支撐、未實現損益、持有天數、`recommended_action` 與 `exit_reason` 解釋持股狀態。`recommended_action` / `trailing_stop` / `exit_reason` 仍由 Python rule-based 計算，LLM 不得覆寫；使用者主要呈現必須使用 additive risk-language 欄位。

> **Shared context 邊界（Phase 2C）**：`/analyze/position` 的 `shared_context.consumer = "position_analysis"`。Shared context 只由 shared cache 讀取並附加於 response，作為 weekly major holders、lending、full margin 等背景 caveat 與資料品質說明；它不進入 position scorer，不改 `position_status`、`recommended_action`、`trailing_stop`、`trailing_stop_reason`、`exit_reason` 或任何持股診斷 rule-based 欄位。Missing/stale context 非阻塞，必須以 `freshness` / `missing_reason` / `data_quality` 表示。

> **快取隔離與邊界**：
>
> - `/analyze` 使用 `analysis_type="general"`，`/analyze/position` 使用 `analysis_type="position"`。
> - 快取鍵值包含 `symbol`、`record_date` 與 `analysis_type`，確保不同分析類型互不覆寫。
> - **持股診斷快取邊界**：`/analyze/position` 的 L1 full_result 快取命中必須比對 `entry_price` / `entry_date` / `quantity`。同一檔股票若成本價、日期或數量不同，會強制重跑持股診斷，避免回傳其他成本基準的 `position_analysis`。

---

### `GET /portfolio`

- **用途**：列出目前登入使用者的 active 持股清單，只回傳 `is_active = TRUE` 的持股。
- **Response 200**

```json
[
  {
    "id": 42,
    "symbol": "2330.TW",
    "name": "台積電",
    "entry_price": 900.0,
    "quantity": 1000,
    "entry_date": "2026-01-15",
    "notes": "長期核心持股"
  }
]
```

### `GET /portfolio/closed`

- **用途**：列出目前登入使用者已結案持股清單。
- **查詢邏輯**：只回傳 `current_user.id` 的 inactive 持股，條件為 `is_active = FALSE` 且 `exit_date` 不為 null，排序為 `exit_date DESC`、`updated_at DESC`。
- **Response 200**

```json
[
  {
    "id": 42,
    "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
    "symbol": "2330.TW",
    "name": "台積電",
    "entry_price": 900.0,
    "quantity": 1000,
    "entry_date": "2026-01-15",
    "is_active": false,
    "exit_date": "2026-06-01",
    "exit_price": 980.0,
    "exit_quantity": 1000,
    "exit_fees": 142.0,
    "exit_taxes": 2940.0,
    "realized_pnl": 76918.0,
    "realized_return_pct": 8.5464,
    "holding_days": 137,
    "notes": "長期核心持股"
  }
]
```

- **Response 欄位**：`id`、`position_group_id`、`symbol`、`name`、`entry_price`、`quantity`、`entry_date`、`is_active`、`exit_date`、`exit_price`、`exit_quantity`、`exit_fees`、`exit_taxes`、`realized_pnl`、`realized_return_pct`、`holding_days`、`notes`。

### `POST /portfolio`

- **用途**：新增個人持股紀錄，供「我的持股」頁與 `/analyze/position` 使用。
- **持股上限**：每位使用者最多 **8 筆** active 持股；已達 8 筆時回傳 `422`，錯誤訊息為 `最多只能追蹤 8 筆持股`。

- **Request Body**

```json
{
  "symbol": "2330.TW",
  "entry_price": 900.0,
  "entry_date": "2026-01-15",
  "quantity": 1000,
  "notes": "長期核心持股",
  "entry_record": {
    "entry_reason": "pullback_held_ma20",
    "planned_holding_period": "swing",
    "default_stop_rule": "break_ma20",
    "add_entry_condition": "no_averaging_down",
    "note": "拉回月線守住後建立首筆部位"
  }
}
```

- **Response 201**

```json
{
  "id": 42,
  "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "2330.TW",
  "name": "台積電",
  "entry_price": 900.0,
  "quantity": 1000,
  "entry_date": "2026-01-15",
  "is_active": true,
  "exit_date": null,
  "exit_price": null,
  "exit_quantity": null,
  "exit_fees": null,
  "exit_taxes": null,
  "realized_pnl": null,
  "realized_return_pct": null,
  "holding_days": null,
  "notes": "長期核心持股"
}
```

- **欄位說明**
  - `symbol`：股票代碼，必填；新增前會以 yfinance 輕量驗證代號是否存在
  - `name`：股票名稱，僅供顯示；由 symbol metadata resolver 補齊，查不到時可為 `null`
  - `entry_price`：購入成本價，必填
  - `entry_date`：購入日期，必填，ISO 8601 日期字串
  - `quantity`：持有數量，選填，未提供時預設 0
  - `notes`：備註，選填
  - `entry_record`：選填的進場決策脈絡，若提供則必須符合 `EntryRecordContext` schema；固定選項是未來 lifecycle review 的主要決策資料來源，`note` 僅為補充，不取代固定選項。

- **`EntryRecordContext` 欄位**

  | 欄位 | 類型 | 允許值 / 說明 |
  | --- | --- | --- |
  | `entry_reason` | enum \| null | `breakout_confirmation` / `pullback_held_support` / `pullback_held_ma20` / `institutional_flow_strengthened` / `fundamental_thesis_improved` / `event_or_news_catalyst` / `long_term_accumulation` / `value_revaluation` / `other` / `not_recorded` |
  | `planned_holding_period` | enum \| null | `short_term` / `swing` / `medium_term` / `long_term` / `not_recorded` |
  | `default_stop_rule` | enum \| null | `break_20d_low` / `break_ma20` / `break_ma60` / `cost_minus_pct` / `fixed_price` / `no_stop_recorded` / `not_recorded` |
  | `add_entry_condition` | enum \| null | `no_add_entry` / `breakout_above_prior_high` / `pullback_holds_ma20` / `pullback_holds_support` / `institutional_flow_continues` / `profit_threshold_reached` / `data_quality_complete_only` / `no_averaging_down` / `custom_plan_required` / `not_recorded` |
  | `note` | string \| null | 使用者補充文字；不得作為固定選項缺漏時的替代決策依據。 |

- **事件與計畫寫入語義**
  - 成功建立持股時會寫入 `position_event`，`event_type = initial_entry`，`source = user_recorded_at_event_time`。
  - 若 `entry_record.entry_reason` 有記錄且不是 `not_recorded`，會寫入 initial entry event 的 `reason_category` 與 `reason_code`。
  - 若 `entry_record` 明確帶入 `planned_holding_period`、`default_stop_rule` 或 `add_entry_condition` 任一欄位，會建立 `position_lifecycle_plan`，`source = user_recorded_at_event_time`，`created_after_entry = false`。
  - 不提供 `entry_record` 或只提供選填 notes 時，不得推論使用者意圖；後續 lifecycle review 需以 `decision_context.status = insufficient` 或既有資料品質 caveat 呈現限制。

### `GET /portfolio/decision-context-status`

- **用途**：列出目前登入使用者 active 持股的 operation plan / decision context 狀態，用於前端提示是否需要補填操作計畫。
- **Response 200**：以 portfolio id 字串為 key 的 map。

```json
{
  "42": {
    "portfolio_id": 42,
    "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
    "symbol": "2330.TW",
    "has_operation_plan": true,
    "operation_plan_status": "backfilled",
    "missing_operation_plan": false,
    "decision_context": "present",
    "source": "user_backfilled",
    "created_after_entry": true,
    "planned_invalidation_present": true,
    "shared_context": {
      "version": "shared-context-read-v1",
      "symbol": "2330.TW",
      "consumer": "portfolio_diagnosis",
      "reference_date": "2026-06-11",
      "point_in_time": true,
      "contexts": [],
      "caveats": [],
      "data_quality": {
        "status": "missing",
        "blocking": false
      }
    }
  }
}
```

- **欄位說明**
  - `operation_plan_status`：`missing` / `present` / `backfilled`。
  - `decision_context`：`present` / `insufficient`。沒有 lifecycle plan 時為 `insufficient`，前端與 review 不得推論未記錄意圖。
  - `source`：`user_recorded_at_event_time` / `user_backfilled` / `synthetic_from_portfolio_row` / `manual_record_correction` / `not_recorded` / `null`。
  - `created_after_entry`：plan 是否在進場後補填；`true` 時不得視為原始進場當下已存在的計畫。
  - `planned_invalidation_present`：目前 plan 是否有 `planned_invalidation` 文字。
  - `shared_context`：Phase 2D portfolio diagnosis shared context reference。只讀 `shared_background_contexts` cache，作為 evidence/caveat 與資料品質說明；不得轉成 portfolio action、加減碼指令或交易建議。Active portfolio 最多 8 筆，因此此 read path 為 bounded cache read，不觸發 weekly major holders、lending、full margin 即時逐檔 provider。

### `GET /portfolio/risk-summary`

- **用途**：Phase 5 read-only portfolio risk summary。以目前登入使用者的 active positions、最新可用 `stock_raw_data` 與既有 lifecycle plan 產生 deterministic portfolio-level risk diagnostics。
- **資料邊界**：只讀 `user_portfolio`、`position_lifecycle_plan` 與 `stock_raw_data`；不得建立、修改或刪除持股、交易事件、review 或任何 portfolio state。
- **語言邊界**：此 response 是風險紀律診斷，不輸出 portfolio action、recommended action 或交易命令。若 sector/theme data 不可靠，concentration 僅做 symbol / setup-type / risk-state / stop-rule 類別，不硬編產業分類。
- **缺資料行為**：`missing_price`、`missing_defense_reference`、`zero_quantity`、`stale_price` 皆以 `data_quality.caveats[]` 明示；缺少必要欄位時相關部位的 `estimated_risk_amount` 與 `estimated_risk_pct_of_portfolio` 可為 `null`，不得捏造成 0。

- **Response 200**

```json
{
  "version": "portfolio-risk-summary-v1",
  "as_of_date": "2026-06-12",
  "portfolio_value": 120000.0,
  "total_unrealized_pnl": 20000.0,
  "total_at_risk": 25000.0,
  "total_at_risk_pct": 20.8333,
  "position_risks": [
    {
      "symbol": "2330.TW",
      "name": "台積電",
      "quantity": 1000.0,
      "current_price": 120.0,
      "entry_price": 100.0,
      "market_value": 120000.0,
      "unrealized_pnl": 20000.0,
      "defense_reference": {
        "price": 95.0,
        "source": "planned_stop_price"
      },
      "estimated_risk_amount": 25000.0,
      "estimated_risk_pct_of_portfolio": 20.8333,
      "portfolio_weight_pct": 100.0,
      "risk_state": "elevated",
      "discipline_triggers": [
        "單一部位估計曝險占投資組合 20.83%，高於 5% 檢查線。"
      ],
      "data_quality": {
        "status": "ok",
        "caveats": []
      }
    }
  ],
  "concentration": {
    "by_symbol": [
      {
        "type": "symbol",
        "key": "2330.TW",
        "market_value": 120000.0,
        "pct_of_portfolio": 100.0,
        "status": "elevated"
      }
    ]
  },
  "shared_exposures": [
    {
      "type": "setup_type",
      "key": "breakout",
      "symbols": ["2330.TW"],
      "count": 1,
      "market_value": 120000.0,
      "pct_of_portfolio": 100.0
    }
  ],
  "risk_budget_status": {
    "status": "constrained",
    "total_at_risk_pct": 20.8333,
    "watch_threshold_pct": 5.0,
    "constrained_threshold_pct": 10.0,
    "notes": []
  },
  "data_quality": {
    "status": "ok",
    "caveats": [],
    "price_stale_after_days": 5
  }
}
```

### `GET /portfolio/{portfolio_id}/lifecycle-plan`

- **用途**：讀取目前登入使用者某筆持股所屬 `position_group_id` 的 lifecycle plan。若尚無 plan，欄位回傳 `null`。
- **權限邊界**：只能讀取目前登入使用者自己的持股；非擁有者回傳 `403`。
- **Response 200**

```json
{
  "portfolio_id": 42,
  "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "2330.TW",
  "thesis": "拉回月線守住後建立首筆部位",
  "setup_type": "pullback",
  "planned_holding_period": "swing",
  "default_stop_rule": "break_ma20",
  "add_entry_condition": "no_averaging_down",
  "planned_invalidation": "跌破 MA20 且法人轉弱",
  "planned_stop_price": null,
  "planned_target_or_scale_out_rule": null,
  "planned_risk_amount": null,
  "planned_risk_pct": null,
  "position_sizing_rationale": null,
  "source": "user_backfilled",
  "created_after_entry": true
}
```

### `PUT /portfolio/{portfolio_id}/lifecycle-plan/backfill`

- **用途**：為既有 active 持股補填 lifecycle plan，改善未來 review context，但不把補填內容當成原始進場當下意圖。
- **權限與狀態邊界**：只能補填目前登入使用者自己的 active 持股；非擁有者回傳 `403`，已結案持股回傳 `409`。
- **衝突行為**：若已存在 `source != user_backfilled` 的原始進場計畫，回傳 `409`，訊息為 `已有原始進場計畫，不可改為事後補填`。
- **Request Body**

```json
{
  "thesis": "拉回月線守住後建立首筆部位",
  "setup_type": "pullback",
  "planned_holding_period": "swing",
  "default_stop_rule": "break_ma20",
  "add_entry_condition": "no_averaging_down",
  "planned_invalidation": "跌破 MA20 且法人轉弱",
  "planned_stop_price": 142.5,
  "planned_target_or_scale_out_rule": "先在前高附近減碼一半",
  "planned_risk_amount": 5000,
  "planned_risk_pct": 1.0,
  "position_sizing_rationale": "首筆試單，確認後再加碼"
}
```

- **Response 200**：回傳欄位同 `GET /portfolio/{portfolio_id}/lifecycle-plan`，且 `source = user_backfilled`、`created_after_entry = true`。

### `PUT /portfolio/{portfolio_id}`

- **用途**：更新既有持股的成本價、數量、購入日期與備註。
- **權限邊界**：只能更新目前登入使用者自己的持股；非擁有者回傳 `403`。

- **Request Body**

```json
{
  "entry_price": 950.0,
  "entry_date": "2026-02-01",
  "quantity": 1200,
  "notes": "調整成本後續追蹤"
}
```

- **Response 200**

```json
{
  "id": 42,
  "symbol": "2330.TW",
  "entry_price": 950.0,
  "quantity": 1200,
  "entry_date": "2026-02-01",
  "notes": "調整成本後續追蹤"
}
```

### `POST /portfolio/{portfolio_id}/add-entry`

- **用途**：為 active 持股建立明確加碼事件；此端點是記錄 add-entry intent 的唯一入口之一，不從一般 `PUT /portfolio/{portfolio_id}` 數量變更推論加碼。
- **權限與狀態邊界**：只能加碼目前登入使用者自己的 active 持股；非擁有者回傳 `403`，已結案持股回傳 `409`。
- **Request Body**

```json
{
  "event_date": "2026-02-01",
  "price": 920.0,
  "quantity": 500,
  "fees": null,
  "taxes": null,
  "reason_code": "planned_scale_in",
  "plan_adherence": "yes",
  "confidence_level": "medium",
  "note": "拉回不破 MA20 後依計畫加碼"
}
```

- **欄位說明**
  - `event_date`：加碼日期，必填，不可早於初始進場日期；違反時回傳 `422`，訊息為 `加碼日期不可早於初始進場日期`。
  - `price`：加碼價格，必填，需大於 0。
  - `quantity`：加碼股數，必填，需大於 0。
  - `fees`：手續費，選填，未提供時依 broker fee rule 計算 event ledger fee。
  - `taxes`：交易稅，選填，未提供時 add-entry event 稅額為 0。
  - `reason_code`：`breakout_confirmation` / `pullback_held_support` / `pullback_held_ma20` / `institutional_flow_strengthened` / `fundamental_thesis_improved` / `event_or_news_catalyst` / `long_term_accumulation` / `value_revaluation` / `other` / `planned_scale_in` / `averaging_down` / `chasing_momentum` / `not_recorded`。
  - `plan_adherence`：`yes` / `partial` / `no` / `not_recorded`。
  - `confidence_level`：`high` / `medium` / `low` / `not_recorded`。
  - `note`：選填補充文字；不替代固定選項。

- **行為與計算邊界**
  - 會以平均成本法更新 active portfolio 的 `entry_price` 與 `quantity`。
  - 會寫入 `position_event`，`event_type = add_entry`，`source = user_recorded_at_event_time`，並保存 `reason_code`、`plan_adherence`、`confidence_level`、`fees`、`taxes`。
  - `not_recorded` reason 會保留為未記錄脈絡，不推論使用者加碼意圖。

- **Response 201**

```json
{
  "portfolio": {
    "id": 42,
    "symbol": "2330.TW",
    "name": "台積電",
    "entry_price": 906.6667,
    "quantity": 1500,
    "entry_date": "2026-01-15",
    "notes": "長期核心持股"
  },
  "event": {
    "id": 101,
    "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
    "symbol": "2330.TW",
    "event_type": "add_entry",
    "event_date": "2026-02-01",
    "price": 920.0,
    "quantity": 500,
    "fees": 653.0,
    "taxes": 0.0,
    "source_portfolio_id": 42,
    "note": "拉回不破 MA20 後依計畫加碼",
    "reason_category": "plan_execution",
    "reason_code": "planned_scale_in",
    "plan_adherence": "yes",
    "confidence_level": "medium",
    "source": "user_recorded_at_event_time",
    "data_quality_note": null,
    "created_at": "2026-06-10T10:30:00Z",
    "updated_at": "2026-06-10T10:30:00Z"
  }
}
```

### `POST /portfolio/{portfolio_id}/close`

- **用途**：將目前登入使用者的一筆 active 持股出場結案，保留持股與歷史診斷紀錄。
- **權限邊界**：只能結案目前登入使用者自己的持股；非擁有者回傳 `403`。

- **Request Body**

```json
{
  "exit_date": "2026-06-01",
  "exit_price": 980.0,
  "exit_quantity": 1000,
  "fees": 142.0,
  "taxes": 2940.0
}
```

- **欄位說明**
  - `exit_date`：出場日期，必填，ISO 8601 日期字串。
  - `exit_price`：出場價格，必填，需大於 0。
  - `exit_quantity`：出場股數，必填，需大於 0，且不可大於目前 active 持有股數。
  - `fees`：手續費，選填，需大於或等於 0；未提供時依 broker fee rule 自動估算，若提供則視為使用者覆寫值。
  - `taxes`：交易稅，選填，需大於或等於 0；未提供時依 sell transaction tax rule 自動估算，若提供則視為使用者覆寫值。

- **計算邏輯**
  - 已實現損益採平均成本法計算：`realized_pnl = (exit_price - entry_price) * exit_quantity - fees - taxes`，其中 `fees` / `taxes` 使用同一組寫入 closed portfolio row 與 `position_event` 的實際成本值。
  - 已實現報酬率採本次出場股數的成本基準計算：`realized_return_pct = realized_pnl / (entry_price * exit_quantity) * 100`
  - `holding_days = exit_date - entry_date` 的天數
  - `exit_quantity == quantity` 時為全數平倉：原持股設定 `is_active = FALSE`，並回傳該筆 closed portfolio。
  - `exit_quantity < quantity` 時為部分平倉：原 active 持股保留並扣減 `quantity`，另建立一筆 `is_active = FALSE` 的 closed portfolio 紀錄，該 inactive 紀錄代表本次出場股數，response 回傳新建立的 closed portfolio。

- **Response 200**：回傳欄位同 `GET /portfolio/closed` 的 closed portfolio 物件。

- **錯誤行為**
  - 已結案持股回傳 `409`，訊息為 `持倉已關閉`。
  - `exit_quantity` 大於目前 active 持有股數時回傳 `422`，訊息為 `出場股數不可大於持有股數`。
  - `exit_date` 早於 `entry_date` 時回傳 `422`，訊息為 `出場日期不可早於進場日期`。

- **歷史保留**：此端點不刪除 `daily_analysis_log`，結案後仍可保留歷史診斷。

### `GET /portfolio/latest-history` / `GET /portfolio/{portfolio_id}/history`

- **用途**：讀取 portfolio history 的最新或分頁診斷紀錄。History response 會保留 `recommended_action` 作為 legacy compatibility 欄位，但 primary display 應使用 additive risk-language 欄位。
- **Additive risk-language 欄位**
  - `risk_state`：`stable` / `watch` / `elevated` / `critical` / `unknown`。
  - `risk_state_label`：給前端主要呈現的中文風險狀態。
  - `discipline_triggers`：紀律觸發條件清單。
  - `risk_control_reference`：風險控制參考價或參考條件；資料不足時為 `null`。
  - `compatibility_source`：`position_risk_language` / `legacy_recommended_action` / `insufficient_history_data`，表示該 row 的 risk-language 來源。
- **來源優先順序**
  1. `daily_analysis_log.indicators.position_risk_language` 或由 `stock_analysis_cache.full_result.position_analysis` seed 的 snapshot。
  2. 舊資料的 `recommended_action` fallback mapping。
  3. 無足夠資料時回傳 `risk_state = unknown`、`risk_state_label = 資料不足`。
- **相容策略**：`recommended_action` 仍存在於 response，不可作為 primary UI copy；前端歷史視圖必須優先讀 `risk_state_label`。

### `GET /portfolio/{portfolio_id}/review`

- **用途**：讀取一筆已結案持股的已保存 Single Trade Review。
- **權限邊界**：只能讀取目前登入使用者自己的 closed portfolio row；非擁有者回傳 `403`。
- **資料邊界**：review 單位是一筆 closed `UserPortfolio` row，也就是一次 realized exit batch；不合併同 `position_group_id` 的其他出場批次，也不做 lifecycle review。
- **Response 200**：回傳欄位同 `POST /portfolio/{portfolio_id}/review`。
- **錯誤行為**：
  - 目標持股仍為 active 或沒有 `exit_date` 時回傳 `422`，訊息為 `僅可審核已結案持倉`。
  - 尚未建立 review 時回傳 `404`，訊息為 `尚未建立交易審核`。

### `POST /portfolio/{portfolio_id}/review`

- **用途**：為一筆已結案持股建立 deterministic rule-based Single Trade Review；若已存在 saved review，直接回傳既有 review，不重新產生。
- **Request Body**：無必填欄位；目前 frontend 送出空 POST body。
- **持久化語義**：同一 `portfolio_id` 只會有一筆預設 review。第一次 POST 建立 `trade_review`，第二次以後 POST 回傳既有資料；沒有 refresh 或重新分析行為。
- **LLM 邊界**：目前不呼叫 LLM，`llm_summary` 固定為 `null`。
- **Evidence 邊界**：`evidence_payload` 只存 trade scalar、path metrics、point-in-time indicators、detected events、data quality、source summary；不存完整 OHLCV/K-line arrays、raw news、raw LLM prompts 或 unrelated portfolio history。

- **Response 200**

```json
{
  "id": 456,
  "portfolio_id": 123,
  "user_id": 1,
  "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "2330.TW",
  "review_version": "trade-review-v1",
  "review_result": {
    "data_quality": {
      "status": "ok",
      "notes": [],
      "insufficient_data": []
    },
    "trade_result": {
      "entry_date": "2026-01-05",
      "exit_date": "2026-02-14",
      "entry_price": 980.0,
      "exit_price": 1040.0,
      "realized_pnl": 60000.0,
      "realized_return_pct": 6.12,
      "holding_days": 40,
      "max_profit_pct": 12.4,
      "max_drawdown_pct": -4.8,
      "profit_giveback_pct": 6.2,
      "highest_close_during_holding": 1102.0,
      "lowest_close_during_holding": 933.0,
      "entry_indicators": {
        "as_of_date": "2026-01-05",
        "ma20": 950.0,
        "ma60": 910.0,
        "rsi14": 72.0,
        "volume_ratio": 1.8,
        "entry_vs_ma20_pct": 3.16,
        "entry_vs_ma60_pct": 7.69,
        "market_regime": "strong_momentum"
      },
      "exit_indicators": {
        "as_of_date": "2026-02-14",
        "ma20": 1055.0,
        "ma60": 990.0,
        "rsi14": 48.0,
        "volume_ratio": 1.3,
        "exit_vs_ma20_pct": -1.42,
        "exit_vs_ma60_pct": 5.05,
        "market_regime": "uptrend"
      }
    },
    "entry_review": {
      "classification": "breakout_entry",
      "confidence": "medium",
      "market_regime": "strong_momentum",
      "supporting_signals": ["Entry close broke above the recent 20-row high."],
      "conflicting_signals": [],
      "caveats": [],
      "summary": "Entry leaned breakout with price and volume confirmation."
    },
    "holding_review": {
      "market_regime": "uptrend",
      "confidence": "medium",
      "detected_events": [
        {
          "date": "2026-02-10",
          "type": "profit_giveback",
          "summary": "Close gave back at least 5% from the holding-period high.",
          "evidence": { "close": 1040.0 }
        }
      ],
      "event_count": 1,
      "risk_event_count": 1,
      "supporting_signals": ["Detected profit_giveback on 2026-02-10."],
      "conflicting_signals": [],
      "caveats": [],
      "summary": "Chronological holding review uses capped technical events only."
    },
    "exit_review": {
      "classification": "profit_protection_exit",
      "confidence": "medium",
      "market_regime": "uptrend",
      "supporting_signals": ["Exit protected realized gains after momentum cooled or giveback appeared."],
      "conflicting_signals": [],
      "caveats": [],
      "summary": "Exit protected profit after cooling or giveback evidence."
    },
    "operation_review": {
      "classification": "rule_based_trade_review",
      "confidence": "medium",
      "market_regime": "uptrend",
      "supporting_signals": ["Review scope is the current closed portfolio row only."],
      "conflicting_signals": [],
      "caveats": [],
      "reviewed_portfolio_id": 123,
      "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
      "scope": "current_closed_row_only",
      "summary": "Operation review preserves the existing persistence/API boundary and does not aggregate same-group rows."
    },
    "user_readable_conclusion": {
      "overall_verdict": "reasonable",
      "overall_verdict_label": "這次出場合理",
      "one_sentence_reason": "這筆交易有保住已實現獲利，出場前也已出現動能降溫或獲利回吐跡象。",
      "evidence": [
        "已實現報酬率 6.12%",
        "持有期間最高收盤價 1102.0，出場價 1040.0",
        "持有期間偵測到 profit_giveback 事件"
      ],
      "next_time_rules": [
        "下次獲利拉開後，先設定可接受的回吐比例。",
        "若動能降溫伴隨獲利回吐，優先檢查是否該分批保護獲利。"
      ]
    }
  },
  "evidence_payload": {
    "trade": {
      "id": 123,
      "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
      "symbol": "2330.TW",
      "entry_price": 980.0,
      "entry_date": "2026-01-05",
      "exit_date": "2026-02-14",
      "exit_price": 1040.0,
      "return_pct": 6.12,
      "holding_days": 40
    },
    "path_metrics": {
      "max_profit_pct": 12.4,
      "max_drawdown_pct": -4.8,
      "profit_giveback_pct": 6.2
    },
    "entry_indicators": {
      "ma20": 950.0,
      "ma60": 910.0,
      "rsi14": 72.0,
      "volume_ratio": 1.8,
      "market_regime": "strong_momentum"
    },
    "exit_indicators": {
      "ma20": 1055.0,
      "ma60": 990.0,
      "rsi14": 48.0,
      "volume_ratio": 1.3,
      "market_regime": "uptrend"
    },
    "detected_events": [
      {
        "date": "2026-02-10",
        "type": "profit_giveback",
        "summary": "Close gave back at least 5% from the holding-period high.",
        "evidence": { "close": 1040.0 }
      }
    ],
    "data_quality": {
      "status": "ok",
      "notes": [],
      "insufficient_data": []
    },
    "source_data": {
      "symbol": "2330.TW",
      "rows_up_to_exit": 80,
      "holding_rows": 28,
      "first_record_date": "2025-11-01",
      "last_record_date": "2026-02-14"
    }
  },
  "llm_summary": null,
  "created_at": "2026-06-04T10:30:00Z",
  "updated_at": "2026-06-04T10:30:00Z"
}
```

- **主要欄位說明**
  - `review_result.data_quality.status`：`ok` 或 `insufficient`。
  - `review_result.user_readable_conclusion`：前端「交易檢討結論」的資料來源，包含 `overall_verdict`、`overall_verdict_label`、`one_sentence_reason`、`evidence`、`next_time_rules`。
  - `review_result.user_readable_conclusion.overall_verdict`：`early` / `reasonable` / `late` / `insufficient`。
  - `entry_review.classification`：`breakout_entry` / `pullback_entry` / `chase_entry` / `weak_entry` / `range_entry` / `insufficient_data`。
  - `exit_review.classification`：`profit_protection_exit` / `stop_loss_exit` / `late_stop_exit` / `early_profit_exit` / `panic_exit` / `technical_break_exit` / `insufficient_data`。
  - `confidence`：`high` / `medium` / `low`。
  - `market_regime`：`uptrend` / `downtrend` / `range_bound` / `strong_momentum` / `high_volatility` / `insufficient_data`。
  - `holding_review.detected_events`：最多保留重要 holding events，event item 不包含完整 K 線序列。

> **Single Trade Review 結論邊界**：`review_result.user_readable_conclusion` 是 `review_result` JSONB 內的 additive 欄位，不需資料庫 migration。它由後端 deterministic rule-based 邏輯產出，不呼叫 LLM，不新增 `llm_summary`，也不需要將 `review_version` 從 `trade-review-v1` 升版。若資料不足，`overall_verdict` 回傳 `insufficient`，並在 `evidence` 與 `next_time_rules` 說明限制。

### Closed portfolio grouping behavior

- `/portfolio/closed` 回傳的每筆 closed portfolio 皆包含 `position_group_id`。
- 前端 `/portfolio/closed` 依可見 rows 的 `position_group_id` 做視覺分組，group header 顯示 symbol、entry date、entry price、可見批次 total closed quantity、可見批次 total realized PnL、exit batch count。
- `檢討分析` 按鈕只出現在每個 exit batch child row，語義是 Single Trade Review：一筆 closed portfolio row / one sell decision。
- group header 提供 `整體部位檢討`，語義是 Position Lifecycle Review：同一 `position_group_id` 下的 multi-entry / multi-exit lifecycle。
- group header 也保留 `操作時間線`，可只檢視 event ledger，不等同 lifecycle review。

### `GET /portfolio/groups/{position_group_id}/events`

- **用途**：讀取同一 `position_group_id` 的 event ledger timeline，供 closed portfolio group 的操作時間線與 lifecycle review trace 使用。
- **權限邊界**：只能讀取目前登入使用者自己的 position group；非擁有者回傳 `403`。
- **排序**：`event_date ASC`、`created_at ASC`、`id ASC`。
- **Response 200**

```json
{
  "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "2330.TW",
  "events": [
    {
      "id": 101,
      "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
      "symbol": "2330.TW",
      "event_type": "initial_entry",
      "event_date": "2026-01-15",
      "price": 900.0,
      "quantity": 1000,
      "fees": 0.0,
      "taxes": 0.0,
      "source_portfolio_id": 42,
      "note": "拉回月線守住後建立首筆部位",
      "reason_category": "technical",
      "reason_code": "pullback_held_ma20",
      "plan_adherence": null,
      "confidence_level": null,
      "source": "user_recorded_at_event_time",
      "data_quality_note": null,
      "created_at": "2026-06-10T10:30:00Z",
      "updated_at": "2026-06-10T10:30:00Z"
    }
  ]
}
```

- **Event enum contract**
  - `event_type`：`initial_entry` / `add_entry` / `partial_exit` / `full_exit` / `manual_adjustment`。
  - `source`：`synthetic_from_portfolio_row` / `user_backfilled` / `user_recorded_at_event_time` / `manual_record_correction` / `not_recorded`。
  - `reason_category`：`technical` / `institutional_flow` / `fundamental` / `news` / `risk_control` / `plan_execution` / `emotional` / `record_correction` / `not_recorded` / `null`。
  - `plan_adherence`：`yes` / `partial` / `no` / `not_recorded` / `null`。
  - `confidence_level`：`high` / `medium` / `low` / `not_recorded` / `null`。

### `GET /portfolio/groups/{position_group_id}/lifecycle-review`

- **用途**：讀取同一 `position_group_id` 的已保存 Position Lifecycle Review；此端點只讀取已保存結果，不觸發 freshness 檢查或重算。
- **權限邊界**：只能讀取目前登入使用者自己的 position group；非擁有者回傳 `403`。
- **資料邊界**：review 單位是整個 position group lifecycle，不與 `/portfolio/{portfolio_id}/review` 共用 endpoint，也不寫入 `trade_review`。
- **Response 200**：回傳欄位同 `POST /portfolio/groups/{position_group_id}/lifecycle-review`。
- **404**：目前登入使用者擁有該 group 但尚未建立 saved lifecycle review 時，回傳 `404`。

### `POST /portfolio/groups/{position_group_id}/lifecycle-review`

- **用途**：為同一 `position_group_id` 建立或更新 deterministic rule-based Position Lifecycle Review；若同版 saved review 已存在且來源資料未變，直接回傳既有 review。
- **權限邊界**：只能建立目前登入使用者自己的 position group lifecycle review；非擁有者回傳 `403`。
- **持久化語義**：第一次 POST 建立 `position_lifecycle_review`，`review_result` 與 `evidence_payload` 在同一 transaction 寫入。第二次以後 POST 會比較同一使用者與 `position_group_id` 下 `PositionEvent.updated_at` 與 `PositionLifecyclePlan.updated_at` 的最新時間；若來源資料比 saved review 更新，重建 `review_result` / `evidence_payload` 並更新同一筆 `position_lifecycle_review`，避免部分出場後新增事件或事後補填 plan 時持續讀到 stale lifecycle review。
- **版本策略**：`review_version` 為 `position-lifecycle-review-v1`，以 `user_id + position_group_id + review_version` 唯一避免同版重複保存。
- **LLM 邊界**：本端點不呼叫 LLM，不新增 LLM summary；`llm_summary` 固定為 `null`。Phase F 若要加入 summary，必須另行升版或新增 explicit narrative refresh contract。
- **Evidence 邊界**：`evidence_payload` 只存 compact event facts、lifecycle metrics、entry/exit sequence metrics、advanced internal trace、point-in-time indicator snapshots、capped detected events、market regime snapshots、Phase 2D point-in-time shared context references、source summary 與 data quality；不存完整 OHLCV/K-line arrays、raw LLM prompts、raw user notes、未記錄意圖推論、plan thesis 或 planned invalidation。
- **Shared context point-in-time 邊界（Phase 2D）**：`review_result.shared_context` 與 `evidence_payload.shared_context` 以每個 `PositionEvent.event_date` 作為 `reference_date`，只引用適用目標 consumer 且 `as_of_date <= event_date` 的 shared background context。`shared_background_contexts` 以 `symbol` / `context_type` / `replay_key` 保留歷史 trace；若沒有可用歷史 context 且只存在晚於事件日的 context，會以 `missing_reason = "future_context_excluded"` 保留 caveat，並保留原始 excluded `as_of_date` trace；不得使用該未來資料批評 entry/exit-time decision。Shared context 只作 evidence/caveat/data quality，不改 `lifecycle_review.classification.primary_label`、tier、deterministic metrics 或 fixed-option decision-context 判讀。
- **Response 200**

```json
{
  "id": 789,
  "user_id": 1,
  "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "2330.TW",
  "review_version": "position-lifecycle-review-v1",
  "review_result": {
    "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
    "symbol": "2330.TW",
    "lifecycle_metrics": {
      "total_realized_pnl": 12000.0,
      "total_return_pct_on_weighted_cost": 5.42,
      "weighted_average_entry_price": 900.0,
      "profit_giveback_pct": 8.5
    },
    "entry_sequence": {
      "entry_count": 2,
      "add_entry_count": 1,
      "average_down_count": 0,
      "add_after_breakdown_count": 0
    },
    "exit_sequence": {
      "exit_count": 2,
      "partial_exit_count": 1,
      "percentage_sold_before_peak": 40.0,
      "percentage_sold_after_breakdown": 0.0,
      "profit_protected_by_partial_exits": 8000.0
    },
    "advanced_internal": {
      "plan_adherence_score": 75.0,
      "decision_quality_score": 68.2
    },
    "event_indicator_snapshots": [
      {
        "event_key": "id:101",
        "event_type": "initial_entry",
        "event_date": "2026-01-05",
        "ma20": 880.0,
        "ma60": 850.0,
        "rsi14": 61.0,
        "event_price_vs_ma20_pct": 2.27,
        "market_regime": "uptrend"
      }
    ],
    "event_facts": [
      {
        "event_key": "id:101",
        "id": 101,
        "event_type": "initial_entry",
        "event_date": "2026-01-05",
        "price": 900.0,
        "quantity": 100,
        "fees": 0.0,
        "taxes": 0.0,
        "reason_code": "breakout_confirmation",
        "plan_adherence": "yes",
        "source": "user_recorded_at_event_time"
      }
    ],
    "decision_context": {
      "status": "present",
      "has_plan": true,
      "source": "user_backfilled",
      "created_after_entry": true,
      "planned_holding_period": "swing",
      "default_stop_rule": "break_ma20",
      "add_entry_condition": "no_averaging_down"
    },
    "data_quality": {
      "status": "ok",
      "notes": [],
      "insufficient_data": []
    },
    "lifecycle_review": {
      "classification": {
        "primary_label": "disciplined_scale_out",
        "labels": ["disciplined_scale_out", "coherent_position_management"],
        "tier": "constructive",
        "reasons": [
          {
            "text": "Partial exits protected realized profit before the position was fully closed.",
            "source_refs": ["exit_sequence.partial_exit_count", "exit_sequence.profit_protected_by_partial_exits"]
          }
        ],
        "caveats": [],
        "source_refs": ["exit_sequence.partial_exit_count", "exit_sequence.profit_protected_by_partial_exits"]
      },
      "overall_conclusion": {
        "text": "Lifecycle review tier is constructive; primary classification is disciplined_scale_out.",
        "source_refs": ["exit_sequence.partial_exit_count", "exit_sequence.profit_protected_by_partial_exits"]
      },
      "what_worked": [],
      "what_needs_review": [],
      "event_level_evidence": [],
      "next_operation_rules": [],
      "data_quality_notes": []
    }
  },
  "evidence_payload": {
    "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
    "symbol": "2330.TW",
    "metrics": {
      "lifecycle": {},
      "entry_sequence": {},
      "exit_sequence": {},
      "advanced_internal": {}
    },
    "events": [],
    "indicator_snapshots": [],
    "detected_events": [],
    "market_regime_snapshots": [],
    "source_data": {
      "symbol": "2330.TW",
      "event_count": 4,
      "market_row_count": 80,
      "plan_present": true
    },
    "data_quality": {
      "status": "ok",
      "notes": [],
      "insufficient_data": []
    }
  },
  "llm_summary": null,
  "created_at": "2026-06-09T10:30:00Z",
  "updated_at": "2026-06-09T10:30:00Z"
}
```

- **主要欄位說明**
  - `review_result.lifecycle_review.classification.primary_label`：主要 lifecycle 分類，例如 `averaging_down_into_weakness`、`disciplined_scale_out`、`risk_reduction_exit`、`premature_scale_out`、`late_scale_out`、`coherent_position_management`、`insufficient_data`。
  - `review_result.lifecycle_review.classification.tier`：前端預設 summary 使用的 tier，例如 `needs_review`、`insufficient_context`、`constructive`、`mixed`。
  - `review_result.lifecycle_review.*.source_refs`：每段固定模板文字的來源指標、事件或分類 trace。前端可顯示來源，但不應要求使用者解讀 raw score。
  - `review_result.event_indicator_snapshots`：每個 entry/exit event 的 point-in-time 技術指標與 market regime snapshot，不包含完整 K 線序列。
  - `review_result.event_facts[].fees` / `taxes`：event ledger 中已保存或系統計算的成本事實；不表示本端點要求使用者手動輸入交易稅。
  - `review_result.shared_context` / `evidence_payload.shared_context`：每個事件的 shared context read payload，包含 `source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key` 與 `data_quality`；missing/stale/future-excluded 均非阻塞。
  - `review_result.decision_context.status`：`present` / `insufficient`。若為 `insufficient`，前端需明確提示不要推論未記錄意圖。
  - `review_result.decision_context.source` / `created_after_entry`：用於標示 plan provenance；`source = user_backfilled` 或 `created_after_entry = true` 時必須顯示事後補填 caveat，不可視為原始 entry-time intent。
  - `review_result.decision_context.planned_holding_period`、`default_stop_rule`、`add_entry_condition`：固定選項 plan facts，可被 deterministic lifecycle review 引用，但缺漏或 `not_recorded` 時不得用未記錄 intent 補判。
  - Phase E 已穩定的 lifecycle review labels 包含 `ma20_pullback_supported`、`add_entry_plan_violation`、`unacted_stop_rule_break`、`holding_period_needs_review`；這些 labels 需以 `reasons`、`caveats`、`source_refs` 追溯到 `event_facts`、`event_indicator_snapshots` 或 `decision_context`，不得使用未來資料批評 entry-time decision，也不得以 raw 0-100 score 作為預設主視覺。

> **Position Lifecycle Review 邊界**：本端點與 Single Trade Review 分離。`/portfolio/{portfolio_id}/review` 繼續代表 one sell decision；`/portfolio/groups/{position_group_id}/lifecycle-review` 代表 whole multi-entry/multi-exit lifecycle。兩者資料表、endpoint 與 review version 均不同。

### `DELETE /portfolio/{portfolio_id}`

- **用途**：刪除個人持股紀錄，並同步刪除該使用者該股票的 `daily_analysis_log`。
- **資料行為**：此端點仍為硬刪除，會刪除 `user_portfolio` 與對應的 `daily_analysis_log`，不同於結案端點。
- **權限邊界**：只能刪除目前登入使用者自己的持股；非擁有者回傳 `403`。
- **Response 204**：無 response body。

---

### Daily Radar endpoints

Daily Radar 是每日觀察雷達，用 rule-based 流程完成候選標的篩選、排序、bucket 分類與風險標籤。LLM 不參與候選標的選擇、排名、bucket 歸類或風險判斷。

Daily Radar run status：

- `completed`：執行完成，公開讀取 API 可回傳此 run。
- `running`：執行中，公開讀取 API 不回傳此 run。
- `failed`：執行失敗，公開讀取 API 不回傳此 run。
- `stale_data`：完成但資料日落後，公開讀取 API 可回傳此 run，前端需顯示資料新鮮度風險。

公開讀取 API 只暴露 `completed` 與 `stale_data` run。

#### `POST /internal/daily-radar/run`

- **用途**：供 GitHub Actions 或後端排程觸發 Daily Radar run。
- **Auth**：內部 token 必填，可使用 `Authorization: Bearer <DAILY_RADAR_INTERNAL_TOKEN>` 或 `X-Internal-Token`。
- **環境契約**：後端必須設定 `DAILY_RADAR_INTERNAL_TOKEN`。若後端未設定此 token，回傳 `503 Service Unavailable`。
- **Auth 錯誤**：request 未帶 token 時回傳 `401 Unauthorized`，並附 Bearer challenge；token 不符時回傳 `403 Forbidden`。
- **後端 orchestration**：live run 會自行選出 multi-track universe（保留 `same_day_institutional`、`recent_accumulation`，並加入本地 final `StockRawData` 可支撐的日頻技術 trigger tracks），對 selected symbols 補齊缺少的 OHLCV，建立固定 market index context，執行 Stage 1/2 rule-based scoring，然後持久化 run log 與 candidates。
- **Fixture fallback**：live run 關閉 fixture fallback，只使用 live provider 與既有 final `StockRawData`。
- **409 Conflict**：selected universe 為空，或嘗試 backfill 後 selected symbols 仍沒有 final `StockRawData` rows 時回傳。
- **公開 schema**：後端資料流改為自包含流程後，public Daily Radar read endpoints 與 candidate response schema 不變。
- **資料源 request budget**：
  - TWSE RWD institutional reports：目前 live provider 讀取 `TWT38U` / `TWT44U` fund reports 建立 same-day institutional 與 recent accumulation tracks。這是 report-level 查詢，不是 selected symbols 的逐檔法人 request。
  - yfinance selected-symbol OHLCV：只對 selected universe 中缺少 final raw row 的 symbols 做一次 batch download，區間 bounded by `run_date`，既有 final `StockRawData` 直接重用。
  - yfinance market index OHLCV：每次 run 只抓固定 benchmark。TW 使用 `TAIEX` / `^TWII`，US 使用 `SPX` / `^GSPC`，用於 market regime 與 relative strength benchmark。
  - Shared background context：daily run 只批次讀 `shared_background_contexts` 中 selected symbols 的 cache trace；weekly major holders、lending、full margin context 不在 daily run 主流程即時呼叫 provider。
  - Live limits：目前不抓完整 live margin。回填 rows 只放最小 margin `data_date`，避免技術與法人資料被誤判為 stale。

- **Request Body**

```json
{
  "run_date": "2026-06-02",
  "market": "TW"
}
```

- **欄位說明**
  - `run_date`：選填，Daily Radar run 日期，未提供時由後端使用當日日期。
  - `market`：選填，市場代碼，預設 `TW`。

- **Response 200**

```json
{
  "run_id": 123,
  "run_date": "2026-06-02",
  "market": "TW",
  "status": "completed",
  "universe_count": 82,
  "prefilter_count": 58,
  "candidate_count": 20,
  "errors": [],
  "started_at": "2026-06-02T12:30:00+00:00",
  "finished_at": "2026-06-02T12:31:45+00:00"
}
```

- **Response 欄位**

  | 欄位              | 類型   | 說明                                              |
  | ----------------- | ------ | ------------------------------------------------- |
  | `run_id`          | int    | Daily Radar run ID                                |
  | `run_date`        | string | run 日期                                          |
  | `market`          | string | 市場代碼，預設 `TW`                               |
  | `status`          | string | `completed` / `running` / `failed` / `stale_data` |
  | `universe_count`  | int    | Multi-track selected universe 標的數，會因軌道重疊去重而低於各軌 limit 加總 |
  | `prefilter_count` | int    | 通過前置條件的標的數                              |
  | `candidate_count` | int    | 產出候選標的數                                    |
  | `errors`          | array  | 執行期間累積的錯誤訊息                            |
  | `started_at`      | string | run 開始時間，ISO 8601                            |
  | `finished_at`     | string | run 結束時間，ISO 8601；執行中可為 `null`         |

#### `POST /internal/daily-radar/name-backfill`

- **用途**：正式機 maintenance endpoint，用於修復既有 Daily Radar rows 中 `name == symbol` 或空字串的顯示名稱。此流程由雲端 backend 使用正式環境的 `DATABASE_URL` 寫入正式 DB；本機 CLI 僅作除錯輔助。
- **Auth**：內部 token 必填，可使用 `Authorization: Bearer <DAILY_RADAR_INTERNAL_TOKEN>` 或 `X-Internal-Token`。
- **資料修復範圍**：更新 `daily_radar_candidates.name`，並同步修復相同 symbol 的 `stock_raw_data.technical.name`。公開 read endpoints 不做 live metadata resolver。
- **Request Body**

```json
{
  "limit": 1000,
  "dry_run": true
}
```

- `limit`：可省略；限制本次掃描的 candidate rows 數量。
- `dry_run`：預設 `false`。為 `true` 時只回報預計更新數量，不 commit 寫入。
- **Response 200**

```json
{
  "status": "completed",
  "dry_run": true,
  "scanned": 12,
  "updated_candidates": 10,
  "updated_raw_rows": 8,
  "unresolved_symbols": ["9999.TW"]
}
```

#### Public Daily Radar reads

公開讀取 API 不需要 `DAILY_RADAR_INTERNAL_TOKEN`。

- `GET /daily-radar/latest?market=TW&bucket=&limit=`：讀取指定市場最新可公開 run 的候選標的。
- `GET /daily-radar/{run_date}?market=TW&bucket=&limit=`：讀取指定日期與市場的候選標的。
- `GET /daily-radar/symbol/{symbol}?market=TW&bucket=&limit=&lookback_days=`：讀取指定標的的 Daily Radar 歷史。

- **Query 參數**
  - `market`：選填，預設 `TW`。
  - `bucket`：選填，只回傳指定 primary bucket 的候選標的。
  - `limit`：選填，限制回傳候選標的筆數。
  - `lookback_days`：選填，僅適用 symbol history，用於限制回看天數。

- **無資料行為**
  - `GET /daily-radar/latest`：沒有可公開 run 時回傳 `404`，message 需明確說明找不到 Daily Radar 結果。
  - `GET /daily-radar/{run_date}`：指定日期沒有可公開 run 時回傳 `404`，message 需明確說明該日期沒有 Daily Radar 結果。
  - `GET /daily-radar/symbol/{symbol}`：沒有歷史資料時回傳 `200`，候選資料為空陣列。

- **Candidate 欄位**

  | 欄位                | 類型           | 說明                          |
  | ------------------- | -------------- | ----------------------------- |
  | `symbol`            | string         | 股票代碼                      |
  | `name`              | string \| null | 持久化於 candidate 的顯示名稱；public read 不做 live metadata resolver，若 ingestion/backfill 當下未取得名稱可等於 `symbol` |
  | `primary_bucket`    | string         | 主要觀察分類                  |
  | `secondary_buckets` | array          | 次要觀察分類                  |
  | `observation_score` | number         | rule-based 內部排序分，用於排序、校準與 trace，不是勝率、推薦分數或預設前台 headline |
  | `risk_labels`       | array          | rule-based 風險標籤           |
  | `repeat_status`     | string \| null | 是否連續進入雷達或重新出現    |
  | `explanation`       | string         | 候選原因摘要                  |
  | `scoring_version`   | string \| null | scoring version trace，舊資料可為 `null` |
  | `rule_version`      | string \| null | rule version trace，舊資料可為 `null` |
  | `bucket_scores`     | object         | 各 bucket 的 rule-based 內部分數 |
  | `score_breakdown`   | object         | 分數拆解，用於 advanced trace / debug evidence；包含 bucket scores、cross confirmation、market context、relative strength、freshness、risk penalties、observation score 與 version trace |
  | `input_snapshot`    | object         | 產生候選時使用的輸入快照；包含 market context、relative strength、版本資訊與 replayable evidence |
  | `data_dates`        | object         | 各資料來源對應日期            |
  | `matched_rules`     | array          | 命中的 rule ID 或規則名稱     |
  | `background_context_labels` | array | Phase 2B shared background context labels，用於 Daily Radar detail surface，不參與分數或排序 |

  `name == symbol` 的既有 Daily Radar 資料需透過 `POST /internal/daily-radar/name-backfill` 主動修復；本機 `backend/scripts/backfill_daily_radar_symbol_names.py` 僅作除錯輔助。修復流程會更新 `daily_radar_candidates.name` 與 `stock_raw_data.technical.name`。公開讀取 API 不得為了補顯示名稱同步呼叫 TWSE/TPEX metadata provider。

- **Trace contract**
  - `input_snapshot.market_context` 至少可表示固定 benchmark 的 `regime`、`freshness`、`data_date`、均線位置、波動狀態與 risk flags。
  - `input_snapshot.background_context[]` 可表示 Phase 2A shared background context cache trace，包含 `context_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key`、`applicable_consumers` 與 `payload`。Missing/stale context 不改 `observation_score`、bucket、risk labels 或排序。
  - `background_context_labels[]` 由 background context trace 派生，包含 `context_type`、`label`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key` 與 `applicable_consumers`。目前 labels 包含 weekly major holders 背景持股集中脈絡、lending 借券空方壓力背景、full margin 完整融資融券背景。這些 labels 是 context/detail surface，不是交易 action、portfolio recommendation 或 score driver。
  - `score_breakdown.relative_strength` 表示 benchmark symbol、lookback window、candidate return、benchmark return、relative value、score impact、freshness、data dates、aligned dates 與 missing reason。資料不足時 `relative_value` 為 `null`，不可補 0 假裝中性。
  - `input_snapshot.evidence[]` 使用 consumer-neutral replayable evidence shape，包含 `evidence_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key`、`applicable_consumers` 與 `details`。Phase 1 僅 `daily_radar` consumer 使用。
  - Current version trace：`daily-radar-scoring-v2.1c` / `daily-radar-rules-v2.1c`。

- **Calibration workflow**
  - Daily Radar calibration report 可由 `uv run python scripts/daily_radar_calibration.py --source fixture --run-date 2026-05-29` 重跑。
  - Report 是 deterministic JSON，包含 sample count、bucket distribution、rank cutoff impact、bucket threshold impact、risk/overheat impact、relative strength impact、skip reasons 與 version manifest。
  - Calibration report 不改 live scoring 行為，不宣稱勝率、價格承諾或交易指令。

#### Internal Daily Radar chip context update

- **Endpoint**：`POST /internal/daily-radar/chip-context/update`
- **用途**：由 GitHub Actions 背景排程觸發，更新 `shared_background_contexts` cache。這是 weekly major holders、lending 與 full margin context 的正式背景更新路徑；daily run 和其他 analysis flows 不即時逐檔呼叫這些 provider。同一 `replay_key` 會 upsert，新的 `replay_key` 會保留為歷史 trace，供 point-in-time consumer 回放。
- **Auth**：沿用 Daily Radar internal token，可使用 `Authorization: Bearer <DAILY_RADAR_INTERNAL_TOKEN>` 或 `X-Internal-Token`。
- **Request Body**

```json
{
  "run_date": "2026-06-02",
  "market": "TW",
  "symbols": ["2330.TW", "2454.TW"],
  "context_types": ["weekly_major_holders", "lending", "full_margin"]
}
```

`symbols` 選填；未提供時 backend 以指定 market 最新可公開 Daily Radar run 的 candidates 作為 selected symbols。`context_types` 預設為 `weekly_major_holders`、`lending`、`full_margin`。

- **Response 200**

```json
{
  "status": "completed",
  "run_date": "2026-06-02",
  "market": "TW",
  "symbol_count": 2,
  "context_types": ["weekly_major_holders", "lending", "full_margin"],
  "records_written": 6,
  "errors": []
}
```

Provider failure 以 `status: "failed"` 與 `errors[]` 記錄，response 仍是 200，避免背景更新失敗阻塞 existing daily run。正式 workflow 為 `.github/workflows/daily-radar-chip-context.yml`，使用 `ZEABUR_BACKEND_URL` 與 `DAILY_RADAR_INTERNAL_TOKEN` secrets，不硬編 secret；workflow 會檢查 response JSON 的 `status == "completed"`，若為 failed 或 non-JSON response 會 fail job 以利排程監控。Workflow 以資料頻率拆分 request body：台灣時間週二至週六 07:00 更新 `lending` / `full_margin`，台灣時間週日 07:30 更新週頻 `weekly_major_holders`。

> **Daily Radar 邊界**：Daily Radar 是 deterministic rule-based 觀察清單。它可整理觀察理由與風險標籤，但不產生交易指令，也不讓 LLM 決定候選標的、排序、bucket 或風險。Raw scores 保留於 API 作為內部排序、校準、回測與 traceability；一般使用者介面應優先顯示觀察等級、bucket、風險標籤與命中原因，若顯示 `observation_score` 應標示為內部排序分，不得稱為勝率、推薦分數或保證性結果。

---

## 4) 錯誤碼表（`errors[]`）

`errors` 為陣列，每筆格式如下：

```json
{
  "code": "ERROR_CODE",
  "message": "human readable message"
}
```

目前錯誤碼定義：

- `ANALYZE_RUNTIME_ERROR`：graph 執行期間拋出未預期例外
- `MISSING_SNAPSHOT`：graph 最終 state 缺少有效 `snapshot`
- `MISSING_ANALYSIS`：graph 最終 state 缺少有效 `analysis`
- `CRAWL_ERROR`：`crawl_node` 抓取股票快照失敗（yfinance 例外）
- `RSS_FETCH_ERROR`：`fetch_news_node` 抓取 RSS 新聞失敗（網路例外）
- `CLEAN_ERROR`：`clean_node` 呼叫新聞清潔器失敗（LLM 或 heuristic 例外）
- `TECHNICAL_CALC_ERROR`：`fetch_technical_node` 計算技術指標失敗（yfinance / Pandas 例外）
- `INSTITUTIONAL_FETCH_ERROR`：`fetch_institutional_node` 抓取法人籌碼資料失敗（API 不可用或網路例外）
- `CROSS_VALIDATION_ERROR`：`analyze_node` 執行多維交叉驗證失敗
- `INVALID_ENTRY_PRICE`：`entry_price` 為負數或零（`/analyze/position` 專屬）
- `POSITION_SCORE_ERROR`：`PositionScorer` 計算倉位位階或移動停利失敗（`/analyze/position` 專屬）

---

## 5) 驗證錯誤（422）

當 request body 不符合 schema（例如 `symbol` 為空字串），API 會回傳 `422 Unprocessable Entity`。

---

## 6) 測試對應

- 測試檔：`backend/tests/test_api.py`
- 覆蓋項目：
  - 健康檢查
  - 分析成功路徑（snapshot + analysis）
  - `technical_indicators` 對外欄位，包含布林通道、MACD、KD、ADX、OBV
  - 有 `cleaned_news` 的成功路徑
  - `raw_news_items` 不對外暴露
  - 請求驗證錯誤（422）
  - graph 執行期例外 → `ANALYZE_RUNTIME_ERROR`
  - graph 最終 state 缺 snapshot/analysis → `MISSING_SNAPSHOT` / `MISSING_ANALYSIS`
  - graph 執行期累積的 errors 傳遞到 response
- 測試檔（持股 API）：`backend/tests/test_api.py`
- 覆蓋項目（持股 API）：
  - 持股診斷成功路徑（`position_analysis` 物件完整性）
  - position L1 快取需比對 `entry_price` / `entry_date` / `quantity`，不同成本基準不可命中舊診斷
  - `entry_price` 為負數 → `422` + `INVALID_ENTRY_PRICE`
  - `flow_label = distribution` 且獲利中 → `recommended_action = Trim`、`exit_reason` 非 null
  - `position_status = under_water` 且 `profit_loss_pct < -10%` → `recommended_action = Exit`
  - `PositionScorer` 計算失敗 → `POSITION_SCORE_ERROR`（流程繼續，`position_analysis` 降級為 null）
- 測試檔（持股規則）：`backend/tests/test_position_scorer.py`
- 覆蓋項目（持股規則）：
  - KD / ADX / OBV / MACD / 布林位置會參與持股 `Trim` / `Exit` 判斷
  - 獲利狀態不再因成本價低於支撐位而誤判為 `under_water`
  - 獲利分層與量價轉弱會調整 `trailing_stop`
- 測試檔（個人持股）：`backend/tests/test_portfolio_router.py`
- 覆蓋項目（個人持股）：
  - `POST /portfolio` 在 active 持股數小於 8 時可新增
  - active 持股數達 8 筆時回傳 `422`，錯誤訊息包含 `8`
  - `PUT /portfolio/{id}` 僅允許持股擁有者更新
  - `DELETE /portfolio/{id}` 僅允許持股擁有者刪除
- 測試檔（LLM input contract）：`backend/tests/test_graph_nodes.py`、`backend/tests/test_langchain_analyzer.py`
- 覆蓋項目（LLM input contract）：
  - `analyze_node` 傳入 `signal_summary`，且摘要包含 KD / ADX / OBV 與 rule-based labels
  - analyzer prompt 將 `signal_summary` 放在優先閱讀區，並保留 `position_context` / `prev_context` 可選參數
