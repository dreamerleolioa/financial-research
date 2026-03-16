# AI Stock Sentinel 後端 API 技術規格（v3）

> 類型：技術文件（Technical Doc）
> 更新日期：2026-03-16
> 更新摘要：補充 `/analyze` 與 `/analyze/position` 的策略語義邊界：`/analyze` 底部策略區塊正式定位為「新倉策略建議」，`/analyze/position` 維持持股操作建議；並明確定義 `action_plan` / `strategy_type` / `entry_zone` / `stop_loss` 為 rule-based 新倉策略輸出，非 LLM 直接產生的買賣指令。

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
    "technical_signal": "bullish",
    "institutional_flow": "institutional_accumulation",
    "sentiment_label": "positive",
    "tech_insight": "均線多頭排列，RSI 62 位於健康動能區，短線無超買疑慮。",
    "inst_insight": "外資近 5 日累計買超 12,500 張，籌碼持續沉澱，機構資金流向偏多。",
    "news_insight": "法說會利多消息帶動市場情緒正面，事件時效性已驗證（日期明確）。",
    "final_verdict": "三維訊號共振：技術面健康、籌碼面偏多、消息面正面，信心分數 78 反映訊號一致性高。"
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

  | 欄位                       | 類型           | 說明                                                                                                                                                                                                          |
  | -------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `snapshot`                 | object         | yfinance 即時快照                                                                                                                                                                                             |
  | `analysis`                 | string         | LLM Skeptic Mode 四步驟完整分析文字                                                                                                                                                                           |
  | `cleaned_news`             | object \| null | LLM pipeline 消費用的新聞結構（`sentiment_label`、`mentioned_numbers` 等）；無新聞時為 null                                                                                                                   |
  | `news_display`             | object \| null | 前端顯示用的新聞資料（乾淨 RSS 標題、ISO 日期、來源 URL）；無新聞時為 null                                                                                                                                    |
  | `cleaned_news_quality`     | object \| null | 新聞摘要品質評估（`quality_score: 0-100`、`quality_flags: string[]`）；無新聞時為 null                                                                                                                        |
  | `data_confidence`          | int \| null    | 0–100，資料完整度（成功取得的維度數量，CS-4 新增）                                                                                                                                                            |
  | `signal_confidence`        | int \| null    | 0–100，訊號強度（CS-4 新增；`confidence_score` 為向後相容別名）                                                                                                                                               |
  | `confidence_score`         | int \| null    | 0–100，反映三維訊號一致性（= `signal_confidence`，向後相容）                                                                                                                                                  |
  | `cross_validation_note`    | string \| null | 三維交叉驗證結論簡述（rule-based 固定字串）                                                                                                                                                                   |
  | `strategy_type`            | enum \| null   | `short_term` / `mid_term` / `defensive_wait`                                                                                                                                                                  |
  | `entry_zone`               | string \| null | 建議入場區間（rule-based）                                                                                                                                                                                    |
  | `stop_loss`                | string \| null | 防守底線／停損條件（rule-based）                                                                                                                                                                              |
  | `holding_period`           | string \| null | 預期持股期間（rule-based）                                                                                                                                                                                    |
  | `analysis_detail`          | object \| null | LLM 結構化分析輸出，包含 `summary` / `risks` / `technical_signal` / `institutional_flow` / `sentiment_label` / `tech_insight` / `inst_insight` / `news_insight` / `final_verdict`（Session 8 新增分維度欄位） |
  | `sentiment_label`          | string \| null | 新聞情緒標籤（從 `cleaned_news.sentiment_label` 浮出）：`positive` / `negative` / `neutral`                                                                                                                   |
  | `action_plan`              | object \| null | rule-based 新倉戰術行動計劃（含 `action` / `target_zone` / `defense_line` / `momentum_expectation`）；不表示持股中的出場/減碼指令                                                                             |
  | `data_sources`             | array          | 本次實際成功取得資料的來源列表（如 `["google-news-rss", "yfinance", "twse-openapi"]`）                                                                                                                        |
  | `institutional_flow_label` | enum \| null   | 籌碼歸屬標籤：`institutional_accumulation` / `retail_chasing` / `distribution` / `neutral`                                                                                                                    |
  | `action_plan_tag`          | enum \| null   | 燈號標籤（rule-based，後端計算）：`opportunity` / `overheated` / `neutral`；前端僅做顯示映射                                                                                                                  |
  | `errors`                   | array          | 錯誤碼陣列                                                                                                                                                                                                    |

> **策略產生邊界（`POST /analyze`）**：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`、`action_plan`、`action_plan_tag` 皆由後端 Python rule-based 邏輯產出；LLM 可參與分析文字、新聞情緒或綜合敘事生成，但**不得直接輸出最終進場指令**。

> **`analysis_detail` 分維度欄位**（Session 8，2026-03-09）：
>
> - `tech_insight`：技術面獨立分析段落；禁止提及法人買賣超或新聞事件
> - `inst_insight`：籌碼面獨立分析段落；禁止提及均線數值、RSI、新聞事件
> - `news_insight`：消息面獨立分析段落；禁止提及具體技術指標數值
> - `final_verdict`：三維整合仲裁段落；允許跨維度推論
>   以上四欄位若 LLM 未回傳或回傳空字串，均 fallback 為 `null`，不崩潰。

---

### `POST /analyze/position`

- **用途**：持股診斷——以使用者購入成本價為錨點，評估當前倉位健康度、動態停利/停損位，以及出場建議（詳見 [持股診斷系統技術規格](./ai-stock-sentinel-position-diagnosis-spec.md)）
- **產品語義**：此端點是持股中的操作建議唯一真相來源；`recommended_action` / `exit_reason` 才對應續抱 / 減碼 / 出場等當前操作判斷

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
    "fetched_at": "2026-03-09T00:00:00+00:00"
  },
  "technical": {
    "ma5": 1098.4,
    "ma20": 1055.2,
    "ma60": 1010.5,
    "bias_ma20": 4.72,
    "rsi14": 65.1,
    "volume_change_pct": 12.3,
    "support_20d": 1040.0,
    "resistance_20d": 1120.0
  },
  "institutional": {
    "foreign_net": 18500,
    "trust_net": 2100,
    "dealer_net": 400,
    "margin_balance_delta": 1200,
    "short_balance_delta": -800
  },
  "position_analysis": {
    "entry_price": 980.0,
    "profit_loss_pct": 12.76,
    "position_status": "profitable_safe",
    "position_narrative": "目前獲利已脫離成本區，持股安全緩衝充足。",
    "recommended_action": "Hold",
    "trailing_stop": 980.0,
    "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價保本",
    "exit_reason": null
  },
  "data_confidence": 100,
  "signal_confidence": 79,
  "confidence_score": 79,
  "cross_validation_note": "三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高",
  "analysis_detail": {
    "summary": "台積電法人持續買超，RSI 動能尚未過熱，多頭格局延續。",
    "risks": ["RSI 接近超買區間，短線留意拉回壓力"],
    "technical_signal": "bullish",
    "institutional_flow": "institutional_accumulation",
    "sentiment_label": "positive",
    "tech_insight": "均線多頭排列，RSI 65 位於健康動能區，尚未進入超買。",
    "inst_insight": "外資近 5 日累計買超 18,500 張，籌碼持續沉澱。",
    "news_insight": "法說會消息偏正向，事件時效性已驗證。",
    "final_verdict": "三維訊號共振，持股健康，目前無出場訊號。"
  },
  "institutional_flow_label": "institutional_accumulation",
  "action_plan": {
    "action": "續抱",
    "target_zone": null,
    "defense_line": "980.0（成本保本線）",
    "momentum_expectation": "法人持續買超，動能延續"
  },
  "action_plan_tag": "opportunity",
  "data_sources": ["google-news-rss", "yfinance", "finmind"],
  "errors": []
}
```

- **欄位說明**

  | 欄位                       | 類型           | 說明                                                                         |
  | -------------------------- | -------------- | ---------------------------------------------------------------------------- |
  | `snapshot`                 | object         | yfinance 即時快照（與 `/analyze` 相同）                                      |
  | `technical`                | object         | 技術指標（與 `/analyze` 相同，額外含 `support_20d` / `resistance_20d`）      |
  | `institutional`            | object         | 法人籌碼資料（與 `/analyze` 相同）                                           |
  | `position_analysis`        | object         | **持股診斷專屬**——見下方欄位細節                                             |
  | `data_confidence`          | int \| null    | 0–100，資料完整度                                                            |
  | `signal_confidence`        | int \| null    | 0–100，訊號強度                                                              |
  | `confidence_score`         | int \| null    | = `signal_confidence`，向後相容                                              |
  | `cross_validation_note`    | string \| null | 三維交叉驗證結論（rule-based 固定字串）                                      |
  | `analysis_detail`          | object \| null | LLM 結構化分析輸出（持股版 System Prompt，強化出場推理）                     |
  | `institutional_flow_label` | enum \| null   | `institutional_accumulation` / `retail_chasing` / `distribution` / `neutral` |
  | `action_plan`              | object \| null | 持股版戰術行動（`action` 為 `續抱` / `減碼` / `出場`）                       |
  | `action_plan_tag`          | enum \| null   | `opportunity` / `overheated` / `neutral`                                     |
  | `data_sources`             | array          | 本次成功取得資料的來源列表                                                   |
  | `errors`                   | array          | 錯誤碼陣列                                                                   |

- **`position_analysis` 欄位細節**

  | 欄位                   | 類型           | 說明                                                 |
  | ---------------------- | -------------- | ---------------------------------------------------- |
  | `entry_price`          | float          | 購入成本價（回傳確認）                               |
  | `profit_loss_pct`      | float          | 當前損益百分比（rule-based Python 計算）             |
  | `position_status`      | string         | `profitable_safe` / `at_risk` / `under_water`        |
  | `position_narrative`   | string         | 倉位狀態敘事（rule-based，供 LLM 讀取）              |
  | `recommended_action`   | string         | `Hold` / `Trim` / `Exit`（rule-based，LLM 不得覆寫） |
  | `trailing_stop`        | float          | 動態防守價位（rule-based Python 計算）               |
  | `trailing_stop_reason` | string         | 停利/停損邏輯說明                                    |
  | `exit_reason`          | string \| null | 出場/減碼理由；無觸發條件時為 `null`                 |

> **`recommended_action` 判斷規則（rule-based，後端計算）**：
>
> - `flow_label = distribution` 且 `profit_loss_pct > 0` → `Trim`
> - `flow_label = distribution` 且 `profit_loss_pct <= 0` → `Exit`
> - `technical_signal = bearish` 且 `close < trailing_stop` → `Exit`
> - `position_status = under_water` 且 `profit_loss_pct < -10%` → `Exit`
> - 其他 → `Hold`

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
  - 有 `cleaned_news` 的成功路徑
  - `raw_news_items` 不對外暴露
  - 請求驗證錯誤（422）
  - graph 執行期例外 → `ANALYZE_RUNTIME_ERROR`
  - graph 最終 state 缺 snapshot/analysis → `MISSING_SNAPSHOT` / `MISSING_ANALYSIS`
  - graph 執行期累積的 errors 傳遞到 response
- 測試檔（持股診斷）：`backend/tests/test_position_api.py`
- 覆蓋項目（持股診斷）：
  - 持股診斷成功路徑（`position_analysis` 物件完整性）
  - `entry_price` 為負數 → `422` + `INVALID_ENTRY_PRICE`
  - `flow_label = distribution` 且獲利中 → `recommended_action = Trim`、`exit_reason` 非 null
  - `position_status = under_water` 且 `profit_loss_pct < -10%` → `recommended_action = Exit`
  - `PositionScorer` 計算失敗 → `POSITION_SCORE_ERROR`（流程繼續，`position_analysis` 降級為 null）
