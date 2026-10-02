> 2026-10-02 功能範圍：產品保留個股分析、Daily Radar 與主動式 ETF。關注列表、個人持股、持股診斷與復盤已下線；舊路由導向 `/analyze`，舊 API 回傳 404。歷史資料模型、資料與 Alembic migration 保留，未執行資料刪除。AVWAP／籌碼背景採雷達標的，基本面保留 prepared universe／final raw pool，managed raw data 僅更新近期 general 分析標的。

# AI Stock Sentinel 後端 API 技術規格（v5）

> 類型：技術文件（Technical Doc）
> 更新日期：2026-08-31
> 更新摘要：2026-08-28 起 `/analyze` 與 `/analyze/position` 完全移除 LLM 與 RSS 新聞執行路徑，改為 crawl → external data → judge → preprocess → score → strategy 的 deterministic contract。`persist_result` 控制是否讀寫完整分析快取；`skip_ai` 與 `news_text` 只保留 deprecated request 相容。舊 LLM/news response 欄位暫留但固定為空值，舊快取也不得重新送出歷史 AI 內容。本文後段若仍提及 LLM prompt 或 cleaner，視為歷史設計而非現行 runtime contract。
> Cache 邊界：deterministic contract 的 `STRATEGY_VERSION` 為 `2.0.0`。`1.0.0` 與更舊的當日快取可能含新聞／LLM 派生的 confidence、strategy、action plan 或 position recommendation，必須視為版本失效並重跑，不得只清空顯示欄位後沿用派生決策。
> Release Gate 仍包含 portfolio risk data-gap、Determinism Gate、Shared Context Gate 與 Copy Guard Gate；本次移除模型不得放寬既有投資紀律邊界。
> Technical profile v4 保留純量化的 `ma20_slope_pct_5d`、`ma60_slope_pct_10d`、`macd_hist_slope_pct_3d`、`macd_hist_trend`、`atr_pct_percentile_60d`、`bollinger_bandwidth_percentile_60d` 與 profile 內的 `temporal_evidence`；移除跨指標綜合判斷 `volatility_regime`、`technical_conflicts`、`signal_conflicts`。新增時序欄位目前全為 evidence-only、`impact=0`，不得改變 `score_summary`；盤中若無日期可證明 completed bars，temporal evidence 必須 fail closed。
> 2026-08-31 新增 authenticated 主動式 ETF 每日持股讀取 API 與 internal refresh API；每個來源各自保存 point-in-time 原始證據，只有集中來源與發行投信官方來源逐筆一致的基金才發布變化，不進入 Daily Radar scoring。

## 功能退役與相容欄位邊界

2026-10-02 已移除 Portfolio、watchlist、PositionScorer 與 position_context 的執行路徑及相關 API consumer；前端與對外文件／API 技術規格同步改為三個研究入口。歷史持股資料、daily_analysis_log 與 stock_analysis_cache 仍保留，資料表及既有 migration 不可刪除，也不因功能退役清空歷史快取。

一般分析的 legacy/internal compatibility 欄位仍由既有 schema 管理；前端 primary UI 使用 risk_state、discipline_triggers、observation_conditions、risk_control_reference，不以舊的 recommended_action 等欄位產生買賣指令。之後移除相容欄位時，仍須先核對保留的 consumer、快取讀取與回歸測試。已退役持股 API 不再承諾這些欄位的相容回應。

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

### 主動式 ETF 每日持股追蹤

- **產品邊界**：這是獨立的來源揭露觀察面，不參與 Daily Radar universe、prefilter、score、bucket、risk label 或 ranking。持股股數增減也不得直接命名為基金經理人買進／賣出，因為 ETF 申購贖回可能讓整體持股等比例改變。
- **基金登錄**：每次 refresh 先讀 TWSE `/rwd/zh/ETF/activeList`，只納入六碼證券代號以 `A` 結尾的股票型主動式 ETF；`D` 結尾的債券型不在第一版範圍。基金移出官方清單時設為 disabled，歷史快照不得 cascade delete。若官方清單相較既有 enabled coverage 一次下降超過 20%，整次 registry sync fail closed，避免 partial response 大量誤停用。
- **持股來源**：MoneyDJ 公開「全部持股」頁是唯一持股 adapter，有資料即保存並供公開比較。Parser 只接受來源宣告日期、可辨識證券代號、非負整數股數與 0 到 100 的權重；重複股票、未來資料日、空表、缺欄資料列、超過 2,000 列、回應超過 5 MB 或欄位畸形時 fail closed。
- **原始證據**：`active_etf_source_observations` 與 `active_etf_source_holdings` 保存 MoneyDJ 的 provider、資料日、來源 URL、擷取時間、parser version、SHA-256、最多 5 MB 的 gzip 原始 payload 與正規化持股，供事後重播與來源稽核。切換前已存在的官方觀測不做破壞性刪除，但 runtime 與 public read path 不再讀取或顯示。
- **Canonical snapshot**：同基金／同 `data_date` 只有一份 MoneyDJ canonical snapshot。既有 `verification_status`、`source_count` 與 `verification_details` 欄位為滾動部署與歷史資料相容保留，新寫入固定為 `single_source`、`1` 與 `moneydj_only`。目前快照可和嚴格更早、最近一份 MoneyDJ 快照推導新增、增加、減少、移除；舊 `verified`／`conflict` 值在 public response 也正規化為 MoneyDJ 單一來源，不再封鎖變化或 consensus。
- **比較語意**：權重變化只作同列證據。相鄰資料日的持股股數完全相同時，基金仍為 `ready`、`change_count=0` 且 `changes=[]`，不得標成來源未更新。至少五檔連續持股時，以股數比率中位數估計共同基金規模倍率，`likely_fund_scale_change` 只表示原始增減接近共同倍率，不是交易結論。

#### `POST /internal/active-etf-holdings/refresh`

- 需既有 internal bearer token。
- Request body：`{"fund_codes": ["00985A"]}`；`fund_codes` 可省略，代表更新官方登錄內全部股票型主動式 ETF。
- 正式排程：`.github/workflows/active-etf-holdings.yml` 在台灣時間平日 08:00、19:00 呼叫全量 refresh；19:00 是取得 MoneyDJ 較完整當日持股的主要時段，08:00 保留為補抓。排程沿用 Zeabur backend URL 與 Daily Radar internal token secrets。成功建立／更新／重用的 MoneyDJ 快照數加上來源頁明確表示尚未公布的基金數，必須完整覆蓋 selected funds；其他 partial/error 或品質計數缺口讓 job 失敗。已完成的逐基金 transaction 不回滾。
- Response：`status`、`expected_funds`、`selected_funds`、`snapshots_created`、`snapshots_updated`、`snapshots_reused`、`single_source_snapshots` 與 privacy-safe `errors[{fund_code, code}]`。`verified_snapshots` 與 `conflicted_snapshots` 暫為相容欄位，MoneyDJ-only runtime 固定回傳 `0`。
- 官方 registry 無法驗證或指定基金不在官方清單時回 `502`，不得停用既有基金或建立推測資料。

#### `GET /active-etf-holdings/daily?data_date=YYYY-MM-DD`

- 需要應用程式登入 JWT。
- `data_date` 省略時使用目前資料庫最新來源宣告日。指定日期不存在或尚無任何快照時回 `404` 與 `active_etf_holdings_not_found`。
- Response 包含 `available_dates`、expected/usable coverage、summary、逐基金日期／前次日期／資料狀態、股數變化列，以及每個有變化個股的彙整；彙整達兩檔以上且方向一致時才可標記多基金共識。逐基金提供本期 MoneyDJ `sources[]`，以及依 `current`／`previous` 分期的 `evidence_periods[]`；每期各自包含資料日與 MoneyDJ 的 provider、URL、擷取時間、hash，不得用目前期來源冒充整段比較證據。相容欄位 `verification_status`／`source_count`／reason 固定投影為 `single_source`／`1`／`moneydj_only`，前端不得暴露 raw payload。
- 某基金沒有該 `data_date` MoneyDJ 快照時狀態為 `missing`；有目前快照但沒有嚴格更早快照時為 `no_baseline`，否則為 `ready`。`missing` 不得拿較舊資料混入當日統計，也不得捏造零基準變化。

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
    "macd_hist_pct": 0.1224,
    "macd_bias": "bullish",
    "prior_high_20d": 928.0,
    "prior_low_20d": 865.0,
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
    "version": "technical-layer-v5",
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
      "price_level_data_date": "2026-03-02",
      "price_level_completed_bars_only": true,
      "price_level_missing_reason": null,
      "missing_fields": []
    },
    "formula_versions": {
      "metrics": "technical-metrics-v5",
      "layering": "technical-layer-v5"
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

> **Canonical technical profile（2026-08-28）**：`technical_profile` 由 `backend/src/ai_stock_sentinel/technical/` 的 canonical metrics/profile builder 產生，Analyze、`persist_result: false` Watchlist quick lookup、`/analyze/position` 與 Daily Radar 共用同一套公式。`technical_profile.version` 目前為 `technical-layer-v5`；`score_summary.technical_score = round(50 + capped_total * (17 / 5))`，cap 或映射公式變更時必須升級版本並更新測試 fixture。`primary_score_inputs` 只放方向與可操作性核心證據，例如均線結構、支撐壓力、量能參與、MACD、OBV 與 ATR 支撐距離；`risk_overheat_filters` 只放過熱或高波動懲罰，例如 RSI、BIAS、Bollinger 與 ATR 高波動；`secondary_evidence` 只作輔助，不主導 primary score；`display_only` 保存 raw/display values，不影響 `score_summary`。支撐壓力 primary scoring 與 Daily Radar compatibility scoring 都必須使用當前 bar 之前的 20 根已完成 bar；`technical_indicators.prior_high_20d` / `prior_low_20d` 是可回放的判斷基準，`high_20d` / `low_20d` 則保留包含當前 bar 的純顯示值。`macd_hist_pct = macd_hist / close * 100` 是跨股價尺度比較與門檻判斷的 canonical 值，禁止用 MACD 原始絕對值套用跨股票固定門檻。`atr_risk` 與 `atr_state` 必須分離，前者只回答支撐/停損距離是否可控，後者才處理高波動懲罰，避免 ATR 重複計票。`data_quality` 必須含 `data_date`、`is_final`、lookback coverage、OHLCV/volume 對齊狀態、`price_level_basis` 與 `missing_fields`；OHLC high/low 不完整時，支撐壓力 primary signal 應以 missing/caveat 呈現，不計主要分。`required_lookback_days` 是 profile v5 的最低完整判斷門檻，較長週期訊號需在各 signal state/reason/caveats 或 `missing_fields` 中標示不足，不得只用全域 lookback 判定所有欄位完整。`chip_stability_context` 只能透過 `companion_context_refs` 關聯，不得進入任何 technical bucket 或 `score_summary`。

> **Phase 1 AVWAP Analyze projection（Phase 1B）**：`phase1_observation` 由 `phase1_avwap_snapshots` 以目前台北日期、登入使用者 managed universe 與 symbol 讀取。Analyze read path 可使用 requested date 當日或以前最新 fresh snapshot，最多回看 7 個 calendar days，避免台北日期已跨日但正式 snapshot 停在上一個交易日時誤判缺資料；response 會同時保留 snapshot `data_date` 與 `requested_data_date`。此欄位只作 evidence/data-quality trace，不進入 LangGraph initial state，不觸發 provider 即時查詢，也不擴張 managed universe。Snapshot 命中時回傳 AVWAP anchors、`freshness`、`missing_reason`、`source` 與 `data_quality`；每個 anchor 的 `distance_to_avwap_pct` 代表 `snapshot_close` 相對 AVWAP 的資料日距離，並以 `distance_basis = "snapshot_close"` 標示。Analyze read projection 會額外以當次 `snapshot.current_price` 產生 `current_distance_to_avwap_pct`、`current_price` 與 `current_distance_basis = "analyze_current_price"`，供 Analyze / Watchlist / copy-to-AI 顯示目前價格相對 AVWAP 的距離；這些 current 欄位只存在 response projection，不寫回 shared `phase1_avwap_snapshots` payload。未命中、過期或讀取失敗時用 non-blocking missing payload 表示，且不得讓 `/analyze` 主流程失敗。

> **`analysis_detail` 分維度欄位**（Session 8，2026-03-09）：
>
> - `tech_insight`：技術面獨立分析段落；可引用均線、RSI、布林通道、MACD、KD、ADX、OBV、ATR、MFI、Donchian Channel、支撐壓力位；禁止提及法人買賣超或新聞事件
> - `inst_insight`：籌碼面獨立分析段落；可引用三大法人、連續買賣超、主導買賣方、融資融券、借券、外資持股與大戶/散戶結構；禁止提及均線數值、RSI、新聞事件
> - `news_insight`：消息面獨立分析段落；禁止提及具體技術指標數值
> - `final_verdict`：三維整合仲裁段落；允許跨維度推論
>   以上四欄位若 LLM 未回傳或回傳空字串，均 fallback 為 `null`，不崩潰。

> **指標輸入與一致性（2026-09-08）**：`technical-metrics-v5` / `technical-layer-v5` 統一前20個完整交易日與唐奇安突破基準；v4 快取必須重算整組 profile 與原始指標。`technical_indicators.input_context` 保存歷史完整日K截至、指標模式／收盤確認、日線末根價格、HLC/量序列完整性、突破基準日、量來源／日期／盤中累計或全日狀態與一致性檢查。模式依快照自己的日期與 finality 判定，不使用輸出時的系統日期。獨立取得的 TWSE MIS 現價不插入日線序列；其 `market_quote_time` 及 `market_day_open/high/low` 與原始快照分開，來源時間缺失不能以 fetched_at 代替。即時現價可與已完成日線基準比較位置，但不能宣稱收盤突破。缺失 OHLC 不以收盤價填充；指標依賴的 HLC 缺失或日期錯位時輸出 null。均線與 AVWAP 顯示至少兩位小數，不使用交易跳動單位格式器；AVWAP 摘要距離以所列現價與未取整 AVWAP 重算。MACD 0.001 的顯示差異不能取代原精度驗證。
>
> 計算參數集中在 `technical/metrics.py` 的函式預設值，版本由 `technical/profile.py` 管理：MA / 布林中軌為 20 日 SMA，布林標準差 2 倍；MACD 為 12/26 EMA 與 9 EMA 訊號線；KD 為 9 日 RSV、K/D 各以 1/3 平滑（初值 50）；ADX/DMI 為 14 日 Wilder 平滑；OBV 短期比較末根與 5 個交易日前（6 根端點），唐奇安突破基準使用前20個完整交易日。日線來源與還原方式由 provider 寫入 `history_source/history_adjustment`（Yahoo 明確 auto_adjust=True），舊快取缺少來源時標未知。變更參數／平滑／基準時同步升級版本；輸出只列版本與來源，完整方法保留本文件。

> **MACD / OBV 跨日比較（2026-09-07）**：`macd_hist_previous`、`macd_hist_change_1d`、`obv_previous`、`obv_change_1d` 使用同一次輸入序列，前值由該序列移除最後一根日線後計算；不得從不同摘要相減。`indicator_data_date` / `indicator_previous_date` 標示這兩根日線日期，`obv_start_date` 標示首筆歸零日期。日期缺失時保留 null；價格與成交量日期不一致時 OBV 比較欄位不輸出。`macd_hist_slope_pct_3d` 保留相容欄位名稱，實際是三個交易日的柱體淨變化除以該分類資料日收盤價乘 100，UI / copy 標為「3日淨變化／股價」。`macd_trend_data_date` 為三日分類使用的已完成日線日期，盤中可能早於 `indicator_data_date`。單日改善可與三日轉弱並存；OBV 累積值受移動起點與重算影響，不能跨摘要解讀為資金進出。舊快取若有原始序列，重新計算整組 MACD / OBV 現值與比較值；沒有原始序列時不推算缺失欄位。

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
- 23:30 TWT：`POST /internal/daily-radar/refresh-market-context`，以 yfinance batch download 取得指數，先正規化 single-symbol MultiIndex，並以整列方式排除任一必要 OHLCV 為空或非有限值的資料；失敗、空資料或非同一 `run_date` 時再使用同 provider 的 `Ticker.history` bounded fallback。若 yfinance 歷史完整但落後 `run_date`，或雖有 `run_date` 當日列但最後兩筆日期之間仍夾有未經驗證的平日，最後逐日查詢 TWSE `MI_INDEX` 的「發行量加權股價指數」，略過官方明確 no-data 的休市日，並補齊 yfinance 最後一個連續完整日之後至 `run_date` 的官方收盤；每一筆官方回推的 previous close 都必須與前一筆 yfinance／TWSE close 在容許誤差內一致，任一日期 request、payload 或銜接失敗都不得部分合併。補齊後以完整 closes 重算 MA20/MA60，ATR 必須使用 gap 前最後完整 yfinance 日並將日期另存於 `benchmark.data_dates.market_volatility` / `market.volatility_data_date`，不得放進會合併為 candidate core freshness 的 root `data_dates`；`provider_trace` 保留 official/history provider、`official_dates` 與 fallback 路徑。只有 record date 與 index data date 都精確等於 `run_date`、fresh 且 regime 可判定的 market context 才把 step 標成 `completed`；missing/stale/指標不足或跨來源 previous-close 不一致必須標 `failed` 並阻擋 scoring。若同一 run 已有通過驗證的 context，暫時性重抓失敗沿用既有資料且不得用缺漏 payload 覆寫。
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
- `POST /internal/fundamentals/backfill`：對 request `symbols` 或 managed universe 做 bounded 歷史 bootstrap；EPS 缺漏先查 MOPS 官方歷史季資料，MOPS 失敗或寫入後歷史仍不足才查 FinMind 財報，股利歷史仍由 FinMind 補齊。managed universe 合併 active holdings、watchlist、最新 prepared universe 與最近一次完成的 AI raw pool。第一頁排除已完整 symbols，並把 immutable snapshot、`raw_pool_date`、server-owned cursor 保存到 `fundamental_backfill_jobs`；後續頁帶 `job_id`/cursor 並以 row lock 驗證。所有未帶 `job_id`、可能建立新 job 的入口都先取得同一 PostgreSQL transaction advisory lock 並檢查 running job；已存在時一般 create 回 `409 fundamental_backfill_job_running`，scheduled `resume_running_job=true` 則接續該 job，封住跨 caller 的 no-row create race。日期未完成、job 不存在、cursor 未帶 job、job completed 或 cursor 不一致一律 fail closed。每頁最多 10 檔；MOPS 單檔使用 5 秒 timeout、一次 attempt，失敗立即降級；FinMind client 使用 10 秒 timeout、零 transport retry、停用 token-expired 自動重試。六批最壞 logical upstream bound 為 60 次 MOPS + 120 次 FinMind（財報與股利）共 180 次。回應以 `provider_attempts` 計數各來源呼叫，並在 `fallback_symbols` 列出 MOPS 後仍需 FinMind 財報的股票。任一 lane 失敗時回 `partial`，成功寫入仍保留，cursor 前移過本頁已嘗試 symbols 以免永久錯誤餓死後續佇列；失敗 symbols 保持 cache incomplete，於 current job 結束後進入新 job。有效回應但 EPS 歷史為空或不足時，維持 `partial` 並在新增的 `data_gaps` 列出 `symbol`、`reason`（`no_eps_history` 或 `insufficient_eps_history`）、`period_count`；未被 fallback 修復的來源／格式／寫入異常保留於 `errors`。舊 response 欄位保留，完整性門檻不變。Workflow 收到 partial 仍接續 cursor/job，至最後一頁或六批上限；跨頁缺口與錯誤寫入 Actions summary，僅有 data gaps 不使 backfill 失敗，有 errors 則在處理完本次可執行頁面後非零結束。手動與 scheduled run 額度用完皆正常保存進度並由下一個平日 07:15 排程接續；未指定 job/cursor/raw-pool date 的手動啟動也會帶 `resume_running_job=true`。舊後端未提供 data_gaps 時，原有 errors 仍保守視為失敗。HTTP／回應契約錯誤仍立即失敗，不猜測續跑游標；官方 refresh 的 failure 回報不變。
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
  - `input_snapshot.replay_input` 自 `daily-radar-replay-input-v1` 起保存完整 deterministic scoring input、baseline `ScoringConfig` 與 config version。v2 起將支撐壓力改為 prior-window，並以 `macd_hist_pct` 套用跨股票門檻；舊候選缺少此欄位或版本不符時，月報必須標記 `replay_input_incomplete`，不得猜測。
  - Current version trace：`daily-radar-scoring-v2.7` / `daily-radar-rules-v2.6` / `daily-radar-scoring-config-v2`。v2.3 起，缺少必要 scoring inputs 會標記 `data_gap`，缺值本身不得觸發正向規則；v2.4 scoring 起，legacy `same_day_institutional` 候選會以合法的單一法人正數淨買超計入同日法人分數；v2.5 scoring / v2.4 rules 起，archive-backed `foreign_same_day` / `trust_same_day` 與 actor-specific 近期累積淨買超也會進入同一組互斥法人規則，且不與三大法人合計轉正或外資投信方向一致重複計分；v2.6 scoring / v2.5 rules 起，支撐壓力排除訊號當日 bar，MACD 固定門檻改用相對收盤價百分比；v2.7 scoring / v2.6 rules 起，同 bucket 的相同訊號家族套用正向分數 cap，負向規則維持完整扣分，breakdown 保留 raw、effective 與 capped points。

- **Calibration workflow**
  - Daily Radar calibration report 可由 `uv run python scripts/daily_radar_calibration.py --source fixture --run-date 2026-05-29` 重跑。
  - Report 是 deterministic JSON，包含 sample count、bucket distribution、rank cutoff impact、bucket threshold impact、risk/overheat impact、relative strength impact、skip reasons 與 version manifest。
  - Calibration report 不改 live scoring 行為，不宣稱勝率、價格承諾或交易指令。

#### Internal calibration lifecycle

- `POST /internal/daily-radar/forward-validation/run`：以 `mode = due` 評估最新公開 run 中已成熟的 5 / 10 / 20 交易日窗口；同日 rerun 只採最新公開 run。
  - `daily-radar-forward-validation-report-v2` 的 production report 會在 upsert 後重新讀取已持久化的固定日期 cohort，避免 due rerun 只回傳本批新到期窗口。`selection_diagnostics` 分成 `selected`、可比較 `shadow` 與 `eligibility_audit`，逐 cohort 揭露驗證／跳過率，並同時輸出 absolute-positive 與 benchmark-outperformance 的 conditional precision、observed-pool recall 與 shadow miss share。這些指標只描述目前 Daily Radar universe 內且可驗證的比較池，不代表全市場召回率；既有 bucket／rule／risk／ablation 報表維持 selected-only。
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
- Daily Radar `daily-radar-rule-review-v6` 為最新公開 run 的全部 candidate 建立 5 / 10 / 20 日完整池；validation result 不存在時保留 `status = missing`，不得讓候選從 ranking pool 消失。Replay eligibility 必須驗證 schema、current scoring/rule/config versions、baseline config、record identity/date、必要 scoring fields 的有限數值、data dates、accepted prefilter 與 technical profile，不得只看 `schema_version` 或空容器。結構驗證通過後，baseline replay 還必須逐 candidate 重現原 production 的 observation score、primary/secondary buckets、bucket scores、risk labels 與 matched rule IDs；任一不一致以 `baseline_replay_mismatch` 排除，並將該交易日／窗口 ranking pool 標為 `incomplete`。90% replay coverage 保留作資料品質診斷，但任何 ranking／counterfactual governance 必須逐交易日、逐窗口具備 100% replay ranking pool；不完整時輸出 `ranking_pool_status = incomplete`、`replay_ranking_pool_incomplete` 並禁止調整資格，沒有成熟 cohort 時則為 `not_applicable`，active ablation 輸出 `not_applicable_no_cohort` 而不執行 replay。除單一交易日／窗口 production cap 250 candidates 外，月報在 scoring 前另計算 aggregate workload：最多 300,000 次 estimated scoring calls 與 220,000,000 bootstrap row-iterations，且 estimated scoring calls 必須包含每個 active group 的全域 ablation與其實際存在的 bucket-owned rules 局部 ablation；任一超限即輸出 `capacity_exceeded`／`replay_workload_limit_exceeded` 並停止 replay。報表共用一次 baseline replay，同一 config 下每個 candidate 只 scoring 一次再投影到三個 outcome windows；mean bootstrap 將 validated selected rows 預聚合為每日期 sum／count 後重抽統計量，每次抽樣仍先把 before／after 平均值四捨五入至四位再計算 delta，並以完整 training dates 作 block universe，保留無 selected rows 日期的統計語意。結果最後接 validated outcome 並輸出 `counterfactual_ablation_summary`；只有 live-score tiers 可執行 counterfactual ablation，context-only 群組輸出 `not_in_live_score`。v6 另以 baseline primary bucket 固定 cohort 輸出 `bucket_impacts`，且每個 bucket 只移除該 bucket 擁有的 group rules，避免其他 bucket 同時變動造成錯誤因果歸因；`co_occurrence_summary` 僅是相關性診斷。任何 recommendation 都不直接更新 live config、rule version 或 ranking。
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

未預期例外的完整內容只能寫入 backend log；API `message` 必須使用穩定且不含 provider、連線細節、憑證或 stack trace 的使用者文案。Frontend 依 `code` 轉成顯示文字，不能直接呈現未知 code 或原始 `message`。

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

### 技術指標比較證據

行情狀態與指標模式分開顯示，快照擷取時間不推定為行情交易日。MACD 單日增減是否收盤確認依指標末根日K判定；三日比較使用完整日K，提供 `macd_trend_hist`、`macd_hist_3d_previous`、`macd_hist_change_3d`、比較日期與分母 `macd_trend_price`，不與盤中末柱混用。

MA20 5日斜率為 `(MA20[t] / MA20[t-5] - 1) × 100%`；MA60 10日斜率為 `(MA60[t] / MA60[t-10] - 1) × 100%`，t 均為完整日K末日，非線性回歸。OBV 訊號提供實際比較窗、起日價格與累積值、末日價格及價格百分比與 OBV 淨變化，所有累積值共用此次序列起點。

唐奇安價格位置以原始精度分為低於、觸及下緣、通道內、觸及上緣、高於；突破事件另列。行情交易日未知或未晚於突破基準日期時，事件不可確認。單一越界快照不證明本次新發生交叉。

## Daily Radar 後端模組邊界

`daily_radar/router.py` 僅組裝 HTTP 子路由；拆分不改變 URL、驗證、回應 schema 或 public/internal 邊界。

- `dependencies.py`：provider factories、共用政策常數與日期來源。
- `refresh_router.py`：prepared universe 與必要資料刷新。
- `evidence_router.py`：managed raw data 與 AI 研究證據刷新。
- `maintenance_router.py`：回補、交易日判定、forward validation 與 rule review。
- `run_router.py`：完整執行與 prepared scoring。
- `read_router.py`：唯讀公開查詢。
- `pipeline_support.py`：prepared-run 驗證與資料彙整；`institutional_payloads.py`：法人資料轉換。

測試透過 `dependencies.py` 的同一個 dependency object 覆寫 provider；公開查詢依然只讀已保存的資料。
