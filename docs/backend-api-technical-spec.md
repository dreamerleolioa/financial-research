# AI Stock Sentinel 後端 API 技術規格（v2）

> 類型：技術文件（Technical Doc）  
> 更新日期：2026-03-03  
> 更新摘要：Response 新增 `technical`、`institutional`、`analysis_detail` 區塊，反映全方位數據偵察架構升級

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
  "analysis": "全文分析自然語詞結果",
  "analysis_detail": {
    "summary": "事實型摘要（去情緒化後的核心資訊）",
    "sentiment_label": "positive",
    "confidence_score": 78,
    "technical_signal": "bullish",
    "institutional_flow": "institutional_accumulation",
    "cross_validation_note": "外資連買 3 日，與新聞利多訊號一致，信心分數維持高位",
    "risks": ["RSI 接近超買區間 (>70)，短線需注意回測"],
    "data_sources": ["google-news-rss", "yfinance", "twse-openapi"]
  },
  "cleaned_news": {
    "date": "2026-03-03",
    "title": "台積電 2 月營收年增",
    "mentioned_numbers": ["2,600", "18.2%"],
    "sentiment_label": "positive"
  },
  "errors": []
}
```

- **欄位說明**

  | 欄位 | 類型 | 說明 |
  |------|------|------|
  | `snapshot` | object | yfinance 即時快照 |
  | `technical` | object | **Python 函式計算**的技術指標，禁止 LLM 推算 |
  | `institutional` | object | 三大法人買賣超及融資融券數据，單位千股 |
  | `analysis` | string | LLM 完整分析自然語語段 |
  | `analysis_detail.technical_signal` | enum | `bullish` / `bearish` / `sideways` |
  | `analysis_detail.institutional_flow` | enum | `institutional_accumulation` / `retail_chasing` / `distribution` / `neutral` |
  | `analysis_detail.cross_validation_note` | string | 三維交叉驗證結論簡述 |
  | `analysis_detail.confidence_score` | int | 0–1001，反映三維訊號一致性 |
  | `cleaned_news` | object | 去情緒化後的新聞結構 |
  | `errors` | array | 錯誤碼陣列 |

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
- `CROSS_VALIDATION_ERROR`：`analyze_node` 執行多維交叉驗證通箇失敗

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
