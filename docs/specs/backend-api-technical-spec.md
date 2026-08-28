# AI Stock Sentinel 後端 API 技術規格（v5）

> 類型：技術文件（Technical Doc）
> 更新日期：2026-08-03
> 更新摘要：2026-08-28 起 `/analyze` 與 `/analyze/position` 完全移除 LLM 與 RSS 新聞執行路徑，改為 crawl → external data → judge → preprocess → score → strategy 的 deterministic contract。`persist_result` 控制是否讀寫完整分析快取；`skip_ai` 與 `news_text` 只保留 deprecated request 相容。舊 LLM/news response 欄位暫留但固定為空值，舊快取也不得重新送出歷史 AI 內容。本文後段若仍提及 LLM prompt 或 cleaner，視為歷史設計而非現行 runtime contract。
> Cache 邊界：deterministic contract 的 `STRATEGY_VERSION` 為 `2.0.0`。`1.0.0` 與更舊的當日快取可能含新聞／LLM 派生的 confidence、strategy、action plan 或 position recommendation，必須視為版本失效並重跑，不得只清空顯示欄位後沿用派生決策。
> Release Gate 仍包含 portfolio risk data-gap、Determinism Gate、Shared Context Gate 與 Copy Guard Gate；本次移除模型不得放寬既有投資紀律邊界。
> Technical profile v3 保留純量化的 `ma20_slope_pct_5d`、`ma60_slope_pct_10d`、`macd_hist_slope_pct_3d`、`macd_hist_trend`、`atr_pct_percentile_60d`、`bollinger_bandwidth_percentile_60d` 與 profile 內的 `temporal_evidence`；移除跨指標綜合判斷 `volatility_regime`、`technical_conflicts`、`signal_conflicts`。新增時序欄位目前全為 evidence-only、`impact=0`，不得改變 `score_summary`；盤中若無日期可證明 completed bars，temporal evidence 必須 fail closed。

## 1) 目的

本文件定義目前後端 API 的實作契約與錯誤碼，供前後端串接、測試與除錯使用。

---

## 2) 服務啟動

```bash
cd backend
make run-api
```

預設位址：`http://127.0.0.1:8000`

所有支援的 API 啟動入口都必須先執行 Alembic upgrade 並採 fail-closed；migration 缺少人工確認、逾時或執行失敗時不得進入 FastAPI lifespan 的 serving 階段。Production prestart 另以 `alembic current --check-heads` 驗證目前資料庫位於唯一 head。

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

### `POST /auth/google/code`

- **用途**：以 Google OAuth authorization code 完成登入並換取應用程式 JWT。
- **前端 CSRF 邊界**：redirect request 必須包含密碼學安全的一次性 `state`；callback 在呼叫本端點前需從同一分頁 `sessionStorage` 取出預期值，比對後立即消耗。缺少、不相符或重放的 state 不得送出 code exchange。
- **Redirect URI 邊界**：後端在向 Google token endpoint 送出請求前，先驗證 `redirect_uri`。若設定 `GOOGLE_OAUTH_REDIRECT_URIS`，只接受逗號分隔清單中的精確 URI；未設定時只接受 `CORS_ORIGINS` 的可信 origin，且 path 必須以 `/login/callback` 結尾，不可包含 query、fragment、credentials 或 `..` path segment。
- **錯誤行為**：無效 code、未允許的 redirect URI 或 Google token exchange 失敗均回傳 `401`，不建立應用程式 JWT。

### Phase 1 Daily AVWAP backend foundation（internal service，無公開 endpoint）

- **用途**：建立 Phase 1 日頻 AVWAP snapshot cache，供後續 `/analyze`、Portfolio risk summary 與 Daily Radar response projection 讀取。
- **Managed universe**：只合併目前登入使用者的 active holdings、watchlist symbols，以及 latest public Daily Radar selected candidates；任意 Analyze symbol 不會在 Phase 1A 觸發 historical backfill。
- **資料來源**：TWSE 上市 `.TW` 預設使用 `STOCK_DAY` single-symbol monthly query，逐月補齊 requested lookback window；上櫃 `.TWO` 保留 FinMind `TaiwanStockPrice` fallback。`adjustment_mode = "unadjusted"`，不使用 `TaiwanStockPriceAdj` 作為預設。
- **快取表**：`phase1_avwap_snapshots`，以 `symbol` / `data_date` / logical `dataset = "phase1_daily_ohlcv_amount"` / `adjustment_mode` 唯一 upsert。此表是全域市場 cache，只保存 market bars、generic anchors、data quality 與 source trace，不保存任何使用者持股 `entry_date`、`avg_cost` 或 holding-specific entry anchor。fresh snapshot 會先被重用，缺漏或 stale 才逐檔 fetch。
- **更新路徑**：Daily Radar `refresh-avwap` step 會讀 `daily_radar_prepared_runs.selected_symbols`，再合併 active holdings 與 watchlist symbols 刷新當日 AVWAP snapshot；只會送 `.TW` / `.TWO` 進 provider，其他 symbol 以 `skipped_symbol_reasons.unsupported_phase1_avwap_market` 記錄，不算 missing。Analyze、Portfolio、public Daily Radar read path 與 `run-scoring` 只讀 snapshot，不觸發 refresh。
- **計算契約**：日頻 AVWAP 使用 source traded amount / volume。TWSE 對應 `成交金額 / 成交股數`，FinMind fallback 對應 `Trading_money / Trading_Volume`；若 source row 缺 amount 才用 typical price × volume fallback，且對應 anchor / data quality 必須標記 `estimated = true`。最新 source row 的交易日必須等於 requested `data_date` 才能寫成 `fresh` snapshot；若 provider 只回到較早交易日，應寫 `freshness = "missing"` 與 `missing_reason = "daily_price_row_missing_for_data_date"`，不得把前一交易日資料標成當日 final。
- **資料品質**：provider/quota/row 缺漏不得產生假中性 AVWAP；應寫入 `freshness = "missing"` 與 `missing_reason`，讓 1B/1C 以 caveat 顯示。
- **公開 API 狀態**：不新增 public endpoint；只投影到既有 `/analyze`、Portfolio risk summary 與 Daily Radar response。

### `POST /analyze`

- **用途**：執行確定性股票分析流程（LangGraph：crawl → fetch_external_data → judge → preprocess → score → strategy）
- **產品語義**：此端點對應 Analyze 頁的「新倉策略建議」，用於評估是否值得觀察、等待與建立新倉；**不是**持股中的續抱 / 減碼 / 出場指令端點
- **FinMind 付費資料邊界**：`TaiwanStockHoldingSharesPer` 只開放 backer/sponsor 會員，預設不發送該 request，相關大戶／散戶持股分級欄位維持 `null`。只有部署端明確設定 `FINMIND_HOLDING_SHARES_PER_ENABLED=true` 且 token 具相應權限時才啟用，避免一般方案對每次分析固定產生 HTTP 400。

- **Request Body**

```json
{
  "symbol": "2330.TW",
  "persist_result": true
}
```

- **欄位說明**
  - `symbol`：股票代碼，必填；目前只接受台灣上市 `.TW` 與上櫃 `.TWO`，輸入會去除前後空白並轉大寫，其他市場回 422。
  - `persist_result`：是否讀寫完整分析快取與歷史紀錄，選填，預設為 `true`。Watchlist/Portfolio 快查使用 `false`。
  - `news_text`、`skip_ai`：deprecated 相容欄位；不進入 graph。未提供 `persist_result` 時，舊 `skip_ai: true` 仍映射為不持久化。

- **Response 200（成功/可降級成功）**

```json
{
  "snapshot": {
    "symbol": "2330.TW",
    "currency": "TWD",
    "current_price": 925.0,
    "market_current_price": 925.0,
    "market_current_price_source": "twse_mis",
    "price_limit_quote_price": 925.0,
    "previous_close": 920.0,
    "day_open": 921.0,
    "day_high": 928.0,
    "day_low": 918.5,
    "price_limit_status": "normal",
    "limit_up_price": 1010.0,
    "limit_down_price": 828.0,
    "volume": 28450000,
    "recent_closes": [910.0, 915.0, 920.0, 925.0],
    "data_dates": {"ohlcv": "2026-03-03"},
    "fetched_at": "2026-03-03T00:00:00+00:00",
    "support_20d": 900.0,
    "resistance_20d": 950.0
  },
  "analysis": "",
  "cleaned_news": null,
  "news_display": null,
  "cleaned_news_quality": null,
  "data_confidence": 100,
  "signal_confidence": 72,
  "confidence_score": 78,
  "cross_validation_note": "技術面與籌碼面訊號一致，信心偏高",
  "analysis_detail": null,
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
  "technical_profile": {
    "version": "technical-layer-v3",
    "primary_score_inputs": {
      "ma_structure": {
        "state": "bullish_alignment",
        "impact": 2,
        "reason": "close > MA5 > MA20"
      },
      "support_resistance": {
        "state": "range_mid",
        "impact": 0,
        "reason": "price is between support and resistance"
      }
    },
    "risk_overheat_filters": {
      "rsi_state": {
        "state": "not_overheated",
        "impact": 0,
        "reason": "RSI below overheat threshold"
      }
    },
    "secondary_evidence": {
      "kd": {
        "state": "overbought",
        "impact": -1,
        "reason": "KD is in high zone"
      }
    },
    "display_only": {
      "bollinger_upper": 932.41,
      "bollinger_mid": 905.2,
      "bollinger_lower": 878.0
    },
    "score_summary": {
      "primary_score": 2,
      "risk_filter_score": 0,
      "secondary_score": -1,
      "capped_total": 1,
      "technical_score": 53
    },
    "data_quality": {
      "data_date": "2026-03-03",
      "is_final": true,
      "lookback_days_available": 60,
      "required_lookback_days": 60,
      "ohlcv_aligned": true,
      "volume_aligned": true,
      "price_level_basis": "ohlc_high_low",
      "missing_fields": []
    },
    "formula_versions": {
      "metrics": "technical-metrics-v3",
      "layering": "technical-layer-v3"
    },
    "companion_context_refs": {
      "chip_stability_context": "tdcc_weekly_major_holders"
    },
    "caveats": []
  },
  "chip_stability_context": {
    "version": "chip-stability-context-v1",
    "source": "tdcc_weekly_major_holders",
    "status": "fresh",
    "as_of_date": "2026-06-21",
    "previous_as_of_date": "2026-06-14",
    "thousand_lot_holder_ratio": 48.12,
    "thousand_lot_holder_ratio_delta_pp": 0.36,
    "state": "stable",
    "trend": "improving",
    "summary": "千張大戶持股比例增加，籌碼穩定性提升。",
    "caveats": [
      "TDCC 週頻資料只作籌碼穩定性補充，不納入 technical score。"
    ]
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

`price_limit_status` 為 `limit_up`、`limit_down`、`normal` 或 `unknown`。Analyze／Watchlist 個股查詢會在 response projection 統一補上漲跌停 context，因此新抓 snapshot、10 分鐘 raw cache 與完整分析 cache 命中都使用相同契約：後端必須使用同一筆 TWSE MIS 回傳的成交價 `z` 與官方上下限 `u`／`w` 判斷，並以獨立的 `market_current_price`、`market_current_price_source = "twse_mis"` 與 `price_limit_quote_price` 揭露即時市場報價。Canonical `snapshot.current_price` 不覆寫，因 technical indicators 與 technical profile 仍以該次 yfinance/raw snapshot 計算；前端需把 MIS 價明示為即時顯示值，不得暗示既有技術指標已隨之重算。不得以前一交易日收盤價直接乘上 110%／90% 推算。此 optional provider 使用 bounded worker，response 最多等待 500ms；provider socket／total reader deadline 同為 500ms，reader 使用 available-byte `read1`（fallback 單 byte read）定期重查 wall clock，response body 上限 64 KiB，避免單次填滿 buffer 的 blocking read 讓慢速串流長期占滿 worker。官方端未提供即時成交價／上下限、容量已滿或查詢失敗時回傳 `unknown`，且保留原 snapshot 現價，不得中斷個股分析主流程。Provider/display-only 欄位不進 graph、technical scoring、Portfolio 純價格刷新或內部 raw-data 持久化。

- **欄位說明**

  | 欄位                       | 類型           | 說明                                                                                                                                                                                                                                                                                            |
  | -------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `snapshot`                 | object         | yfinance 即時快照；`data_dates.ohlcv` 為 history 中最後一筆有效 close 的實際交易日，供 raw storage 與 deterministic point-in-time replay 使用                                                                                                                                                   |
  | `analysis`                 | string         | 已停用的舊 LLM 相容欄位；固定為空字串                                                                                                                                                                                                                                                          |
  | `cleaned_news`             | object \| null | 已停用的舊新聞相容欄位；固定為 null                                                                                                                                                                                                                                                            |
  | `symbol_name`              | string \| null | 股票名稱，僅供前端顯示；新鮮分析由 `snapshot.name` 浮出，舊快取可由 symbol metadata resolver 補齊，查不到時為 `null`                                                                                                                                                                                |
  | `news_display`             | object \| null | 已停用的舊新聞顯示欄位；固定為 null                                                                                                                                                                                                                                                            |
  | `cleaned_news_quality`     | object \| null | 已停用的舊新聞品質欄位；固定為 null                                                                                                                                                                                                                                                            |
  | `data_confidence`          | int \| null    | 0–100，現行 runtime 依法人籌碼與技術資料兩個啟用維度計算（0 / 50 / 100）；availability 與方向標籤分離，缺資料時的 `neutral` / `sideways` fallback 不得算成已取得。技術維度至少需要 20 筆有效收盤資料；僅產出 `technical_profile.score_summary`、但 `data_quality.lookback_days_available < 20` 時仍視為不可用，且該 profile 不得影響 `signal_confidence`。若 raw closes 已達 20 筆則維度仍可用，但必須忽略不足 lookback 的 profile，改由 raw technical fallback 產生訊號。歷史三維 replay 仍保留原始 denominator，避免改寫既有校準樣本 |
  | `signal_confidence`        | int \| null    | 0–100，內部訊號強度（CS-4 新增；`confidence_score` 為向後相容別名），用於 guardrail、校準與 trace                                                                                                                                                                                                 |
  | `confidence_score`         | int \| null    | 0–100，有方向的內部訊號強度（= `signal_confidence`，向後相容；50 為中性基準，低於 50 偏空、高於 50 偏多）；不得解讀為不分方向的一致性、勝率或機率                                                                                                                                                    |
  | `cross_validation_note`    | string \| null | 技術面與籌碼面的交叉驗證結論簡述（rule-based 固定字串）                                                                                                                                                                                                                                       |
  | `strategy_type`            | enum \| null   | `short_term` / `mid_term` / `defensive_wait`                                                                                                                                                                                                                                                    |
  | `entry_zone`               | string \| null | 建議入場區間（rule-based）                                                                                                                                                                                                                                                                      |
  | `stop_loss`                | string \| null | 防守底線／停損條件（rule-based）                                                                                                                                                                                                                                                                |
  | `holding_period`           | string \| null | 預期持股期間（rule-based）                                                                                                                                                                                                                                                                      |
  | `analysis_detail`          | object \| null | 已停用的舊 LLM 結構化欄位；固定為 null                                                                                                                                                                                                                                                        |
  | `technical_indicators`     | object \| null | 技術指標顯性輸出，包含布林通道、MACD、KD、ADX、OBV、ATR、MFI、Donchian Channel 數值與標籤（詳見下方 `technical_indicators` 欄位說明）                                                                                                                                                           |
  | `technical_profile`        | object \| null | Canonical technical profile，將 raw 指標投影為 primary scoring inputs、risk overheat filters、secondary evidence、display-only values、score summary 與 data quality；後端 scoring 與前端分層摘要優先使用此欄位，raw `technical_indicators` 保留相容與 copy/export |
  | `chip_stability_context`   | object \| null | TDCC 週頻千張大戶持股比例變化產生的籌碼穩定性補充；只提供 state/trend/summary/caveats，不給分、不進 technical score、不改 Daily Radar ranking，也不作為單獨看空/看多判斷 |
  | `sentiment_label`          | string \| null | 已停用的舊消息面相容欄位；固定為 null                                                                                                                                                                                                                                                          |
  | `action_plan`              | object \| null | rule-based 新倉戰術行動計劃（含 `action` / `target_zone` / `defense_line` / `momentum_expectation` / `breakeven_note` / `conviction_level` / `thesis_points` / `upgrade_triggers` / `downgrade_triggers` / `invalidation_conditions` / `suggested_position_size`）；前端主要呈現應改用 risk-language 欄位 |
  | `shared_context`           | object \| null | Phase 2C shared background context read payload；只作 evidence/caveat 與資料完整度 trace，不參與核心數值計算、ranking、bucket、`action_plan` 或 rule-based 欄位覆寫 |
  | `phase1_observation`       | object \| null | Phase 1 Daily AVWAP snapshot read projection；只讀 `phase1_avwap_snapshots`，不進入 Graph，也不觸發 provider backfill。Out-of-universe 回 `missing_reason = "not_in_phase1_universe"`；managed universe 內但未有 snapshot 回 `missing_reason = "phase1_snapshot_missing"`；若最新 snapshot 超過 7 個 calendar days 回 `missing_reason = "phase1_snapshot_stale"`；snapshot read failure 回 `missing_reason = "phase1_snapshot_read_failed"` |
  | `data_sources`             | array          | 本次實際成功取得資料的來源列表（如 `["yfinance", "twse-openapi"]`）                                                                                                                                                                                                                          |
  | `institutional_flow_label` | enum \| null   | 籌碼歸屬標籤：`institutional_accumulation` / `retail_chasing` / `distribution` / `neutral`                                                                                                                                                                                                      |
  | `action_plan_tag`          | enum \| null   | 燈號標籤（rule-based，後端計算）：`opportunity` / `overheated` / `neutral`；前端僅做顯示映射                                                                                                                                                                                                    |
  | `risk_state`               | string \| null | 研究/紀律語言的 setup 或風險狀態；前端 primary copy 使用                                                                                                                                                                         |
  | `risk_state_label`         | string \| null | `risk_state` 的可讀標籤                                                                                                                                                                                                           |
  | `discipline_triggers`      | array          | 紀律觸發條件；前端 primary copy 使用                                                                                                                                                                                             |
  | `observation_conditions`   | array          | 觀察條件；前端 primary copy 使用                                                                                                                                                                                                 |
  | `risk_control_reference`   | object \| null | 風險控制參考線或參考條件                                                                                                                                                                                                          |
  | `command_language_deprecated` | object       | legacy/internal compatibility 欄位集合；不得作為 primary user-facing copy                                                                                                                                                        |
  | `errors`                   | array          | 錯誤碼陣列                                                                                                                                                                                                                                                                                      |

> **策略產生邊界（`POST /analyze`）**：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`、`action_plan`、`action_plan_tag`、risk language 與信心分數皆由後端 Python rule-based 邏輯產出。Primary user-facing copy 使用 `risk_state`、`discipline_triggers`、`observation_conditions` 與 `risk_control_reference`；`entry_zone`、`stop_loss` 與 `action_plan.action` 保留為相容/trace 欄位。

> **Shared context read contract（Phase 2C）**：`shared_context` 由 `shared_background_contexts` cache 以 selected symbol 批次/單檔讀取產生，欄位包含 `version`（目前 `shared-context-read-v1`）、`symbol`、`consumer`、`contexts[]`、`caveats[]` 與 `data_quality`。`contexts[]`/`caveats[]` 使用 consumer-neutral 欄位：`context_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key`、`applicable_consumers`；read path 會尊重 `applicable_consumers`，若 cache row 不適用目標 consumer，會回傳 non-blocking `context_not_applicable_to_consumer` caveat。資料缺漏或 stale 時以 caveat 呈現且 `data_quality.blocking=false`。此 payload 在 response 組裝階段附加，不進入 LangGraph initial state，不觸發 weekly major holders、lending、full margin 的即時逐檔昂貴查詢。

> **Chip stability context（2026-06-23）**：`chip_stability_context` 是從 `weekly_major_holders` shared context 派生的 response-only companion。它讀取 TDCC 千張大戶持股比例與前期差異，增加代表籌碼穩定性提升，連續增加代表籌碼愈加穩定；下降代表籌碼穩定性轉弱或集中度下降，但必須帶 caveat，不能單獨判定看空。此欄位不進入 LangGraph initial state、LLM prompt、`technical_indicators` 分數、Daily Radar ranking driver、portfolio risk score 或 action/verdict/classification 覆寫。

> **Canonical technical profile（2026-06-24）**：`technical_profile` 由 `backend/src/ai_stock_sentinel/technical/` 的 canonical metrics/profile builder 產生，Analyze、`persist_result: false` Watchlist quick lookup、`/analyze/position` 與 Daily Radar 共用同一套公式。`technical_profile.version` 目前為 `technical-layer-v3`；`score_summary.technical_score = round(50 + capped_total * (17 / 5))`，cap 或映射公式變更時必須升級版本並更新測試 fixture。`primary_score_inputs` 只放方向與可操作性核心證據，例如均線結構、支撐壓力、量能參與、MACD、OBV 與 ATR 支撐距離；`risk_overheat_filters` 只放過熱或高波動懲罰，例如 RSI、BIAS、Bollinger 與 ATR 高波動；`secondary_evidence` 只作輔助，不主導 primary score；`display_only` 保存 raw/display values，不影響 `score_summary`。支撐壓力 primary scoring 必須用當前 bar 之前的 20 根已完成 bar 判斷 breakdown/near-support/near-resistance；raw `technical_indicators.high_20d` / `low_20d` 可作 display 數值並包含當前 bar，但不得直接拿來判斷當前 close 是否跌破支撐。`atr_risk` 與 `atr_state` 必須分離，前者只回答支撐/停損距離是否可控，後者才處理高波動懲罰，避免 ATR 重複計票。`data_quality` 必須含 `data_date`、`is_final`、lookback coverage、OHLCV/volume 對齊狀態、`price_level_basis` 與 `missing_fields`；OHLC high/low 不完整時，支撐壓力 primary signal 應以 missing/caveat 呈現，不計主要分。`required_lookback_days` 是 profile v3 的最低完整判斷門檻，較長週期訊號需在各 signal state/reason/caveats 或 `missing_fields` 中標示不足，不得只用全域 lookback 判定所有欄位完整。`chip_stability_context` 只能透過 `companion_context_refs` 關聯，不得進入任何 technical bucket 或 `score_summary`。

> **Phase 1 AVWAP Analyze projection（Phase 1B）**：`phase1_observation` 由 `phase1_avwap_snapshots` 以目前台北日期、登入使用者 managed universe 與 symbol 讀取。Analyze read path 可使用 requested date 當日或以前最新 fresh snapshot，最多回看 7 個 calendar days，避免台北日期已跨日但正式 snapshot 停在上一個交易日時誤判缺資料；response 會同時保留 snapshot `data_date` 與 `requested_data_date`。此欄位只作 evidence/data-quality trace，不進入 LangGraph initial state，不觸發 provider 即時查詢，也不擴張 managed universe。Snapshot 命中時回傳 AVWAP anchors、`freshness`、`missing_reason`、`source` 與 `data_quality`；每個 anchor 的 `distance_to_avwap_pct` 代表 `snapshot_close` 相對 AVWAP 的資料日距離，並以 `distance_basis = "snapshot_close"` 標示。Analyze read projection 會額外以當次 `snapshot.current_price` 產生 `current_distance_to_avwap_pct`、`current_price` 與 `current_distance_basis = "analyze_current_price"`，供 Analyze / Watchlist / copy-to-AI 顯示目前價格相對 AVWAP 的距離；這些 current 欄位只存在 response projection，不寫回 shared `phase1_avwap_snapshots` payload。未命中、過期或讀取失敗時用 non-blocking missing payload 表示，且不得讓 `/analyze` 主流程失敗。

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
> | `avg_volume_20` / `avg_volume_60`                       | number \| null | 近 20／60 個交易日平均成交量；收盤資料包含當日，盤中資料排除尚未完成的當日，lookback、成交量日期或 finite 檢查不足時回傳 `null`；兩欄位屬於 display-only，不改變既有 `volume_ratio` 與技術評分公式 |
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
> | `ma20_slope_pct_5d` / `ma60_slope_pct_10d`              | number \| null | 均線在指定回看期的百分比變化；只作時序 evidence                                                        |
> | `macd_hist_slope_pct_3d` / `macd_hist_trend`             | number/string   | MACD 柱體相對價格正規化的 3 日斜率，以及擴張/收斂狀態                                                   |
> | `atr_pct_percentile_60d`                                | number \| null | ATR% 在最近最多 60 個可計算觀察值中的 percentile rank                                                  |
> | `bollinger_bandwidth_percentile_60d`                    | number \| null | 布林帶寬在最近最多 60 個可計算觀察值中的 percentile rank                                                |

> **相容策略**：`technical_indicators` 仍是公開 response 欄位與 copy/export raw data 來源，不可因 `technical_profile` 上線而移除。舊 cache 若缺 `technical_profile` 但 snapshot 仍足以重建 profile，read path 可 backfill projection；若無法建立 profile，前端 fallback 到 legacy raw 指標顯示，不顯示分層結論。

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
  "cross_validation_note": "技術面與籌碼面訊號一致，信心偏高",
  "analysis_detail": null,
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
  "data_sources": ["yfinance", "finmind"],
  "errors": []
}
```

- **欄位說明**

  | 欄位                       | 類型           | 說明                                                                                             |
  | -------------------------- | -------------- | ------------------------------------------------------------------------------------------------ |
  | `snapshot`                 | object         | yfinance 即時快照（與 `/analyze` 相同）                                                          |
  | `position_analysis`        | object         | **持股診斷專屬**——見下方欄位細節                                                                 |
  | `data_confidence`          | int \| null    | 0–100，資料完整度；技術維度至少需要 20 筆有效收盤資料，不能以不足 lookback 所產生的 fallback score 充當可用資料；前端預設應轉成資料品質提示 |
  | `signal_confidence`        | int \| null    | 0–100，內部訊號強度，用於 guardrail、校準與 trace                                                  |
  | `confidence_score`         | int \| null    | = `signal_confidence`，向後相容；不應作為預設前台 headline                                        |
  | `cross_validation_note`    | string \| null | 技術面與籌碼面的交叉驗證結論（rule-based 固定字串）                                              |
  | `analysis_detail`          | object \| null | 已停用的舊 LLM 相容欄位；固定為 null                                                             |
  | `technical_indicators`     | object \| null | 技術指標顯性輸出（與 `/analyze` 相同，包含布林通道、MACD、KD、ADX、OBV，供前端技術指標卡片使用） |
  | `technical_profile`        | object \| null | Canonical technical profile（與 `/analyze` 相同）；持股診斷可用作技術 score/trace，但不得覆寫 position-specific rule-based 欄位 |
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

> **Critical label 語義**：`risk_state = critical` 的通用 `risk_state_label` 使用「風險檢查已觸發」，不得一律寫成「防守條件已觸發」。`critical` 可能來自籌碼出貨、深度套牢、技術轉弱或跌破動態防守線；實際原因必須讀 `discipline_triggers` 與 `risk_control_reference`。Portfolio history 讀取舊 `position_risk_language` 快照時，需將既有「防守條件觸發／防守條件已觸發」正規化為新標籤。Portfolio risk-summary 的「觸及防守」則是另一個即時狀態，只在 `current_price <= defense_reference.price` 時成立。

> **Shared context 邊界（Phase 2C）**：`/analyze/position` 的 `shared_context.consumer = "position_analysis"`。Shared context 只由 shared cache 讀取並附加於 response，作為 weekly major holders、lending、full margin 等背景 caveat 與資料品質說明；它不進入 position scorer，不改 `position_status`、`recommended_action`、`trailing_stop`、`trailing_stop_reason`、`exit_reason` 或任何持股診斷 rule-based 欄位。Missing/stale context 非阻塞，必須以 `freshness` / `missing_reason` / `data_quality` 表示。

> **快取隔離與邊界**：
>
> - `/analyze` 使用 `analysis_type="general"`，`/analyze/position` 使用 `analysis_type="position"`。
> - 快取鍵值包含 `symbol`、`record_date` 與 `analysis_type`，確保不同分析類型互不覆寫。
> - **持股診斷快取邊界**：`/analyze/position` 的 L1 full_result 快取命中必須比對 `entry_price` / `entry_date` / `quantity`。同一檔股票若成本價、日期或數量不同，會強制重跑持股診斷，避免回傳其他成本基準的 `position_analysis`。

---

### `GET /watchlist`

- **用途**：列出目前登入使用者的關注股票清單。關注列表保存「有興趣但尚未進入持股」的觀察標的，不代表進場、持股、加碼或交易紀錄。
- **Auth/Ownership**：需要登入，只回傳 `current_user.id` 的資料。
- **排序**：`sort_order ASC`，同序時以 `created_at DESC`、`id DESC` 作 deterministic fallback。
- **Response 200**

```json
[
  {
    "id": 1,
    "symbol": "2330.TW",
    "name": "台積電",
    "notes": "等待拉回 MA20",
    "sort_order": 0,
    "created_at": "2026-06-17T03:00:00+00:00",
    "updated_at": "2026-06-17T03:00:00+00:00"
  }
]
```

### `POST /watchlist`

- **用途**：新增關注股票，供 `/watchlist`、Analyze 結果與 Daily Radar 候選清單使用。
- **Auth/Ownership**：需要登入，新項目會寫入 `current_user.id`。
- **排序語義**：新項目會排在目前登入使用者關注列表最後。
- **Request Body**

```json
{
  "symbol": "2330.TW",
  "notes": "等待拉回 MA20"
}
```

- **欄位說明**
  - `symbol`：股票代碼，必填，長度 1-20；後端會 `trim()` 並轉大寫，例如 `2330.tw` 會保存為 `2330.TW`。
  - `notes`：觀察備註，選填，最多 500 字；空白字串會正規化為 `null`。
- **唯一性 / 冪等語義**：同一使用者同一 `symbol` 只有一筆。新建成功回傳 `201`；若已存在，回傳既有項目並把 status code 設為 `200`。若 request 明確帶 `notes` 且內容不同，會更新既有項目的備註與 `updated_at`。
- **驗證**：後端會檢查股票代碼是否存在；不存在時回傳 `404`。
- **Response 201 / 200**

```json
{
  "id": 1,
  "symbol": "2330.TW",
  "name": "台積電",
  "notes": "等待拉回 MA20",
  "sort_order": 0,
  "created_at": "2026-06-17T03:00:00+00:00",
  "updated_at": "2026-06-17T03:00:00+00:00"
}
```

### `PUT /watchlist/{item_id}`

- **用途**：更新目前登入使用者某筆關注項目的觀察備註。
- **Auth/Ownership**：需要登入，且只能更新 `current_user.id` 的項目；找不到或非本人項目回傳 `404`。
- **Request Body**

```json
{
  "notes": "等量縮回測 MA20"
}
```

- **欄位說明**
  - `notes`：觀察備註，選填，最多 500 字；空白字串會正規化為 `null`。
- **Response 200**：欄位同 `POST /watchlist` 單筆物件。

### `PUT /watchlist/reorder`

- **用途**：調整目前登入使用者的關注列表順序。
- **Auth/Ownership**：需要登入；排序清單只能包含 `current_user.id` 的關注項目。
- **Request Body**

```json
{
  "item_ids": [3, 1, 2]
}
```

- **欄位說明**
  - `item_ids`：調整後的完整 item id 清單。必須剛好包含目前登入使用者的所有 watchlist item id，不可遺漏、不可重複、不可包含其他使用者的項目。
- **Response 200**：回傳調整後順序的 watchlist item array，欄位同 `GET /watchlist`。
- **Error 400**：`item_ids` 有重複、遺漏目前使用者項目，或包含非本人項目。

### `DELETE /watchlist/{item_id}`

- **用途**：移除目前登入使用者某筆關注項目。
- **Auth/Ownership**：需要登入，且只能刪除 `current_user.id` 的項目；找不到或非本人項目回傳 `404`。
- **Response 204**：無 body。

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

### `GET /portfolio/closed-lifecycles`

- **用途**：提供結案頁所需的完整交易生命週期清單；`GET /portfolio/closed` 保留為相容的逐列 API。
- **完整結案邊界**：group 內不得有 active portfolio row，事件帳本必須包含 `full_exit`，且 ledger open quantity 必須為 0。不完整的部分結案 group 不出現在此清單。
- **期間語義**：前端以 `lifecycle_end_date`，也就是最終出清事件日，決定交易屬於哪個期間；一旦該交易入選，`exit_batches` 必須保留整個生命週期的所有出場，不得先按單批 `exit_date` 截斷。
- **完整交易損益**：`total_realized_pnl` 必須由完整事件帳本重算，與 lifecycle review 共用加權成本會計口徑；初始進場與新增批次的手續費／稅費計入成本基礎，結案事件的手續費／稅費自收入扣除。不得直接加總 closed portfolio rows 的 `realized_pnl`，避免遺漏只記錄於 entry event 的成本。
- **批次識別**：`exit_batches` 依事件日、建立時間與 event id 排序，依序顯示 `第 N 次減碼`，`full_exit` 固定顯示 `最終出清`；不得把 portfolio row id 當成人類可讀的交易序號。
- **卡片摘要**：若 group 已有目前版本的 v4 lifecycle review，`review_summary` 回傳 `outcome`、`process_quality` 與優先取 `improve`、其次 `keep`、最後 `next_actions` 的 `key_feedback`。沒有 v4 review 時回傳 `null`，GET 不主動建立 review。

### `POST /portfolio`

- **用途**：新增個人持股紀錄，供「我的持股」頁與 `/analyze/position` 使用。
- **持股數量**：不再限制每位使用者的 active 持股數量；`POST /portfolio` 不得因 active 持股已達 8 筆而回傳 `422`。同一使用者同一 symbol 仍不得重複建立 active 持股。

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
  - `entry_price`：購入成本價，必填，需為 `0.01`–`99,999,999.99`、最多 2 位小數的有限數值，對齊 `user_portfolio.entry_price NUMERIC(10,2)`；低於可保存精度、超出 precision/scale、`NaN`、正負無限大與溢位成無限大的輸入回傳 `422`
  - `entry_date`：購入日期，必填，ISO 8601 日期字串
  - `quantity`：持有數量，選填，未提供時預設 0；允許範圍為 0–`2,147,483,647`，對齊 PostgreSQL `INTEGER`
  - `notes`：備註，選填
  - `entry_record`：選填的進場決策脈絡，若提供則必須符合 `EntryRecordContext` schema；固定選項是未來 lifecycle review 的主要決策資料來源，`note` 僅為補充，不取代固定選項。

- **`EntryRecordContext` 欄位**

  | 欄位 | 類型 | 允許值 / 說明 |
  | --- | --- | --- |
  | `entry_reason` | enum \| null | `breakout_confirmation` / `pullback_held_support` / `pullback_held_ma20` / `institutional_flow_strengthened` / `fundamental_thesis_improved` / `event_or_news_catalyst` / `long_term_accumulation` / `value_revaluation` / `other` / `not_recorded` |
  | `planned_holding_period` | enum \| null | `short_term` / `swing` / `medium_term` / `long_term` / `not_recorded` |
  | `default_stop_rule` | enum \| null | `break_20d_low` / `break_ma20` / `break_ma60` / `cost_minus_pct` / `fixed_price` / `no_stop_recorded` / `not_recorded` |
  | `planned_stop_price` | number \| null | `0.0001`–`99,999,999.9999`、最多 4 位小數；對齊 lifecycle plan 的 `NUMERIC(12,4)` |
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
  - `shared_context`：Phase 2D portfolio diagnosis shared context reference。只讀 `shared_background_contexts` cache，作為 evidence/caveat 與資料品質說明；不得轉成 portfolio action、加減碼指令或交易建議。Active portfolio 不再有 8 筆硬上限；此 read path 仍須維持 bounded cache read，不觸發 weekly major holders、lending、full margin 即時逐檔 provider。

### `POST /portfolio/risk-summary/refresh-prices`

- **用途**：只更新 active holdings 的最新報價並以同一份價格快照重算 portfolio risk summary，不執行 AI 持股診斷。
- **Request**：`{"portfolio_ids": [42, 43]}` 只更新指定 active positions，最多 500 個 id；`{"portfolio_ids": null}` 更新目前登入使用者的全部 active positions。指定不存在、非本人或已結案 id 時回 `404 持股不存在或已結案`。持股建立、個股分析與 watchlist 寫入都只接受 `.TW`／`.TWO`，因此此路徑不得產生外幣混算。
- **Provider 邊界**：直接呼叫 quote crawler 的 portfolio snapshot，使用 process-wide shared executor，全服務最多 4 路 in-flight 行情請求；單次行情收集階段最多等待 5 秒。Portfolio crawler 以 thread-local total deadline 將每個 yfinance HTTP hop 限制在剩餘 4 秒內，且不呼叫額外的股票名稱 provider，確保 response timeout 後 running worker 仍會在 provider deadline 內回收；逾時或容量已滿以 per-symbol failure 降級。不得進 LangGraph、新聞、法人、基本面、`/analyze`、`/analyze/position`、analysis cache、history 或 calibration 流程。
- **持久化邊界**：response 只作本次 request 與前端 query cache 使用，不新增或更新 `stock_raw_data`、analysis log、portfolio/event/lifecycle state。盤中報價不得覆寫正式收盤資料。
- **市場時段**：僅處理台股上市／上櫃 snapshot，並在 fast-info 即時成交價 materialize 後立即記錄 `fetched_at`，早於 previous-close、歷史資料與 optional metadata 查詢，以該時間搭配 exchange、exchange timezone 與 `currentTradingPeriod.regular.start/end` 實際 session 邊界判定。不得用 request 結束時間回頭改寫較早取得的報價 finality，也不得因 daily volume bar 尚停在前一交易日而把盤中 fast-info quote 標成收盤；`data_date` 獨立揭露該 daily bar 日期。交易所、時區、session 邊界或觀測時間缺漏、格式錯誤或不含 timezone 時，必須回 `market_session = "unknown"` / `is_final = null`，不得回退使用 request 時間。
- **部分失敗**：每檔獨立成功或失敗。失敗檔沿用最新 final `stock_raw_data` 價格，`position_risks[].price_context.refresh_status = "failed"` 並加入 `price_refresh_failed` caveat；其他檔照常更新。Top-level `price_refresh.status` 為 `complete` / `partial` / `failed`，並列出 refreshed/failed symbols。
- **既有非台股資料**：schema 限制上線前若資料庫已存在非 `.TW`／`.TWO` active holding，summary 仍保留該列供使用者辨識，但加上 `unsupported_market` insufficient caveat，且 `market_value`、未實現損益與風險金額不納入 TWD portfolio totals，避免外幣直接混算。
- **前端 cache 合併約束**：因 response 是完整 summary replacement，刷新單一持股時前端必須一併送出目前 cache 中已成功刷新過的持股；若其中任何既有 refreshed symbol 在本次失敗，整份 response 不得套用，以免先前即時價與總計 silently regression。所有 portfolio writes 與價格刷新使用同一 mutation scope；跨分頁 revision 若在 request 期間改變，晚到的價格 response 必須丟棄並重新 invalidation。
- **Response**：沿用 `GET /portfolio/risk-summary` shape，另包含：

```json
{
  "price_refresh": {
    "status": "complete",
    "requested_count": 1,
    "refreshed_count": 1,
    "failed_count": 0,
    "refreshed_symbols": ["2330.TW"],
    "failed_symbols": [],
    "refreshed_at": "2026-07-31T10:30:00+08:00"
  },
  "position_risks": [
    {
      "symbol": "2330.TW",
      "current_price": 1100.0,
      "price_context": {
        "refresh_status": "refreshed",
        "source": "yfinance_fast_info",
        "as_of": "2026-07-31T10:30:00+08:00",
        "data_date": "2026-07-31",
        "market_session": "intraday",
        "is_final": false
      }
    }
  ]
}
```

`portfolio_revision` 是後端以 active position、lifecycle plan、正式 raw data、Phase 1 與 weekly context inputs 產生的 opaque SHA-256 revision；價格 override 不參與 revision。前端只可用於判斷 request-scoped price overlay 是否仍屬於同一份 portfolio 結構，不得解析其內容。

### `GET /portfolio/risk-summary`

- **用途**：Phase 5 read-only portfolio risk summary。以目前登入使用者的 active positions、最新可用 `stock_raw_data` 與既有 lifecycle plan 產生 deterministic portfolio-level risk diagnostics。
- **資料邊界**：只讀 `user_portfolio`、`position_lifecycle_plan`、`stock_raw_data` 與 active holdings 對應的 `phase1_avwap_snapshots` cache；不得建立、修改或刪除持股、交易事件、review、watchlist、Daily Radar 或任何 portfolio state。Portfolio read path 的 Phase 1 AVWAP 欄位只讀既有 snapshot，不觸發 provider backfill、snapshot refresh、watchlist lookup 或 latest Daily Radar candidate lookup。
- **語言邊界**：此 response 是風險紀律診斷，不輸出 portfolio action、recommended action 或交易命令。若 sector/theme data 不可靠，concentration 僅做 symbol / setup-type / risk-state / stop-rule 類別，不硬編產業分類。
- **缺資料行為**：`missing_price`、`missing_defense_reference`、`zero_quantity`、`stale_price` 皆以 `data_quality.caveats[]` 明示；缺少必要欄位時相關部位的 `estimated_risk_amount` 與 `estimated_risk_pct_of_portfolio` 可為 `null`，不得捏造成 0。
- **價格來源說明**：每筆 `position_risks[].price_context` 明示 stored final price 的 `data_date`、`as_of`、`is_final` 與 `refresh_status = "not_requested"`。此 GET 仍不得觸發 provider；只有上述 POST 可建立 request-scoped 最新報價 override。
- **Phase 1 AVWAP 行為**：`position_risks[].phase1_position_state` 是 holding-specific state trace；`phase1_current_day_lists` 是 Portfolio UI 的持股 AVWAP observation projection。Backend 只由 active holdings 產生 `holding_management_candidates` / `holding_risk_alerts`；`pullback_observation_candidates`、`breakout_confirmation_candidates` 與 `overheated_do_not_chase_candidates` 可為相容 response shape 保留空陣列，但 `/portfolio/risk-summary` read path 與 summary builder 不接受 watchlist 或 latest Daily Radar candidate observation map，也不產生非持股清單。非持股 AVWAP 候選應在 Daily Radar 或 watchlist 語境顯示。Portfolio read path 會使用 requested date 當日或以前最新 fresh snapshot，避免台北日期已跨日但正式 snapshot 停在上一個交易日時誤判缺資料；最多回看 7 個 calendar days，超過時回 `freshness = "missing"` / `missing_reason = "phase1_snapshot_stale"`；response 會同時保留 snapshot `data_date` 與 `requested_data_date`。Holding-specific entry anchor 與 avg cost 只在 Portfolio read projection 時由目前使用者的 portfolio rows 套用到 shared market snapshot，不寫回 `phase1_avwap_snapshots`。此 projection 只讀 cache，不觸發 provider backfill，不改 Daily Radar ranking/scoring。
- **TDCC 週頻籌碼行為**：`position_risks[].weekly_major_holders` 是 active holding 的 market-only shared context projection，來源為 `shared_background_contexts.context_type = "weekly_major_holders"`；缺資料時可省略，不讓 risk summary 失敗。Projection 可包含 `status`、`as_of_date`、`previous_as_of_date`、`thousand_lot_holder_ratio`、`thousand_lot_holder_ratio_delta_pp`、`large_holder_400_lot_plus_ratio`、`large_holder_400_lot_plus_ratio_delta_pp`、`retail_100_lot_or_less_ratio`、`retail_100_lot_or_less_ratio_delta_pp` 等欄位。`position_risks[].chip_stability_context` 只作籌碼穩定性摘要；千張大戶增加代表籌碼穩定性提升，連續增加代表籌碼愈加穩定，下降代表籌碼穩定性轉弱或集中度下降但不能單獨判定看空。此 projection 不改 `estimated_risk_amount`、`estimated_risk_pct_of_portfolio`、portfolio-level risk state、排序或任何交易建議。

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
      "price_context": {
        "refresh_status": "not_requested",
        "source": "stock_raw_data",
        "as_of": "2026-06-12",
        "data_date": "2026-06-12",
        "market_session": "closed",
        "is_final": true
      },
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
      "phase1_position_state": {
        "symbol": "2330.TW",
        "data_date": "2026-06-12",
        "dataset": "phase1_daily_ohlcv_amount",
        "adjustment_mode": "unadjusted",
        "state": "hold",
        "label": "續抱",
        "freshness": "fresh",
        "missing_reason": null,
        "display_anchor": {
          "type": "entry",
          "anchor_date": "2026-01-15",
          "anchor_reason": "holding_entry_date",
          "avwap": 112.5,
          "distance_to_avwap_pct": 6.6667,
          "source_granularity": "daily",
          "estimated": false
        },
        "matched_rules": ["phase1_display_anchor_supported"],
        "source": {
          "provider": "twse",
          "dataset": "TWSE_STOCK_DAY",
          "adjustment_mode": "unadjusted"
        },
        "source_granularity": "daily",
        "data_quality": {
          "estimated": false,
          "source_granularity": "daily",
          "rows_used": 80,
          "missing_reason": null,
          "blocking": false
        }
      },
      "weekly_major_holders": {
        "status": "fresh",
        "as_of_date": "2026-06-21",
        "previous_as_of_date": "2026-06-14",
        "thousand_lot_holder_ratio": 48.12,
        "thousand_lot_holder_ratio_delta_pp": 0.36,
        "large_holder_400_lot_plus_ratio": 64.9,
        "large_holder_400_lot_plus_ratio_delta_pp": 0.18,
        "retail_100_lot_or_less_ratio": 21.7,
        "retail_100_lot_or_less_ratio_delta_pp": -0.22
      },
      "chip_stability_context": {
        "version": "chip-stability-context-v1",
        "source": "tdcc_weekly_major_holders",
        "status": "fresh",
        "as_of_date": "2026-06-21",
        "previous_as_of_date": "2026-06-14",
        "thousand_lot_holder_ratio": 48.12,
        "thousand_lot_holder_ratio_delta_pp": 0.36,
        "state": "stable",
        "trend": "improving",
        "summary": "千張大戶持股比例增加，籌碼穩定性提升。",
        "caveats": []
      },
      "data_quality": {
        "status": "ok",
        "caveats": []
      }
    }
  ],
  "phase1_current_day_lists": {
    "version": "phase1-current-day-lists-v1",
    "implemented_lists": [
      "holding_management_candidates",
      "holding_risk_alerts"
    ],
    "pending_lists": [
      "pullback_observation_candidates",
      "breakout_confirmation_candidates",
      "overheated_do_not_chase_candidates"
    ],
    "pullback_observation_candidates": [],
    "breakout_confirmation_candidates": [],
    "holding_management_candidates": [
      {
        "symbol": "2330.TW",
        "name": "台積電",
        "label": "續抱",
        "position_state": "hold",
        "close": 120.0,
        "holding_avg_cost": 100.0,
        "display_anchor": {
          "type": "entry",
          "distance_to_avwap_pct": 6.6667
        },
        "matched_rules": ["phase1_display_anchor_supported"],
        "current_day_observation": "觀察 entry 是否維持支撐，結構仍偏健康。",
        "data_quality": {
          "blocking": false
        }
      }
    ],
    "holding_risk_alerts": [],
    "overheated_do_not_chase_candidates": []
  },
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

### `PUT /portfolio/{portfolio_id}/lifecycle-plan`

- **用途**：更新目前登入使用者 active 持股的 lifecycle plan。
- **provenance 邊界**：若既有 plan 是 `user_recorded_at_event_time` 且 request 實際改變任一 plan 欄位，更新後必須改標為 `source = user_backfilled`、`created_after_entry = true`，避免事後改寫的規則回頭參與歷史判斷。完全相同的 idempotent payload 保留原始 event-time provenance。
- **權限與並行邊界**：非擁有者回傳 `403`，已結案持股回傳 `409`；更新時鎖定 portfolio row，與 add-entry、close 等同群組操作序列化。
- **Request / Response**：Request 欄位與 backfill endpoint 相同；Response 200 欄位同 `GET /portfolio/{portfolio_id}/lifecycle-plan`。

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
- **數值儲存邊界**：`planned_stop_price` 為 `0.0001`–`99,999,999.9999`、最多 4 位小數；`planned_risk_amount` 為 0–`9,999,999,999.99`、最多 2 位小數；`planned_risk_pct` 為 0–`9,999.9999`、最多 4 位小數。超出 PostgreSQL 對應 `NUMERIC` precision/scale 時於 request validation 回傳 `422`，不得開始寫入。

### `PUT /portfolio/{portfolio_id}`

- **用途**：更正尚未發生後續 lifecycle event 的 active 持股成本價、數量、購入日期與備註；經濟欄位更正會同步 initial-entry event。已有後續事件時，一般 PUT 只能在經濟欄位不變的前提下更新備註。
- **權限與一致性邊界**：只能更新目前登入使用者自己的持股；非擁有者回傳 `403`。已結案紀錄回傳 `409`，已有 add-entry／partial-exit／full-exit 後再改寫成本、股數或日期也回傳 `409`，不得造成 portfolio row 與 event ledger 分裂。
- **Legacy ledger 邊界**：經濟欄位更正與 add-entry／close 共用同一補帳防線。單一舊持倉且完全沒有 event ledger 時，可先以目前 row 補建 `user_backfilled` initial-entry 再套用更正；同群組已有其他分批 portfolio row 時回傳 `409`，不得從目前剩餘股數猜測原始進場數量或歷史事件順序。只修改備註且經濟欄位不變時不觸發補帳。
- **更正 provenance**：成本、股數或購入日期任一變更時，同步後的 initial-entry event 必須標記 `source = manual_record_correction` 並寫入事後更正 `data_quality_note`；原有固定選項 reason metadata 保留，但不得再把更正後 facts 視為 event-time 或 backfilled 原始紀錄。只修改備註時不改變 event provenance。
- **數值邊界**：`entry_price` 必須是 `0.01`–`99,999,999.99`、最多 2 位小數的有限數值；`quantity` 必須介於 1–`2,147,483,647`，不符合時於 request validation 回傳 `422`。

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
- **Legacy ledger 邊界**：單一舊持倉若完全沒有 event ledger，第一次 lifecycle mutation 會以目前 row 補建 `user_backfilled` initial-entry；同群組已存在其他分批 row 時無法安全還原歷史順序，必須回傳 `409`，不得猜測補帳。
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
  - `event_date`：加碼日期，必填，不可早於初始進場日期，也不可早於目前帳本的最新事件；違反時回傳 `422`。
  - `price`：加碼價格，必填，需介於 `0.01`–`99,999,999.99` 且最多 2 位小數。
  - `quantity`：加碼股數，必填，需介於 1–`2,147,483,647`。
  - `fees`：手續費，選填，需介於 0–`99,999,999.99` 且最多 2 位小數；未提供時依 broker fee rule 計算 event ledger fee。
  - `taxes`：交易稅，選填，需介於 0–`99,999,999.99` 且最多 2 位小數；未提供時 add-entry event 稅額為 0。
  - `reason_code`：`breakout_confirmation` / `pullback_held_support` / `pullback_held_ma20` / `institutional_flow_strengthened` / `fundamental_thesis_improved` / `event_or_news_catalyst` / `long_term_accumulation` / `value_revaluation` / `other` / `planned_scale_in` / `averaging_down` / `chasing_momentum` / `not_recorded`。
  - `plan_adherence`：`yes` / `partial` / `no` / `not_recorded`。
  - `confidence_level`：`high` / `medium` / `low` / `not_recorded`。
  - `note`：選填補充文字；不替代固定選項。

- **行為與計算邊界**
  - 會以平均成本法更新 active portfolio 的 `entry_price` 與 `quantity`。
  - 加碼後 active portfolio 的總股數不得超過 PostgreSQL `INTEGER` 上限 `2,147,483,647`；即使單次 quantity 合法，只要加總溢位即回傳 `422`，且 portfolio 與 event ledger 均不得寫入。
  - 加碼後平均成本會在 application boundary 依 PostgreSQL `NUMERIC(10,2)` 規則明確量化後，再同步寫入 active portfolio；實際／自動計算的 `fees`、`taxes` 也必須符合 event 的 `NUMERIC(10,2)`。任何衍生值超出 envelope 時回傳 `422 加碼金額超過系統可儲存範圍`，且不得先修改 portfolio 或新增 event。
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
  "taxes": 2940.0,
  "reason_code": "stop_loss",
  "plan_adherence": "yes",
  "confidence_level": "medium"
}
```

- **欄位說明**
  - `exit_date`：出場日期，必填，ISO 8601 日期字串。
  - `exit_price`：出場價格，必填，需介於 `0.01`–`99,999,999.99` 且最多 2 位小數。
  - `exit_quantity`：出場股數，必填，需介於 1–`2,147,483,647`，且不可大於目前 active 持有股數。
  - `fees`：手續費，選填，需介於 0–`99,999,999.99` 且最多 2 位小數；未提供時依 broker fee rule 自動估算，若提供則視為使用者覆寫值。
  - `taxes`：交易稅，選填，需介於 0–`99,999,999.99` 且最多 2 位小數；未提供時依 sell transaction tax rule 自動估算，若提供則視為使用者覆寫值。
  - `reason_code`：結案原因，舊 client 可不提供；新版 UI 必須明確送出 `target_reached` / `trailing_stop_hit` / `support_broken` / `ma20_lost` / `institutional_flow_weakened` / `fundamental_thesis_broken` / `news_risk_increased` / `risk_reduction` / `profit_protection` / `planned_scale_out` / `stop_loss` / `emotional_exit` / `not_recorded` 之一。
  - `plan_adherence`：`yes` / `partial` / `no` / `not_recorded`；舊 client 可不提供，新版 UI 必須明確送出。
  - `confidence_level`：`high` / `medium` / `low` / `not_recorded`；舊 client 可不提供，新版 UI 必須明確送出。

- **計算邏輯**
  - 已實現損益採平均成本法計算：`realized_pnl = (exit_price - entry_price) * exit_quantity - fees - taxes`，其中 `fees` / `taxes` 使用同一組寫入 closed portfolio row 與 `position_event` 的實際成本值。
  - 已實現報酬率採本次出場股數的成本基準計算：`realized_return_pct = realized_pnl / (entry_price * exit_quantity) * 100`
  - 寫入前會先將實際／自動計算的 `fees`、`taxes`、`realized_pnl`、`realized_return_pct` 明確量化到各自 `NUMERIC(10,2)`、`NUMERIC(12,2)`、`NUMERIC(10,4)` scale，再檢查 storage envelope；超出任一上限時回傳 `422 結案金額超過系統可儲存範圍`，且 portfolio 與 event ledger 均不得寫入。
  - `holding_days = exit_date - entry_date` 的天數
  - `exit_quantity == quantity` 時為全數平倉：原持股設定 `is_active = FALSE`，並回傳該筆 closed portfolio。
  - `exit_quantity < quantity` 時為部分平倉：原 active 持股保留並扣減 `quantity`，另建立一筆 `is_active = FALSE` 的 closed portfolio 紀錄，該 inactive 紀錄代表本次出場股數，response 回傳新建立的 closed portfolio。
  - `full_exit` 與 `partial_exit` event 會保存 request 中的結案原因、計畫遵循與信心水準；`reason_code = not_recorded` 只保存為未記錄類別，不推論使用者意圖。舊 client 未提供三欄時維持 `null` 相容語意。
  - Event ledger 的 open quantity 必須等於 active portfolio row 的剩餘 `quantity`。既有 migration 產生的純 `synthetic_from_portfolio_row` 分批群組，若 initial-entry 誤存為剩餘股數，後續修補 migration 只處理可證明形狀：仍持有群組以「單一 active row + 單一 synthetic initial-entry + 每個 inactive portfolio row 恰好各有一筆 synthetic partial-exit」修正為 `active quantity + partial-exit quantity sum`；完全結案群組以「無 active row + 單一 synthetic initial-entry + 至少一筆 synthetic partial-exit + 最後單一 synthetic full-exit，且 initial/full exit 來自同一最後結案 row」修正為全部 exit quantity 總和。所有 active/source row、synthetic initial/exit event 與計算後 initial quantity 都必須嚴格大於 0，且計算後 initial quantity 不得超過 PostgreSQL `INTEGER` 上限 `2,147,483,647`；超出時跳過該群組，不執行可能中止 migration 的溢位更新。exit events 必須對全部 portfolio rows 形成不遺漏、不重複的一對一 source coverage，每筆來源 row 的 `quantity`、`exit_quantity`（null 時回退 `quantity`）與對應 exit event quantity 必須一致，且 synthetic initial event 的 entry price/date 必須與群組內所有來源 portfolio rows 一致。所有 portfolio rows 與 events 必須具有同一個非空 symbol；active row 若在 synthetic initial event 建立後又被更新，視為可能含有舊版 PUT 的人工修正而跳過。真正更新 initial event 前，migration 必須按 row id 對所有 contributing portfolio rows 與整組 initial/partial/full events 的 observed facts/timestamps 做 compare-and-lock，再對 initial event 做 compare-and-set；任一來源事實在 snapshot 後遭並行更正都跳過該群組，不得用 stale snapshot 覆寫。這些鎖只保護同一 transaction；部署 `1b2c3d4e5f6a` 時必須先停止舊版 portfolio writers，明確執行 migration，確認舊 instances 已退出後才開放新版流量，不能把 transaction lock 視為 commit 後的跨版本保護。含人工、補填、混合來源、多 active rows、source coverage 不完整、非正數、數量不一致、symbol 分裂、post-backfill mutation、entry price/date 分裂或其他事件形狀的群組不自動改寫，且修補可安全重跑。

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

- **用途**：為一筆已結案持股建立 deterministic rule-based Single Trade Review。`trade-review-v1` / `trade-review-v2` 會由 POST 原地升級為 v3；目前 v3 會以 source fingerprint 判斷是否可重用，未知或較新版本原樣回傳，不得降級覆寫。
- **Request Body**：無必填欄位；目前 frontend 送出空 POST body。
- **持久化與 freshness 語義**：同一 `portfolio_id` 只保存一筆預設 review。POST 先以短 read transaction 驗證 ownership／closed facts 與 TTL，cache miss 時必須結束 transaction、釋放 DB connection 後才能呼叫 provider；同一 process 內相同 user／portfolio key 的 refresh 使用 non-blocking single-flight，slot 已被占用時立即回 `409 Conflict`、`Retry-After: 1` 與 `交易審核正在更新，請稍後重試`，不得讓同步 request worker 排隊等待；backend 的 CORS middleware 必須暴露 `Retry-After`，first-party frontend 只對這個 `409` 依該 header 做最多 12 次、單次最多 5 秒的有界重試。取得 snapshot 後再鎖定 closed portfolio row、重新讀取 saved review，並在短 transaction 內以品質、normalized trading-bar coverage 與 `fetched_at` 仲裁後完成 fingerprint 比較與寫入，避免持鎖等待外部 I/O、並行重複建立或 stale overwrite；若其他 process 已寫入較新但降級的 fallback，而本次取得足夠完整的正常 provider，仍允許品質升級。closed-row facts 未變時，成功的 market snapshot 可重用 6 小時，帶 `missing_reason` 的降級 snapshot 僅重用 5 分鐘；到期後才重新呼叫 provider。重建時以 closed-row facts、`trade-review-ruleset-v3.1` 與獨立 review market snapshot 建立 `source_fingerprint`；cache reuse 也要求完全相同的 ruleset，舊 ruleset 即使 review version 仍為 v3 也必須重建。同版且 fingerprint 相同、但成功 refresh 的 `fetched_at` 較新時，必須更新 evidence freshness 與 TTL 而不清除既有 `llm_summary`；來源內容改變時才原地重建 `review_result` / `evidence_payload` 並清除舊 summary。GET 保持只讀；frontend 在讀到 v1、v2 或目前 v3 時送 POST 完成升級／freshness 檢查，對未知或較新版本不得自動 POST。
- **Partial provider TTL**：provider 即使回傳非空資料，少於 MA60 所需的 60 根可用交易 bar 時仍必須標記 `provider_coverage_insufficient`，並使用 24 小時 partial-coverage retry TTL，避免新上市或永久短歷史標的每次開啟都重抓；真正 provider 抓取失敗／空回應仍使用 5 分鐘失敗短 TTL，至少 60 根的正常 snapshot 使用 6 小時成功 TTL。
- **LLM 邊界**：目前不呼叫 LLM，`llm_summary` 固定為 `null`。
- **行情與 look-ahead 邊界**：review 補行情不得寫入或覆寫正式 `StockRawData`。yfinance 資料只建立 request-scoped review snapshot；下載明確使用單層欄位、10 秒 timeout、process-wide 4 路 non-blocking bulkhead，容量已滿或 provider 失敗時唯讀使用 bounded、`raw_data_is_final = true` 的既有 `StockRawData`，lifecycle query 與 evidence 也套用相同 final-only 邊界。Fallback 除了 `recent_*`，還會讀取 Daily Radar 已保存且帶日期的 `technical.price_history`；MA20、MA60、RSI14 或量比無法由序列計算時，可使用資料日嚴格早於事件日的 persisted `technical.indicators`，不得使用事件日或未來指標。即使 provider 回傳非空資料，首次建立與後續 refresh 都必須和 fallback 比較 `market-coverage-v1`：`row_count` 只表示 evidence container rows，實際選優只計 finite 且大於 0 的 close，並使用 `trading_bar_count`、Close 自有日期形成的完整 `covered_dates`、`holding_covered_dates`、`date_start`、`date_end` 與 `coverage_basis`；已提供但全部落在目標 window 外的日期不得降級成 undated series 估算，已有 trailing dates 時也不得再把 observation `record_date` 當成額外交易 bar。正常 provider 至少保留既有可比較 trading-bar coverage 的 90%，而既有持有期間日期必須 100% 保留；相同或更多總數仍不得遺失既有內部日期。品質已嚴重不足、少於 60 根的 `market-coverage-v1` evidence，或少於 60 根且具有實際 bars、可推導 coverage 的 legacy evidence，可由至少 60 根且達 material recovery 門檻的 fallback 自癒；一旦明確符合 material recovery，estimated fallback 可越過 holding-date subset gate，避免少量精確日期反向鎖死整體自癒。只有舊 `row_count`、沒有 bars 的不相容 legacy evidence 不得放寬。provider bars 必須依交易日升序並以日期去重後才可計算。一般 yfinance snapshot producer 必須把最後一筆有效 Close 的交易日保存到 `technical.data_dates.ohlcv`，並把 Close／High／Low 各自獨立去除缺值後的日期保存到 `recent_close_dates`／`recent_high_dates`／`recent_low_dates`。事件只有日期、沒有可證明的成交時間，因此 entry/exit 指標一律只使用 event date 前已 completed 的 bar；同日 cache row 要逐序列依日期排除事件日資料，不能用 Close 日期替 High／Low／Volume 證明 finality；High／Low 日期缺失時，即使長度等於 Close 也要保守移除無法證明的尾端值。既有 legacy row 若沒有 data date，保守移除各個無法證明完成的 OHLC 尾端值，但保留更早 trailing history；成交量依 `recent_volume_dates` 或實際 series 對齊獨立判斷，不得把整份 legacy snapshot 丟棄或把 stale cache 的前一日資料誤刪。
- **OHLC 與 full-exit 路徑邊界**：逐序列完成 event-date 裁切後，需要 OHLC 的指標與 compact evidence 必須再取 Close／High／Low 的共同交易日，禁止按索引拼接獨立 `dropna()` 後的序列。Single Trade Review 的平均日內 range 至少需要 20 根對齊 OHLC，樣本不足時不得把單一極端 bar 判為 `high_volatility`。Full-exit 只有日期而無成交時間時，結案日收盤不得進入 lifecycle 已持有路徑；實際出場價只使用 ledger event fact。
- **Evidence 邊界**：`evidence_payload` 保存 trade scalar、path metrics、point-in-time indicators、detected events、data quality、compact market snapshot、`ruleset_version` 與 `source_fingerprint`；不存 raw news、raw LLM prompts 或 unrelated portfolio history。Compact snapshot 使用 bar / trailing-series 結構，不把 canonical `StockRawData` 當成 review 可寫 cache。
- **StockRawData compact 邊界**：Trade Review 保留 fallback／direct-read 的原始 final rows 進行 deterministic 計算，但 evidence 與 fingerprint 必須依交易日 compact。所有 dated trailing series 先於 outer bars 合併；同日重疊的 trailing history 以較新非空值更新，較新 series 缺少的欄位保留既有 completed 值，partial outer bar 則只能補缺，不得用非空的盤中 outer quote 覆蓋 completed OHLCV；無日期的重複 trailing history 只保存最長一份。`quality.row_count` 保留來源 outer rows 數量，`quality.persisted_bar_count` 表示實際持久化的 compact bars，避免 provider fallback 將相同歷史重複寫入 JSONB 與 API response。

- **Response 200**

```json
{
  "id": 456,
  "portfolio_id": 123,
  "user_id": 1,
  "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "2330.TW",
  "review_version": "trade-review-v3",
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
  - `review_result.user_readable_conclusion.overall_verdict`：`early` / `reasonable` / `late` / `unclassified` / `insufficient`。
  - `entry_review.classification`：`breakout_entry` / `pullback_entry` / `chase_entry` / `weak_entry` / `range_entry` / `insufficient_data`。
  - `exit_review.classification`：`profit_protection_exit` / `stop_loss_exit` / `late_stop_exit` / `early_profit_exit` / `panic_exit` / `technical_break_exit` / `unclassified_exit` / `insufficient_data`。
  - `confidence`：`high` / `medium` / `low`。
  - `market_regime`：`uptrend` / `downtrend` / `range_bound` / `strong_momentum` / `high_volatility` / `insufficient_data`。
  - `holding_review.detected_events`：最多保留重要 holding events，event item 不包含完整 K 線序列。

> **Single Trade Review 結論邊界**：`trade-review-v3` 由後端 deterministic rule-based 邏輯產出，不呼叫 LLM，也不新增 `llm_summary`。只有存在可核對的獲利保護、風險控制或技術破位證據時才可回傳 `reasonable`；資料完整但證據不足時回傳 `unclassified`，市場資料不足時回傳 `insufficient`。既有 v1 / v2 紀錄會在 POST 時依 v3 規則與當前 source fingerprint 重建。

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
- **closed-only 邊界**：GET 與 POST 都要求 group 內沒有 active portfolio row、ledger 至少一筆 `full_exit`，且 `entry quantity - exit quantity = 0`；未完整結案回傳 `409`，`detail.code = position_lifecycle_not_closed`。
- **Response 200**：回傳欄位同 `POST /portfolio/groups/{position_group_id}/lifecycle-review`。
- **404**：目前登入使用者擁有該 group 但尚未建立 saved lifecycle review 時，回傳 `404`。

### `POST /portfolio/groups/{position_group_id}/lifecycle-review`

- **用途**：為同一 `position_group_id` 建立或更新 deterministic rule-based Position Lifecycle Review；若同版 saved review 已存在且來源資料未變，直接回傳既有 review。
- **權限邊界**：只能建立目前登入使用者自己的 position group lifecycle review；非擁有者回傳 `403`。
- **持久化語義**：第一次 POST 建立 `position_lifecycle_review`，`review_result` 與 `evidence_payload` 在同一 transaction 寫入。POST 先鎖定 group 的 portfolio rows，避免並行建立或 stale overwrite。第二次以後 POST 以 event facts、plan facts/provenance、compact market snapshot、point-in-time shared-context replay trace 與 ruleset 共同建立 `source_fingerprint`；同版同 fingerprint 維持 idempotent，任一 review-relevant source 改變時更新同一筆 v4 review。若 event / plan / shared-context facts 未變而新 market snapshot 降級成帶 missing reason 的 fallback，或相同品質下 normalized trading-bar coverage 下降，保留較完整的既有 review。舊 v1 / v2 / v3 可讀，但 POST 會建立 v4，避免舊版分類語意與新規則混用。
- **版本策略**：`review_version` 為 `position-lifecycle-review-v4`，以 `user_id + position_group_id + review_version` 唯一避免同版重複保存。v4 在保留 v3 deterministic classification 的同時，新增獨立的 `outcome` 與 `process_quality`，禁止用獲利或虧損直接代替操作品質判斷；另以 `dimensions` 分開呈現進場、部位管理、風險與出場、紀錄品質，狀態採類別而非 0–100 分數。`feedback.keep` 與 `feedback.improve` 只可由實際命中的 positive / risk labels 產生，`feedback.next_actions` 必須附來源引用。規則版本為 `position-lifecycle-ruleset-v4.1`；market evidence gap 只可把受影響的進場、部位管理或風險出場面向標成 `insufficient`，不可把 `record_quality` 降級，也不可要求使用者補記已存在的原因。只有 event、ledger、費稅或 decision context 等紀錄缺口才可把 `record_quality` 標成 `insufficient`。預留但尚未實作的 benchmark / sector relative-return 欄位可維持 `null`，不得因此把 `data_quality` 標為不足。`planned_risk_amount` 與 `planned_stop_price` 都是選填欄位，兩者皆未提供時 `planned_1r_amount` 與 R-multiple 指標維持 `null`，但不構成事件、ledger 或市場證據缺口，也不得單獨觸發 `insufficient_data`。若資料庫已有不在已知 v1/v2/v3/v4 集合內的版本，GET 與 POST 都原樣回傳最新未知版本並維持唯讀，不建立 v4 或降級覆寫。
- **LLM 邊界**：本端點不呼叫 LLM，不新增 LLM summary；`llm_summary` 固定為 `null`。Phase F 若要加入 summary，必須另行升版或新增 explicit narrative refresh contract。
- **Evidence 邊界**：`evidence_payload` 只存 compact event facts、review-relevant plan snapshot、lifecycle metrics、entry/exit sequence metrics、advanced internal trace、point-in-time indicator snapshots、capped detected events、market regime snapshots、compact market snapshot、Phase 2D point-in-time shared context references、source summary、data quality、ruleset 與 fingerprint；不存 raw LLM prompts、raw user notes、未記錄意圖推論、plan thesis 或 planned invalidation。正式 lifecycle market query 從首次進場前 120 個日曆日開始，只讀 `raw_data_is_final = true` 的 Daily Radar／正式 `StockRawData`，保留完整持有期間直到最後 lifecycle event，不另行呼叫 provider；較早歷史不得無界載入。Point-in-time close 會在 completed `recent_closes`、同日與前一筆 final row 的 dated `technical.price_history`、以及事件日前 outer closes 中選擇筆數最多的來源，並嚴格排除 `date >= event_date`；短但非空的 `recent_closes` 不得遮住較完整的 `price_history`。仍無法計算的 MA20、MA60、RSI14、量比才讀取 completed persisted indicators。寫入 evidence 前，`price_history` 會攤平成依交易日去重的 bars，可作 fallback 的四項 indicators 也會保存在對應 outer bar，使 source fingerprint 涵蓋所有可被分析選用的輸入；同日重疊 trailing history 以較新非空值更新並保留其缺少的 completed 欄位，outer bar 只補缺，不得用盤中／partial quote 覆蓋完整 OHLCV。legacy 無日期 series 只保留最長一份，避免每個 `StockRawData` row 重複保存整段歷史。`quality.row_count` 表示來源 rows，`quality.persisted_bar_count` 表示 compact 後實際保存的 bars。
- **Shared context point-in-time 邊界（Phase 2D）**：`review_result.shared_context` 與 `evidence_payload.shared_context` 以每個 `PositionEvent.event_date` 作為 `reference_date`，只引用適用目標 consumer 且 `as_of_date <= event_date` 的 shared background context。`shared_background_contexts` 以 `symbol` / `context_type` / `replay_key` 保留歷史 trace；若沒有可用歷史 context 且只存在晚於事件日的 context，會以 `missing_reason = "future_context_excluded"` 保留 caveat，並保留原始 excluded `as_of_date` trace；不得使用該未來資料批評 entry/exit-time decision。Shared context 只作 evidence/caveat/data quality，不改 `lifecycle_review.classification.primary_label`、tier、deterministic metrics 或 fixed-option decision-context 判讀。
- **Response 200**

```json
{
  "id": 789,
  "user_id": 1,
  "position_group_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "2330.TW",
  "review_version": "position-lifecycle-review-v4",
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
      "declared_plan_adherence_score": 75.0,
      "observed_plan_adherence_score": null,
      "plan_adherence_score": null,
      "decision_quality_score": null
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
      "status": "retrospective_only",
      "has_plan": true,
      "historical_judgment_eligible": false,
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
      "outcome": {
        "status": "profit",
        "label": "結果獲利",
        "summary": "這筆完整交易最終為獲利；獲利結果不等同於操作流程必然正確。",
        "total_realized_pnl": 12000.0,
        "total_return_pct": 5.42,
        "source_refs": ["lifecycle_metrics.total_realized_pnl", "lifecycle_metrics.total_return_pct_on_weighted_cost"]
      },
      "process_quality": {
        "status": "disciplined",
        "label": "流程大致有紀律",
        "summary": "已辨識出可重複的正向操作模式，仍應持續保留觸發條件與執行紀錄。",
        "strength_labels": ["disciplined_scale_out"],
        "risk_labels": [],
        "source_refs": ["exit_sequence.partial_exit_count", "exit_sequence.profit_protected_by_partial_exits"]
      },
      "dimensions": {
        "entry": {"label": "進場品質", "status": "not_observed", "summary": "目前固定規則未命中此面向的明確模式。", "source_refs": ["entry_sequence"]},
        "position_management": {"label": "部位管理", "status": "strength", "summary": "部位調整呈現可追溯的紀律或一致性。", "source_refs": ["exit_sequence"]},
        "risk_exit": {"label": "風險與出場", "status": "strength", "summary": "降低曝險或風險處理有明確可追溯的效果。", "source_refs": ["exit_sequence"]},
        "record_quality": {"label": "紀錄品質", "status": "sufficient", "summary": "目前紀錄足以支持這次規則化判讀。", "source_refs": ["data_quality", "decision_context"]}
      },
      "feedback": {
        "keep": [{"label": "disciplined_scale_out", "title": "保留分批保護獲利", "observation": "部分結案先鎖定獲利。", "action": "下次沿用事前定義的分批條件。", "source_refs": ["exit_sequence.partial_exit_count"]}],
        "improve": [],
        "next_actions": [{"title": "下次操作規則", "action": "延續可追溯的正向部位管理模式。", "source_refs": ["exit_sequence.partial_exit_count"]}]
      },
      "classification": {
        "primary_label": "disciplined_scale_out",
        "labels": ["disciplined_scale_out"],
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
  - `review_result.lifecycle_review.classification.primary_label`：主要 lifecycle 分類，例如 `averaging_down_into_weakness`、`disciplined_scale_out`、`risk_reduction_exit`、`premature_scale_out`、`late_scale_out`、`coherent_position_management`、`insufficient_data`、`unclassified`。`unclassified` 表示資料足以完成檢討但沒有命中既定 pattern，不是資料缺漏或決策脈絡不足。
  - `review_result.lifecycle_review.classification.tier`：前端預設 summary 使用的 tier，例如 `needs_review`、`insufficient_context`、`constructive`、`mixed`。
  - `review_result.lifecycle_review.*.source_refs`：每段固定模板文字的來源指標、事件或分類 trace。前端可顯示來源，但不應要求使用者解讀 raw score。
  - `review_result.event_indicator_snapshots`：每個 entry/exit event 的 point-in-time 技術指標與 market regime snapshot，不包含完整 K 線序列。
  - `review_result.event_facts[].fees` / `taxes`：event ledger 中已保存或系統計算的成本事實；不表示本端點要求使用者手動輸入交易稅。
  - `review_result.shared_context` / `evidence_payload.shared_context`：每個事件的 shared context read payload，包含 `source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key` 與 `data_quality`；missing/stale/future-excluded 均非阻塞。
  - `review_result.decision_context.status`：`present` / `retrospective_only` / `insufficient`。只有 `present` 且 `historical_judgment_eligible = true` 可參與歷史計畫判定；其餘狀態需明確提示不要推論或事後改寫原始意圖。
  - `review_result.decision_context.source` / `created_after_entry`：用於標示 plan provenance；`source = user_backfilled` 或 `created_after_entry = true` 時必須顯示事後補填 caveat，不可視為原始 entry-time intent。
  - `review_result.decision_context.planned_holding_period`、`default_stop_rule`、`add_entry_condition`：固定選項 plan facts。只有 event-time plan 可用於 `add_entry_plan_violation`、`unacted_stop_rule_break`、`holding_period_needs_review`；backfilled plan 僅作 retrospective context。
  - `review_result.advanced_internal.declared_plan_adherence_score`：使用者自報的 yes / partial / no trace；不得當成客觀 observed score。
  - `review_result.advanced_internal.observed_plan_adherence_score` / `plan_adherence_score` / `decision_quality_score`：目前在沒有獨立客觀觀測器時為 `null`，避免自報答案直接產生權威分數或 constructive tier。
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

#### Daily Radar segmented internal pipeline

正式 GitHub Actions workflow 使用分段 endpoints，所有 cron 以 UTC 設定並對應台灣時間；workflow 會明確生成 payload `run_date`，避免 GitHub runner / Zeabur runtime 時區影響資料日期。Scheduled run 會用 GitHub Actions run API 讀取原始 `created_at`，再回推 `github.event.schedule` 對應的 UTC cron slot；啟動延遲、跨過台灣午夜與對舊 run 按 Re-run 都不會改變原本 intended trading date。手動執行可指定 `run_date`，未指定時則使用原始 `created_at` 對應的台北日期。接著 workflow 的一般 step 先呼叫 `POST /internal/daily-radar/market-session`；TWSE 明確回報休市時 scheduled pipeline 與一般手動 step skip，provider 或 payload 異常時 fail closed。明確日期範圍的 `refresh-market-bars`、`backfill-institutional-flows` 與唯讀 `replay-institutional-universe` 是 maintenance exceptions，不依賴目前 `run_date` 的 `market_open` 結果。

#### `POST /internal/daily-radar/market-session`

- **用途**：正式 workflow 的最前置 guard，以 `run_date` 查詢 TWSE RWD `/rwd/zh/afterTrading/MI_INDEX`，並解析 `tables[*].data`。不可改用 legacy `/exchangeReport/MI_INDEX` 卻繼續沿用 RWD parser。
- **Auth**：與其他 Daily Radar internal endpoints 相同，需 `DAILY_RADAR_INTERNAL_TOKEN`。
- **Response**：成功時回傳 `status = open | closed`、`run_date`、`market`、`provider = twse`、`dataset = MI_INDEX`。
- **Fail-closed**：只有 TWSE 明確的 no-data 狀態才視為 `closed`；request failure、無效 payload、response date 不符、開市回應無 rows 或未知 status 回 `503`，不得靜默 skip。

- 17:30 TWT：`POST /internal/daily-radar/refresh-institutional-flows`，歸檔同日 TWSE `T86` 與 TPEX `3itrade_hedge` 完整法人日報；TW/TWO 任一失敗都不得讓後續 `prepare-universe` 冒充完整。
- 18:00 TWT：`POST /internal/daily-radar/prepare-universe`，從已驗證 archive 建立外資當日、投信當日、外資近期連續累積、投信近期連續累積四條獨立軌道，再保存 capped 250 selected symbols、universe trace 與 prepared step status。
- 18:30 TWT：`POST /internal/daily-radar/refresh-market-bars`，以 TWSE/TPEX 官方整表行情刷新 `taiwan_daily_bars`；手動 maintenance/backfill 不受目前 `run_date` 的 `market_open` 結果阻擋，但仍受 180 calendar days range limit 與 endpoint 驗證約束。transport、HTTP 408/425/429/5xx 與 JSON decode 暫時性錯誤最多三次 exponential-backoff attempt，TPEX requests 在同一 provider 內序列化；永久 4xx 與 schema/date mismatch 不 retry。
- 19:00 TWT：`POST /internal/daily-radar/refresh-avwap`，刷新 `phase1_avwap_snapshots`。
- 20:00 TWT：`POST /internal/daily-radar/refresh-lending`，刷新 `shared_background_contexts` 的 `lending`。TWSE 借券資料以回應內全市場實際出現的交易日期建立共同日期軸；個股在有效市場日期沒有活動列時必須補 `0`，不得以個股最後活動日誤判 stale/missing；整個查詢區間沒有任何可驗證市場日期時視為 dataset failure。Required segmented refresh 另要求每個 selected symbol 都收到 `as_of_date = run_date` 的 fresh payload；stale/missing 或 provider 少回 symbol 時列出 `missing_symbols` / `missing_symbol_reasons`、step 標 `failed` 並阻擋 scoring。
- 21:30 TWT：`POST /internal/daily-radar/refresh-full-margin`，等待 FinMind 21:00 更新後刷新 `full_margin`。
- 22:30 TWT：`POST /internal/daily-radar/refresh-ohlcv`，刷新 selected-symbol `stock_raw_data`，並用 refreshed raw rows 回寫 prepared universe 技術面 tracks。
- 23:00 TWT：`POST /internal/daily-radar/refresh-ai-evidence`，以同日全部 final、支援的 `.TW` / `.TWO` raw rows 為獨立 AI pool，補齊 technical、TWSE/TPEX 官方法人、lending/full-margin 與 `official_cache_only` 基本面；回傳 `missing_by_lane`，但不得修改 prepared membership 或成為 scoring required step。
- 23:30 TWT：`POST /internal/daily-radar/refresh-market-context`，以 yfinance batch download 取得指數，先正規化 single-symbol MultiIndex，並以整列方式排除任一必要 OHLCV 為空或非有限值的資料；失敗、空資料或非同一 `run_date` 時再使用同 provider 的 `Ticker.history` bounded fallback。若 yfinance 已有可驗證的前一交易日完整歷史、但同日 Close 仍缺漏，最後以 TWSE `MI_INDEX` 的「發行量加權股價指數」官方收盤補足；只有官方回推的 previous close 與 yfinance 最後完整 close 在容許誤差內一致才可合併，歷史 ATR 日期另存於 `benchmark.data_dates.market_volatility` / `market.volatility_data_date`，不得放進會合併為 candidate core freshness 的 root `data_dates`，並在 `provider_trace` 保留 official/history provider 與 fallback 路徑。只有 record date 與 index data date 都精確等於 `run_date`、fresh 且 regime 可判定的 market context 才把 step 標成 `completed`；missing/stale/指標不足或跨來源 previous-close 不一致必須標 `failed` 並阻擋 scoring。若同一 run 已有通過驗證的 context，暫時性重抓失敗沿用既有資料且不得用缺漏 payload 覆寫。
- 隔日 00:30 TWT：`POST /internal/daily-radar/run-scoring`，使用同一個 intended trading date 作為 `run_date`，只讀 DB cache/snapshot 並持久化 Daily Radar run/candidates。

#### Institutional archive maintenance endpoints

- `POST /internal/daily-radar/backfill-institutional-flows`：需 internal token 與明確 `start_date` / `end_date`。範圍為含首尾最多 11 個 calendar days，`end_date` 不得晚於後端台北當日。週末直接略過；平日先查 market-session，官方休市日不呼叫法人報表 provider。每個日期先驗證 TW/TWO completed snapshots、子 rows、row count 與 payload hash，完整日直接重用，partial 或 integrity check 失敗則重抓修復；若既有 partial/corrupt archive 與 closed session 判斷衝突，必須回 `institutional_backfill_archive_session_conflict`，不得把該日列為正常 skipped。每個交易日為獨立 transaction boundary；已完成日期在後續日期失敗時仍保留，response 以 `dates_completed`、`dates_reused`、`dates_repaired`、`skipped_dates` 與 safe `errors` 說明進度，重跑不得重複新增有效資料。
- `POST /internal/daily-radar/institutional-universe-replay`：需 internal token 與明確、不得晚於後端台北當日的 `run_date`，只讀 institutional archive。response 以 `daily-radar-institutional-universe-replay-v1` 比較現行四條 segmented tracks 與 `archive-combined-legacy-proxy-v1`，輸出 universe counts、symbols、track distribution、overlap、Jaccard ratio、單邊 symbols 與共同標的排名差。Proxy 不包含舊 `TWT38U` / `TWT44U` 報表量能集中度，必須明示 limitations；四條 segmented quota 與兩條 baseline quota 的總容量不同，`comparison.scope` 也固定為單一 run 的 membership/rank，不得解讀為 forward performance。只有最近 5 個完整且未跨缺檔平日的市場日可用時，`ready_for_human_review` 才為 true；`auto_apply_scoring_change` 永遠為 false，endpoint 不更新 prepared universe、candidate、rule 或 scoring config。手動 workflow 會把完整 response 以 14 日 artifact 保存，gate 失敗時也保留診斷檔。

#### Institutional archive release / rollback gate

1. 這個版本會在部署後立即把 live universe provider 切到 archive，不是 shadow mode。首次上線優先選台灣時間 07:30–17:00；若是非交易日，也必須先等當日可能的 07:00 AVWAP repair 結束，避免落在同一條 pipeline 或 repair 中間。
2. Merge 觸發 Zeabur 部署與 Alembic 後，先確認 `alembic current --check-heads` 為唯一 `6a7b8c9d0e1f (head)`，且後端 health check 正常；任一檢查失敗都不得開始回補或手動分段流程。
3. 第一次 17:30 live refresh 前，以 `backfill-institutional-flows` 回補至多 11 個含首尾 calendar days，範圍結束日為前一個台北日期；只有 response `status = completed` 且 `errors` 為空才算暖機完成。休市日被列入 `skipped_dates` 是正常結果，partial/corrupt archive conflict 則不是。
4. 同一 `run_date` 只要重做 `prepare-universe`，就會重置 prepared step statuses 並可能改變 selected symbols；因此不得沿用舊 universe 已完成的下游結果。若部署錯過 17:30，只有在所有 selected-symbol 下游步驟尚未開始時，才可手動依序執行 `refresh-institutional-flows` 與 `prepare-universe`；否則預設放棄該日新版 run、等下一個交易日完整重跑。若人工決定挽救當日，必須從新的 `prepare-universe` 後重跑全部依賴 selected symbols 的 refresh steps 再 scoring。
5. 應用回滾時部署前一個已驗證版本，保留這兩張 additive archive tables 與已歸檔資料，不在 production 執行 downgrade。若當日已建立 segmented prepared run，回滾後必須用舊 provider 重做 `prepare-universe` 及所有下游步驟，否則直接等下一個交易日。

`run-scoring` 不得打 FinMind、yfinance、TWSE 或 market index provider；缺少 prepared universe、prepared market context、final raw rows、selected universe 為空，或任一 required refresh step 不是 `completed` 時回 `409`，由 workflow/monitor 顯示資料準備缺口。空 selected universe 使用 `daily_radar_selected_universe_empty`，raw row 不完整使用 `daily_radar_raw_data_incomplete`。Required refresh steps 為 `refresh-institutional-flows`、`refresh-lending`、`refresh-full-margin`、`refresh-ohlcv`、`refresh-market-context`。`refresh-avwap` 是 optional evidence step：失敗或缺漏不得阻塞 `run-scoring`，但 candidate detail 必須保留 `input_snapshot.phase1_avwap_context.freshness`、`missing_reason` 與 data-quality caveat。

#### Fundamental archive internal endpoints

- TWSE/TPEX 官方市場 HTTP request 共用 `data_sources.official_http` 的 libcurl transport，維持 CA 與 hostname 驗證；不得為相容 Python 3.14 `VERIFY_X509_STRICT` 而使用 `verify=false`。Provider 仍保留 injectable `request_get` 供 deterministic tests 使用。
- `POST /internal/fundamentals/refresh`：固定最多 4 路並行取得 TWSE/TPEX 六類產業財報（共 12 datasets）、TWSE 股利決議與 TPEX 除息事件。每個 dataset 最多三次 request attempt；成功資料以 payload hash append revision，只有報表日期非空的已知官方占位列回報 `datasets_skipped` / `skipped_datasets` 並保留 cache，真正的空 payload、schema drift 或單一 dataset 失敗回 `status = partial` 並保留其他成功資料。
- `POST /internal/fundamentals/backfill`：對 request `symbols` 或 managed universe 做 bounded 歷史 bootstrap；EPS 缺漏先查 MOPS 官方歷史季資料，MOPS 失敗或寫入後歷史仍不足才查 FinMind 財報，股利歷史仍由 FinMind 補齊。managed universe 合併 active holdings、watchlist、最新 prepared universe 與最近一次完成的 AI raw pool。第一頁排除已完整 symbols，並把 immutable snapshot、`raw_pool_date`、server-owned cursor 保存到 `fundamental_backfill_jobs`；後續頁帶 `job_id`/cursor 並以 row lock 驗證。所有未帶 `job_id`、可能建立新 job 的入口都先取得同一 PostgreSQL transaction advisory lock 並檢查 running job；已存在時一般 create 回 `409 fundamental_backfill_job_running`，scheduled `resume_running_job=true` 則接續該 job，封住跨 caller 的 no-row create race。日期未完成、job 不存在、cursor 未帶 job、job completed 或 cursor 不一致一律 fail closed。每頁最多 10 檔；MOPS 單檔使用 5 秒 timeout、一次 attempt，失敗立即降級；FinMind client 使用 10 秒 timeout、零 transport retry、停用 token-expired 自動重試。六批最壞 logical upstream bound 為 60 次 MOPS + 120 次 FinMind（財報與股利）共 180 次。回應以 `provider_attempts` 計數各來源呼叫，並在 `fallback_symbols` 列出 MOPS 後仍需 FinMind 財報的股票。任一 lane 失敗時回 `partial`，成功寫入仍保留，cursor 前移過本頁已嘗試 symbols 以免永久錯誤餓死後續佇列；失敗 symbols 保持 cache incomplete，於 current job 結束後進入新 job。Workflow 先擷取 cursor/job 再處理 partial；每次最多六批，額度用完時 scheduled run 正常結束並由下一個排程續跑，實際 partial/provider failure 才非零結束。
- 兩者皆使用 `DAILY_RADAR_INTERNAL_TOKEN`。正式 `.github/workflows/fundamental-data.yml` 每個工作日 07:15 先做官方 refresh，再以最多六批、每批十檔的上限補齊 managed/latest raw-pool 基本面歷史；達上限時保留 running job 供下一個排程續跑。手動 backfill/resume 入口維持可用。
- `FUNDAMENTAL_PROVIDER_MODE` 預設 `finmind_only` 以維持部署相容；切為 `official_cache_first` 後分析先讀 `company_fundamental_periods` / `company_dividend_events`，只有歷史不足才 bootstrap；`official_cache_only` 完全不呼叫 FinMind/yfinance。官方股利事件若無法證明完整涵蓋一整年，`annual_cash_dividend` 必須維持 `null`，不可把部分年度事件冒充年股利。

#### `POST /internal/daily-radar/run`

- **用途**：保留一鍵手動相容入口；正式 GitHub Actions 排程使用 segmented internal pipeline。
- **Auth**：內部 token 必填，可使用 `Authorization: Bearer <DAILY_RADAR_INTERNAL_TOKEN>` 或 `X-Internal-Token`。
- **環境契約**：後端必須設定 `DAILY_RADAR_INTERNAL_TOKEN`。若後端未設定此 token，回傳 `503 Service Unavailable`。
- **Auth 錯誤**：request 未帶 token 時回傳 `401 Unauthorized`，並附 Bearer challenge；token 不符時回傳 `403 Forbidden`。
- **後端 orchestration**：相容入口仍會自行選出 multi-track universe，並一次完成 AVWAP evidence refresh、lending/full margin、OHLCV、market context 與 scoring；正式排程不使用此入口，避免免費 FinMind quota 在同一小時集中消耗。
- **Fixture fallback**：live run 關閉 fixture fallback，只使用 live provider 與既有 final `StockRawData`。
- **409 Conflict**：selected universe 為空，或嘗試 backfill 後 selected symbols 仍沒有 final `StockRawData` rows 時回傳。
- **公開 schema**：後端資料流改為分段 pipeline 後，public Daily Radar read endpoints 與 candidate response schema 不變。
- **資料源 request budget**：
  - TWSE/TPEX institutional archive universe：live provider 從已完成的 `T86` / `3itrade_hedge` archive 建立外資當日、投信當日、外資近期連續累積、投信近期連續累積四條獨立軌道，合併上市 `.TW` 與上櫃 `.TWO`。近期軌道最多讀最近 5 個 TW/TWO 同時完整的市場日，要求截至 `run_date` 的 trailing buy streak 至少 2 日且窗口累計淨買超為正；streak 不得跨越缺少 completed archive 的平日，週末則可自然銜接。舊 `TWT38U` / `TWT44U` provider 只保留 legacy prepared-run 相容。
  - TWSE-first Phase 1 Daily AVWAP：正式排程只在 `refresh-avwap` 小時合併 selected universe、active holdings 與 watchlist symbols 後做 refresh；上市 `.TW` 使用 TWSE `STOCK_DAY` 逐月 single-symbol query 補齊 lookback window，上櫃 `.TWO` 保留 FinMind `TaiwanStockPrice` fallback，其他 symbol 只記錄 `skipped_symbol_reasons.unsupported_phase1_avwap_market`。同一 `data_date` 已有 fresh snapshot 時直接重用。若 provider 尚未提供 requested `run_date` row，step status 會標記 failed 並輸出 per-symbol `missing_symbol_reasons`，其中 TWSE 延遲、request failure 與 parser error 需分別保留 `daily_price_row_missing_for_data_date`、`twse_stock_day_request_failed`、`twse_stock_day_parser_error`；但 `run-scoring` 仍可放行，候選 detail 以 `phase1_avwap_context.freshness = missing` / `missing_reason` 呈現。
  - AVWAP repair：台灣時間週二至週六 07:00 的 GitHub Actions 補修排程會對前一個 intended trading date 重跑 `refresh-avwap`；若 business status completed，立即重跑同日 `run-scoring`。Public read 以同日期最新完成 run 呈現補齊後版本，不直接改 candidate JSON。
  - FinMind lending / full margin：正式排程分別在 `refresh-lending` / `refresh-full-margin` 小時對 selected universe symbols refresh；同一 `run_date` 已有 fresh shared context 時直接重用，不再呼叫 provider。其餘 selected symbols 使用固定上限 8 路的 ordered sliding window，只維持最多 8 個 queued / in-flight futures，遇到前方致命錯誤時取消尚未開始的工作，不得先排入完整 symbol batch。所有 `FinMindClient` instance 另共用單一 process-wide HTTP capacity，並在第一個 client 建立時讀取 `FINMIND_MAX_CONCURRENT_REQUESTS`，預設上限 8；一般分析請求維持 non-blocking fail-fast，required Daily Radar refresh 則在整次 `fetch_data` 共用的 30 秒 admission deadline 內取得容量，HTTP retry 只可使用剩餘等待額度。逾時均回傳 `capacity_exhausted` 且不扣 hourly quota。上述路徑維持 per-symbol timeout、retry、quota ledger 與 deterministic response order，避免逐檔同步等待超過反向代理的單一 request 連線時間，也避免重疊 refresh 乘倍放大實際 upstream concurrency。
  - yfinance selected-symbol OHLCV：正式排程只在 `refresh-ohlcv` 小時對 selected universe 中缺少 final raw row，或 final row 缺少必要且為有限數值的 OHLCV / compatibility indicators、canonical `technical_profile`、非空且不晚於 `run_date` 的 `price_history`、必要資料日期的 symbols 做一次 batch download，區間 bounded by `run_date`。`raw_data_is_final = true` 只表示持久化狀態；只有同時通過 candidate/replay 完整度的既有 `StockRawData` 才可重用。補抓既有 row 只更新 technical payload，不得清空既有 institutional / fundamental payload；有明確 refresh payload 或 fresh full-margin context 時，再由對應 projection 覆寫。同一步驟會把技術面 tracks 回寫到 prepared universe，並以 `run_date` 做 point-in-time 查詢，將 fresh `full_margin` shared context 投影至新建或既有 final raw row 的 `fundamental.margin`：`margin_balance_delta_pct` 對應 `margin_delta_pct`，`latest_margin_balance × 1000 / ohlcv.volume` 對應 `margin_to_volume`，並以 context `as_of_date` 寫入 `data_dates.margin`；若比較起點融資餘額為 0，百分比在數學上不可定義，必須保留 `margin_delta_pct_unavailable_reason = baseline_zero`，完整度檢查接受這個明確理由，但 scoring 不得虛構 `0%`、無限大或套用需要該百分比的規則。任何入口的 context refresh 若降級或只回 missing/stale trace，不得清空 raw row 原本可用的 margin。`run-scoring` 與一鍵相容入口都必須在評分前拒絕空 selected universe，並重新確認每個 selected symbol 具備完整 raw row；不得在補抓失敗後退回未過完整度檢查的 final rows。
  - yfinance row completeness：selected-symbol batch 在建立 payload 前必須移除尾端任一必要 OHLCV 為空或非有限值的 rows，`data_dates.ohlcv` 只能取最後一個完整 row 的日期；中間缺口仍保留為 technical-profile data-quality caveat，不得把前一日 close 搭配當日 index 誤標為 current final data。
  - 完整 AI evidence pool：`refresh-ai-evidence` 從 `stock_raw_data(record_date, raw_data_is_final=true)` 取出所有支援台股，不讀 `daily_radar_candidates`、score、bucket、rule trace 或 prepared membership 作為 pool filter。缺 technical contract 的 row 由既有 batch technical fetcher 補抓；法人使用 TWSE `T86` 與 TPEX `3itrade_hedge_result` 日期報表建立 neutral raw flow，selected symbols 再合併 canonical prepared-universe trace 而不改其結論；融資由 fresh point-in-time full-margin context 投影；基本面只讀截至台北 `run_date` 已觀測的版本庫，不觸發 FinMind bootstrap，UTC 保存的 `first_observed_at` 必須先換算為台北日期，歷史補跑不得使用台北隔日才觀測到的財報或股利 revision。執行成功只代表 materialization 完成，仍須在 `missing_by_lane` 保留官方缺值與短歷史等分析缺口。
  - yfinance market index OHLCV：每次 run 只抓固定 benchmark。TW 使用 `TAIEX` / `^TWII`，US 使用 `SPX` / `^GSPC`，用於 market regime 與 relative strength benchmark。
  - Shared background context：正式排程把 `lending` 與 `full_margin` 拆成不同小時 refresh；`weekly_major_holders` 仍由週頻背景排程更新，不在 daily pipeline 內強行日更。
  - Live limits：只有 fresh、適用於 `daily_radar` 且不晚於 `run_date` 的 `full_margin` context 可以投影正式評分欄位；missing / stale / future context 不得合成中性值，也不得清空 raw row 原本可用的 margin。保留的 margin 仍須通過 required numeric finite-value validation；malformed、`NaN` 或 infinity 由 prefilter 標記 `data_gap`，不得進入 scoring。新建 row 若沒有 fresh context 則維持空 margin，同樣讓 prefilter fail closed。完整融資融券與借券內容仍由 selected-symbol shared context refresh 保存，並附加為背景 labels。

- **Request Body**

```json
{
  "run_date": "2026-06-02",
  "market": "TW"
}
```

- **欄位說明**
  - `run_date`：選填，Daily Radar run 日期，未提供時由後端使用當日台北日期；正式 GitHub Actions workflow 必須顯式傳入此欄位。
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
  | `score_breakdown`   | object         | 分數拆解，用於 advanced trace / debug evidence；包含 bucket scores、technical profile layer impact、cross confirmation、market context、relative strength、freshness、risk penalties、observation score 與 version trace |
  | `input_snapshot`    | object         | 產生候選時使用的輸入快照；包含 market context、relative strength、canonical `technical_profile`、版本資訊與 replayable evidence |
  | `data_dates`        | object         | 各資料來源對應日期            |
  | `matched_rules`     | array          | 命中的 rule ID 或規則名稱     |
  | `background_context_labels` | array | Phase 2B shared background context labels，用於 Daily Radar detail surface，不參與分數或排序 |

  `name == symbol` 的既有 Daily Radar 資料需透過 `POST /internal/daily-radar/name-backfill` 主動修復；本機 `backend/scripts/backfill_daily_radar_symbol_names.py` 僅作除錯輔助。修復流程會更新 `daily_radar_candidates.name` 與 `stock_raw_data.technical.name`。公開讀取 API 不得為了補顯示名稱同步呼叫 TWSE/TPEX metadata provider。

- **Trace contract**
  - `input_snapshot.market_context` 至少可表示固定 benchmark 的 `regime`、`freshness`、`data_date`、均線位置、波動狀態與 risk flags。
  - `input_snapshot.background_context[]` 可表示 Phase 2A shared background context cache trace，包含 `context_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key`、`applicable_consumers` 與 `payload`。Missing/stale context 不改 `observation_score`、bucket、risk labels 或排序。
  - `background_context_labels[]` 由 background context trace 派生，包含 `context_type`、`label`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key` 與 `applicable_consumers`。目前 labels 包含 weekly major holders 背景持股集中脈絡、lending 借券空方壓力背景、full margin 完整融資融券背景。這些 labels 是 context/detail surface，不是交易 action、portfolio recommendation 或 score driver。
  - `score_breakdown.relative_strength` 表示 benchmark symbol、lookback window、candidate return、benchmark return、relative value、score impact、freshness、data dates、aligned dates 與 missing reason。資料不足時 `relative_value` 為 `null`，不可補 0 假裝中性。
  - `input_snapshot.technical_profile` 與 `score_breakdown.technical_profile` 由 canonical technical profile builder 產生，用於 replay trace、data-quality 與後續 scoring 遷移依據。現行 Daily Radar bucket/cross scoring 仍讀 compatibility `indicators`；`technical_profile` trace 必須能回放 layer impact、bucket cap 前後分數、`technical_profile.version`、`formula_versions` 與 `data_quality`，但不得和 compatibility scoring 重複計票。後續若要讓排名改由 `technical_profile` 主導，必須先用 production-like replay 證明新 layer trace 足以替代既有 KD/MFI/MACD/ATR 排查用途，再更新 scoring version、tests 與本規格。
  - `input_snapshot.evidence[]` 使用 consumer-neutral replayable evidence shape，包含 `evidence_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key`、`applicable_consumers` 與 `details`。Phase 1 僅 `daily_radar` consumer 使用。
  - `input_snapshot.replay_input` 自 `daily-radar-replay-input-v1` 起保存完整 deterministic scoring input、baseline `ScoringConfig` 與 config version。舊候選缺少此欄位時，月報必須標記 `replay_input_incomplete`，不得猜測。
  - Current version trace：`daily-radar-scoring-v2.5` / `daily-radar-rules-v2.4` / `daily-radar-scoring-config-v1`。v2.3 起，缺少必要 scoring inputs 會標記 `data_gap`，缺值本身不得觸發正向規則；v2.4 scoring 起，legacy `same_day_institutional` 候選會以合法的單一法人正數淨買超計入同日法人分數；v2.5 scoring / v2.4 rules 起，archive-backed `foreign_same_day` / `trust_same_day` 與 actor-specific 近期累積淨買超也會進入同一組互斥法人規則，且不與三大法人合計轉正或外資投信方向一致重複計分。

- **Calibration workflow**
  - Daily Radar calibration report 可由 `uv run python scripts/daily_radar_calibration.py --source fixture --run-date 2026-05-29` 重跑。
  - Report 是 deterministic JSON，包含 sample count、bucket distribution、rank cutoff impact、bucket threshold impact、risk/overheat impact、relative strength impact、skip reasons 與 version manifest。
  - Calibration report 不改 live scoring 行為，不宣稱勝率、價格承諾或交易指令。

#### Internal calibration lifecycle

- `POST /internal/daily-radar/forward-validation/run`：以 `mode = due` 評估最新公開 run 中已成熟的 5 / 10 / 20 交易日窗口；同日 rerun 只採最新公開 run。
- `POST /internal/analysis-calibration/forward-validation/run`：評估 append-only、final `/analyze` 樣本的 5 / 10 / 20 交易日 outcome。
- `POST /internal/daily-radar/rule-review/monthly`：輸出 Daily Radar baseline / candidate config、training / holdout 指標、watermark、coverage 與自動修改資格。
- `POST /internal/analysis-calibration/monthly`：輸出一般分析 confidence baseline / candidate config、training / holdout 指標、watermark、coverage 與自動修改資格。
- 四個端點均沿用 `DAILY_RADAR_INTERNAL_TOKEN`。月報只透過 AES-256 加密的 GitHub Actions artifact 下載，密碼來自 `CALIBRATION_REPORT_PASSPHRASE`，不寫入 public issue 或 main branch。一般分析第一版只保存 `.TW` / `.TWO` final `/analyze` 樣本，固定分區為 TW / TAIEX；其他市場不寫入這個 calibration cohort。
- 兩軌 forward validation 透過 feature adapter 共用 `ai_stock_sentinel.calibration.forward_validation`；月報以 SQL monthly aggregation 選 cohort，再以明確月份條件載入六個成熟月份的 replay / validation detail。
- 一般分析校準只收 `/analyze`，不含 `/analyze/position`；replay payload 不保存 user id、使用者筆記、新聞全文或 LLM 分析全文。Final cache 內另保存同一份精簡 payload，capture 暫時失敗時由後續 final cache hit 冪等重試；舊 cache 無正式 payload 時不得反推。
- 一般分析校準的 active cohort 固定為目前 `strategy_version` + `confidence_config_version`；資料庫唯一鍵包含 `analysis_type / market / symbol / record_date / strategy_version / confidence_config_version`，日內重跑不得因 input hash 改變而增加獨立樣本。Validation outcome 的 `signal_date`／`benchmark_symbol` 必須和所屬 sample 一致；寫入不一致時拒絕，既有異常 row 不得計入 evaluated／validated watermark，並在 watermark 保留逐窗口 mismatch 計數。Due mode 判斷既有 terminal row 時也必須重新核對 sample identity；日期或 benchmark 錯配的舊 row 視為尚未完成並重新排入 evaluation，讓系統可用正確結果自癒。升級 migration 會以 exclusive table lock 阻止 writer 競態，lock 等待上限固定為 10 秒，整個 statement 執行上限為 5 分鐘，逾時必須 fail closed 並由 operator 排除阻塞 transaction 或重新評估資料規模後重試；同一 identity 先一次性固定具有最多 `validated` outcomes 的 canonical sample，再以 evaluated outcome 數與最早 ID 決定 tie-break。只保留 canonical sample 原生 outcomes，絕不把其他 input hash 的 outcome 改掛過來；缺少的窗口由後續 due validation 重算。此 canonicalization 刻意不可 downgrade，部署前必須備份、盤點 table row/duplicate 規模、停止所有舊版 backend 與 calibration workflows，並設定一次性 `CALIBRATION_MIGRATION_BACKUP_CONFIRMED=2c3d4e5f6a7b`，否則 upgrade fail closed。
- 一般分析與 Daily Radar 的月份 maturity 都必須以 5／10／20 日三個窗口共同判斷；只完成 20 日窗口的月份不得進入最近六個月 cohort。
- 一般分析與 Daily Radar 的 candidate config 都必須逐一通過 5 / 10 / 20 日 holdout gate，不得用跨 horizon 聚合改善掩蓋單一窗口退化。
- 一般分析 `general-analysis-confidence-review-v7` 的 replay eligibility 必須驗證 current schema、`base_score` 0–100 整數、方向 labels、0–1.6 有限 `sentiment_strength`、布林 `date_unknown` 與完整 current `ConfidenceScoringConfig`；結構通過後先計算 aggregate workload，最多 300,000 次 estimated scoring calls 與 40,000,000 次 before／after bootstrap row-iterations，任一超限即輸出 `replay_workload_limit_exceeded` 並在 baseline replay 前停止。容量允許時按 sample 單次重播 current baseline，並和 production 保存的 `signal_confidence` 比較。任一 mismatch 以 `baseline_replay_mismatch` 排除並重算 coverage；即使整體與逐月 coverage 仍達 threshold，`baseline_replay_complete = false` 也必須阻止所有 candidate config eligibility。
- Daily Radar `daily-radar-rule-review-v5` 為最新公開 run 的全部 candidate 建立 5 / 10 / 20 日完整池；validation result 不存在時保留 `status = missing`，不得讓候選從 ranking pool 消失。Replay eligibility 必須驗證 schema、current scoring/rule/config versions、baseline config、record identity/date、必要 scoring fields 的有限數值、data dates、accepted prefilter 與 technical profile，不得只看 `schema_version` 或空容器。結構驗證通過後，baseline replay 還必須逐 candidate 重現原 production 的 observation score、primary/secondary buckets、bucket scores、risk labels 與 matched rule IDs；任一不一致以 `baseline_replay_mismatch` 排除，並將該交易日／窗口 ranking pool 標為 `incomplete`。90% replay coverage 保留作資料品質診斷，但任何 ranking／counterfactual governance 必須逐交易日、逐窗口具備 100% replay ranking pool；不完整時輸出 `ranking_pool_status = incomplete`、`replay_ranking_pool_incomplete` 並禁止調整資格，沒有成熟 cohort 時則為 `not_applicable`，active ablation 輸出 `not_applicable_no_cohort` 而不執行 replay。除單一交易日／窗口 production cap 250 candidates 外，月報在 scoring 前另計算 aggregate workload：最多 300,000 次 estimated scoring calls 與 220,000,000 bootstrap row-iterations，任一超限即輸出 `capacity_exceeded`／`replay_workload_limit_exceeded` 並停止 replay。報表共用一次 baseline replay，同一 config 下每個 candidate 只 scoring 一次再投影到三個 outcome windows；mean bootstrap 將 validated selected rows 預聚合為每日期 sum／count 後重抽統計量，每次抽樣仍先把 before／after 平均值四捨五入至四位再計算 delta，並以完整 training dates 作 block universe，保留無 selected rows 日期的統計語意。結果最後接 validated outcome 並輸出 `counterfactual_ablation_summary`；只有 live-score tiers 可執行 counterfactual ablation，context-only 群組輸出 `not_in_live_score`。`co_occurrence_summary` 僅是相關性診斷。任何 recommendation 都不直接更新 live config、rule version 或 ranking。
- Daily Radar validation identity 另綁定 candidate scoring snapshot 的 `benchmark_symbol` 與所屬 run date：forward-validation request 或新 outcome 錯配時直接拒絕，既有錯配 row 視為 `validation_identity_mismatch` 並重新排入 due evaluation，不得進入 rule recommendation、maturity 或 replay。月報的 aggregate workload 必須先用只計數、不選取 candidate JSON snapshot 的 SQL preflight 判斷；超限時不得再 hydrate optimizer detail。

- Daily Radar replay identity 另要求 validation row `signal_date`、candidate snapshot `record_date` 與 replay record `record_date` 三者完全一致；任一缺漏或不一致都以 `replay_input_incomplete` fail closed，錯日期 outcome 不得進入治理。
- Daily Radar freshness identity 同時要求 core `data_dates`、price history、market context 與 benchmark dates 都不得晚於 candidate `record_date`；replay 遇到未來日期時以 `replay_input_incomplete` 排除，live prefilter/scoring 則把負 lag 視為 freshness/data gap，避免未來資料進入正式候選。

Forward validation due request：

```json
{
  "mode": "due",
  "market": "TW",
  "windows": [5, 10, 20],
  "benchmark_symbol": "TAIEX",
  "as_of_date": "2026-07-27"
}
```

`as_of_date` 可省略。成功回應包含 `status`、`mode`、`as_of_date`、候選或樣本數、`records_written`、`validated_count`、相容總數 `skipped_count`、`retryable_skipped_count`、`terminal_skipped_count` 與詳細 `report`。Due mode 先以任一價格序列判斷可能成熟窗口並觸發 provider refresh；若 benchmark 需要補資料，必須先更新 benchmark 市場日曆，再依更新後的交易日期重新計算並抓取候選股缺口。兩條 validation route 共用同一個 planning service 執行 benchmark refresh、candidate refresh 與 evaluation-readiness 判斷，不得各自複製 refresh 排序。Refresh 完成後以 benchmark 的交易日期作為市場日曆，候選股必須完整涵蓋相同的前 N 個 benchmark 交易日才可進入 outcome evaluation，且候選股與 benchmark 的 target date 必須相同。候選 provider 額外產生但 benchmark 不存在的休市日資料不得計入窗口、target return、MFE 或 MAE。若距 signal date 已超過窗口兩倍日曆天數仍不完整，才視為 retryable data gap。此交易日語意自 `daily-radar-forward-validation-v2` 與 `general-analysis-forward-validation-v2` 起生效；舊 `v1` 結果保留作歷史稽核，但不得阻擋 `v2` 重算。Due mode 的預設 lookback 會回補範圍內的 `v2`，若需補更早資料必須用明確日期範圍執行 backfill。Daily Radar 月報未指定版本時固定只讀目前的 `v2`，不得以筆數多寡選舊版本或混合不同版本。`stale_candidate_price` 屬於 terminal skip；它會保留診斷紀錄，但不應阻擋 workflow。`missing_benchmark` 等暫時性缺口屬於 retryable skip，後續 due run 仍會重新評估。兩條正式 forward-validation workflows 都會輸出三種 count 與 `report.skip_reasons`，並只要求 `retryable_skipped_count == 0`；因此第一次遇到 terminal skip 不會讓 CI 失敗。Daily Radar 與一般分析的 Re-run 都會排除目前 validation version 已 `validated` 的窗口及既有 terminal skip。GitHub job 的 `skipped` conclusion 不等同任何 response skip count。

Daily Radar monthly request：

```json
{
  "market": "TW",
  "benchmark_symbol": "TAIEX",
  "year": 2026,
  "month": 6,
  "min_sample_count": 20,
  "min_validated_coverage": 0.9,
  "min_replay_coverage": 0.9
}
```

一般分析 monthly request 另帶 `"benchmark_symbol": "TAIEX"`。回應共同包含 `report_json` 與 `report_markdown`；`report_json` 至少包含：

- `cohort`：最近六個成熟月份、五個 training months 與一個 holdout month。
- `completeness_watermarks`：保留 20 日 expected / evaluated / validated 相容欄位，並提供 5 / 10 / 20 日逐窗口 expected / evaluated / validated 與 coverage；三個窗口都完整 evaluated 才算成熟月份。
- `coverage` 或 `replay_coverage`：distinct samples、整體與逐月 replay coverage、排除原因及 threshold 結果。一般分析另輸出 `baseline_replay_complete`；兩軌的 `replay_workload` 都輸出 sample／candidate、row、config、estimated scoring calls、bootstrap row-iterations、各上限及 exceeded limits，Daily Radar 另外包含 ablation 數量。
- `candidate_configs[]`：單參數單 step before / after、各窗口 metrics、training block bootstrap、holdout、distinct sample counts、training / holdout block counts、`auto_change_eligible` 與 `eligibility_reason`。

`min_sample_count` 是每個 5 / 10 / 20 日窗口的 distinct sample / candidate 數，不是三個窗口的 validation row 總和。Training 固定至少需要 20 個日期 blocks、holdout 至少 5 個日期 blocks；replay coverage 必須整體與每個入選月份都達 90%，且一般分析的 coverage 分母只計 optimizer scope 的 `short_term` / `mid_term`。Artifact retention 為 30 天，因此每月需下載保存；報表可累積六個成熟月份後再人工審查，且不會直接修改 production。

#### Internal Daily Radar chip context update

- **Endpoint**：`POST /internal/daily-radar/chip-context/update`
- **用途**：更新 `shared_background_contexts` cache。這是週頻 `weekly_major_holders` 的正式背景更新路徑，也可作為 `lending` / `full_margin` 維護或補跑入口；每日正式 Daily Radar pipeline 已改用 `refresh-lending` / `refresh-full-margin` 分段 endpoints，再由 `run-scoring` 讀 cache 寫入 candidate snapshot。同一 `replay_key` 會 upsert，新的 `replay_key` 會保留為歷史 trace，供 point-in-time consumer 回放。
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

`symbols` 選填；明確提供時，所有 requested `context_types` 都使用同一批 symbols。未提供時，backend 依 context type 決定更新範圍：`weekly_major_holders` 使用目前 active portfolio holdings、watchlist symbols 與指定 market 最新可公開 Daily Radar candidates 的去重集合；`lending` 與 `full_margin` 仍只使用最新可公開 Daily Radar candidates。若 request 未指定 `context_types` 而採預設全量，weekly 與 daily context 會各自使用上述範圍，避免把日頻 FinMind refresh 擴張到 holdings/watchlist。active holdings 與 watchlist 只作 symbol selector，不寫入 `shared_background_contexts.payload`；shared cache 仍是 market-only evidence cache，不保存 user id、quantity、avg cost、holding ownership 或 watchlist ownership。

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

Provider failure 以 `status: "failed"` 與 `errors[]` 記錄，response 仍是 200。Daily Radar run 內的日頻背景刷新失敗時會降級為 missing/stale cache trace，不阻塞 candidate persistence；獨立 workflow 會檢查 response JSON 的 `status == "completed"`，若為 failed 或 non-JSON response 會 fail job 以利排程監控。正式 workflow 為 `.github/workflows/daily-radar-chip-context.yml`，使用 `ZEABUR_BACKEND_URL` 與 `DAILY_RADAR_INTERNAL_TOKEN` secrets，不硬編 secret；週頻 `weekly_major_holders` 在台灣時間週日 07:30 更新，日頻 `lending` / `full_margin` 可透過同一 endpoint 維護或補跑。

`weekly_major_holders` payload 採 `holder_level_schema_version = "tdcc-holder-level-v2"`。TDCC level 15 代表 `thousand_lot_holder_ratio`（千張大戶持股比例），levels 12-15 合計為 `large_holder_400_lot_plus_ratio`，levels 1-9 合計為 `retail_100_lot_or_less_ratio`；legacy `major_holder_ratio` 保留為 400 張以上大戶比例的向後相容別名。Payload 應保留 `holder_level_schema` 與各 level 明細，讓後續 projection 可重建 delta、consecutive increase 與資料品質 caveat。

Alembic migration `f7a8b9c0d1e2_backfill_tdcc_weekly_holders_v2_payload.py` 是 data-only backfill：只針對已存在的 `weekly_major_holders` rows，從既有 `payload.distribution` 重算 holder-level v2 欄位，不呼叫 TDCC、不改 `replay_key`，且可重跑。缺少或格式不合法的 distribution 會跳過，之後由正式 weekly background updater 補新資料。

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
  - `POST /portfolio` 在 active 持股數已達 8 筆時仍可新增
  - `POST /portfolio` 不再回傳舊的 8 筆上限 `422`
  - `PUT /portfolio/{id}` 僅允許持股擁有者更新
  - `DELETE /portfolio/{id}` 僅允許持股擁有者刪除
- 測試檔（LLM input contract）：`backend/tests/test_graph_nodes.py`、`backend/tests/test_langchain_analyzer.py`
- 覆蓋項目（LLM input contract）：
  - `analyze_node` 傳入 `signal_summary`，且摘要包含 KD / ADX / OBV 與 rule-based labels
  - analyzer prompt 將 `signal_summary` 放在優先閱讀區，並保留 `position_context` / `prev_context` 可選參數
