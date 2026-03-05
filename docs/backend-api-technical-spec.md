# AI Stock Sentinel 後端 API 技術規格（v2）

> 類型：技術文件（Technical Doc）
> 更新日期：2026-03-05
> 更新摘要：新增 `news_display` 欄位（新聞顯示資料，含乾淨標題/日期/來源 URL）；新增 `cleaned_news_quality` 欄位；`cleaned_news` 角色重新定義為 LLM pipeline 專用

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
    "fetched_at": "2026-03-03T00:00:00+00:00"
  },
  "technical": {
    "ma5": 918.4,
    "ma20": 905.2,
    "ma60": 880.5,
    "bias_ma20": 2.18,
    "rsi14": 62.3,
    "volume_change_pct": 23.5
  },
  "institutional": {
    "foreign_net": 12500,
    "trust_net": -3200,
    "dealer_net": 800,
    "margin_balance_delta": 4500,
    "short_balance_delta": -1200
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
    "summary": "台積電法人持續買超，RSI 動能尚未過熱，多頭格局延續。",
    "risks": ["短線乖離偏高，留意拉回壓力"],
    "technical_signal": "bullish"
  },
  "sentiment_label": "positive",
  "action_plan": {
    "action": "分批買進",
    "target_zone": "900.0–915.0（support_20d ~ MA20）",
    "defense_line": "880.5（近20日低點×0.97）或跌破 MA60",
    "momentum_expectation": "法人持續買超，技術面健康，動能延續"
  },
  "data_sources": ["google-news-rss", "yfinance", "twse-openapi"],
  "institutional_flow_label": "institutional_accumulation",
  "strategy_type": "mid_term",
  "entry_zone": "現價附近分批買進",
  "stop_loss": "近20日低點 - 3% 或跌破 MA60",
  "holding_period": "1-3 個月",
  "action_plan_tag": "opportunity",
  "errors": []
}
```

- **欄位說明**

  | 欄位 | 類型 | 說明 |
  |------|------|------|
  | `snapshot` | object | yfinance 即時快照 |
  | `analysis` | string | LLM Skeptic Mode 四步驟完整分析文字 |
  | `cleaned_news` | object \| null | LLM pipeline 消費用的新聞結構（`sentiment_label`、`mentioned_numbers` 等）；無新聞時為 null |
  | `news_display` | object \| null | 前端顯示用的新聞資料（乾淨 RSS 標題、ISO 日期、來源 URL）；無新聞時為 null |
  | `cleaned_news_quality` | object \| null | 新聞摘要品質評估（`quality_score: 0-100`、`quality_flags: string[]`）；無新聞時為 null |
  | `data_confidence` | int \| null | 0–100，資料完整度（成功取得的維度數量，CS-4 新增） |
  | `signal_confidence` | int \| null | 0–100，訊號強度（CS-4 新增；`confidence_score` 為向後相容別名） |
  | `confidence_score` | int \| null | 0–100，反映三維訊號一致性（= `signal_confidence`，向後相容） |
  | `cross_validation_note` | string \| null | 三維交叉驗證結論簡述（rule-based 固定字串） |
  | `strategy_type` | enum \| null | `short_term` / `mid_term` / `defensive_wait` |
  | `entry_zone` | string \| null | 建議入場區間（rule-based） |
  | `stop_loss` | string \| null | 防守底線／停損條件（rule-based） |
  | `holding_period` | string \| null | 預期持股期間（rule-based） |
  | `analysis_detail` | object \| null | LLM 結構化分析輸出（`summary` / `risks` / `technical_signal`），Task 7 新增 |
  | `sentiment_label` | string \| null | 新聞情緒標籤（從 `cleaned_news.sentiment_label` 浮出）：`positive` / `negative` / `neutral`；⚠️ **計劃中（Day 2 Session 3，尚未實作）** |
  | `action_plan` | object \| null | rule-based 戰術行動計劃（含 `action` / `target_zone` / `defense_line` / `momentum_expectation`）；⚠️ **計劃中（Day 2 Session 3，尚未實作）** |
  | `data_sources` | array | 本次實際成功取得資料的來源列表（如 `["google-news-rss", "yfinance", "twse-openapi"]`）；⚠️ **計劃中（Day 2 Session 3，尚未實作）** |
  | `institutional_flow_label` | enum \| null | 籌碼歸屬標籤：`institutional_accumulation` / `retail_chasing` / `distribution` / `neutral`；⚠️ **計劃中（Day 1 Session 2，尚未實作）** |
  | `action_plan_tag` | enum \| null | 燈號標籤（rule-based，後端計算）：`opportunity` / `overheated` / `neutral`；前端僅做顯示映射；⚠️ **計劃中（Day 1 Session 2，尚未實作）** |
  | `errors` | array | 錯誤碼陣列 |

> ⚠️ **計劃中欄位（尚未實作）**：
> - `action_plan_tag`、`institutional_flow_label`：對應 `docs/plans/2026-03-06-spec-gap-fix-day1.md` Day 1 Session 2
> - `sentiment_label`（頂層）、`action_plan`（dict）、`data_sources`：對應 `docs/plans/2026-03-07-spec-gap-fix-day2.md` Day 2 Session 3
> Response example 中的值為規格目標，非目前實際回傳。
> ⚠️ **即將變更**：`news_display`（單筆 object）計劃升級為 `news_display_items`（陣列，最多 5 筆），對應 NM-3~NM-5（`docs/plans/2026-03-06-news-scope-and-display-items.md`）。

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

---

## 5) 驗證錯誤（422）

當 request body 不符合 schema（例如 `symbol` 為空字串），API 會回傳 `422 Unprocessable Entity`。

---

## 6) 測試對應

- 測試檔：`backend/tests/test_api.py`
- 覆蓋項目：
  - 健康檢查
  - 分析成功路徑（snapshot + analysis）
  - 有 `cleaned_news` 的成功路徑
  - `raw_news_items` 不對外暴露
  - 請求驗證錯誤（422）
  - graph 執行期例外 → `ANALYZE_RUNTIME_ERROR`
  - graph 最終 state 缺 snapshot/analysis → `MISSING_SNAPSHOT` / `MISSING_ANALYSIS`
  - graph 執行期累積的 errors 傳遞到 response
