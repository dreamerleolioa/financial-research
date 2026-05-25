# AI Stock Sentinel 後端 API 技術規格（v5）

> 類型：技術文件（Technical Doc）
> 更新日期：2026-05-25
> 更新摘要：同步技術面、持股診斷、個人持股上限與 LLM input 穩定化完成狀態；`technical_indicators` 對外欄位新增 KD / ADX / OBV；`position_analysis` 新增防守線距離、支撐距離、未實現損益與持有天數；個人 active 持股上限調整為 8 筆；更新 `/analyze`、`/analyze/position` 與 `/portfolio` contract；補充 `signal_summary` 為內部 LLM input contract，不屬於 API response；更新 `tech_insight` 與測試對應說明。

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
    "final_verdict": "三維訊號共振：技術面健康、籌碼面偏多、消息面正面，信心分數 78 反映訊號一致性高。"
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
  "errors": []
}
```

- **欄位說明**

  | 欄位                       | 類型           | 說明                                                                                                                                                                                                                                                                                            |
  | -------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `snapshot`                 | object         | yfinance 即時快照                                                                                                                                                                                                                                                                               |
  | `analysis`                 | string         | LLM Skeptic Mode 四步驟完整分析文字                                                                                                                                                                                                                                                             |
  | `cleaned_news`             | object \| null | LLM pipeline 消費用的新聞結構（`sentiment_label`、`mentioned_numbers` 等）；無新聞時為 null                                                                                                                                                                                                     |
  | `news_display`             | object \| null | 前端顯示用的新聞資料（乾淨 RSS 標題、ISO 日期、來源 URL）；無新聞時為 null                                                                                                                                                                                                                      |
  | `cleaned_news_quality`     | object \| null | 新聞摘要品質評估（`quality_score: 0-100`、`quality_flags: string[]`）；無新聞時為 null                                                                                                                                                                                                          |
  | `data_confidence`          | int \| null    | 0–100，資料完整度（成功取得的維度數量，CS-4 新增）                                                                                                                                                                                                                                              |
  | `signal_confidence`        | int \| null    | 0–100，訊號強度（CS-4 新增；`confidence_score` 為向後相容別名）                                                                                                                                                                                                                                 |
  | `confidence_score`         | int \| null    | 0–100，反映三維訊號一致性（= `signal_confidence`，向後相容）                                                                                                                                                                                                                                    |
  | `cross_validation_note`    | string \| null | 三維交叉驗證結論簡述（rule-based 固定字串）                                                                                                                                                                                                                                                     |
  | `strategy_type`            | enum \| null   | `short_term` / `mid_term` / `defensive_wait`                                                                                                                                                                                                                                                    |
  | `entry_zone`               | string \| null | 建議入場區間（rule-based）                                                                                                                                                                                                                                                                      |
  | `stop_loss`                | string \| null | 防守底線／停損條件（rule-based）                                                                                                                                                                                                                                                                |
  | `holding_period`           | string \| null | 預期持股期間（rule-based）                                                                                                                                                                                                                                                                      |
  | `analysis_detail`          | object \| null | LLM 結構化分析輸出，包含 `summary` / `risks` / `technical_signal` / `institutional_flow` / `sentiment_label` / `tech_insight` / `inst_insight` / `news_insight` / `final_verdict`（Session 8 新增分維度欄位）                                                                                   |
  | `technical_indicators`     | object \| null | 技術指標顯性輸出，包含布林通道、MACD、KD、ADX、OBV 數值與標籤（詳見下方 `technical_indicators` 欄位說明）                                                                                                                                                                                       |
  | `sentiment_label`          | string \| null | 新聞情緒標籤（從 `cleaned_news.sentiment_label` 浮出）：`positive` / `negative` / `neutral`                                                                                                                                                                                                     |
  | `action_plan`              | object \| null | rule-based 新倉戰術行動計劃（含 `action` / `target_zone` / `defense_line` / `momentum_expectation` / `breakeven_note` / `conviction_level` / `thesis_points` / `upgrade_triggers` / `downgrade_triggers` / `invalidation_conditions` / `suggested_position_size`）；不表示持股中的出場/減碼指令 |
  | `data_sources`             | array          | 本次實際成功取得資料的來源列表（如 `["google-news-rss", "yfinance", "twse-openapi"]`）                                                                                                                                                                                                          |
  | `institutional_flow_label` | enum \| null   | 籌碼歸屬標籤：`institutional_accumulation` / `retail_chasing` / `distribution` / `neutral`                                                                                                                                                                                                      |
  | `action_plan_tag`          | enum \| null   | 燈號標籤（rule-based，後端計算）：`opportunity` / `overheated` / `neutral`；前端僅做顯示映射                                                                                                                                                                                                    |
  | `errors`                   | array          | 錯誤碼陣列                                                                                                                                                                                                                                                                                      |

> **策略產生邊界（`POST /analyze`）**：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`、`action_plan`、`action_plan_tag` 皆由後端 Python rule-based 邏輯產出；LLM 可參與分析文字、新聞情緒或綜合敘事生成，但**不得直接輸出最終進場指令**。

> **`analysis_detail` 分維度欄位**（Session 8，2026-03-09）：
>
> - `tech_insight`：技術面獨立分析段落；可引用均線、RSI、布林通道、MACD、KD、ADX、OBV、支撐壓力位；禁止提及法人買賣超或新聞事件
> - `inst_insight`：籌碼面獨立分析段落；禁止提及均線數值、RSI、新聞事件
> - `news_insight`：消息面獨立分析段落；禁止提及具體技術指標數值
> - `final_verdict`：三維整合仲裁段落；允許跨維度推論
>   以上四欄位若 LLM 未回傳或回傳空字串，均 fallback 為 `null`，不崩潰。

> **`technical_indicators` 顯性輸出**（2026-05-25）：
>
> - 此欄位為 API 與前端技術指標卡片的正式資料來源。
> - 布林通道與 MACD 數值由 Python 根據 `snapshot.recent_closes` 計算；KD / ADX 由 `recent_closes` + `recent_highs` + `recent_lows` 計算；OBV 由 `recent_closes` + `recent_volumes` 計算。不由 LLM 推算。
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

> **LLM input contract：`signal_summary`（內部欄位，非 API response）**
>
> - `analyze_node` 在呼叫 LLM 前會建立 compact `signal_summary`，放在 prompt 最前段。
> - 內容包含 rule-based labels（`technical_signal` / `institutional_flow` / `sentiment_label` / `confidence_score` / `cross_validation_note`）、技術證據（布林、MACD、KD、ADX、OBV、RSI、支撐壓力）、消息面聚合與策略標籤。
> - LLM 只能解釋 `signal_summary`，不得改寫 labels 或重新計算分數；此欄位不會出現在 `/analyze` 或 `/analyze/position` response。

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
    "fetched_at": "2026-03-09T00:00:00+00:00",
    "support_20d": 1040.0,
    "resistance_20d": 1120.0
  },
  "position_analysis": {
    "entry_price": 980.0,
    "profit_loss_pct": 12.76,
    "position_status": "profitable_safe",
    "position_narrative": "目前獲利已脫離成本區，持股安全緩衝充足。",
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
  "data_sources": ["google-news-rss", "yfinance", "finmind"],
  "errors": []
}
```

- **欄位說明**

  | 欄位                       | 類型           | 說明                                                                                             |
  | -------------------------- | -------------- | ------------------------------------------------------------------------------------------------ |
  | `snapshot`                 | object         | yfinance 即時快照（與 `/analyze` 相同）                                                          |
  | `position_analysis`        | object         | **持股診斷專屬**——見下方欄位細節                                                                 |
  | `data_confidence`          | int \| null    | 0–100，資料完整度                                                                                |
  | `signal_confidence`        | int \| null    | 0–100，訊號強度                                                                                  |
  | `confidence_score`         | int \| null    | = `signal_confidence`，向後相容                                                                  |
  | `cross_validation_note`    | string \| null | 三維交叉驗證結論（rule-based 固定字串）                                                          |
  | `analysis_detail`          | object \| null | LLM 結構化分析輸出（持股版 context + `signal_summary`，強化持股健康度與出場推理）                |
  | `technical_indicators`     | object \| null | 技術指標顯性輸出（與 `/analyze` 相同，包含布林通道、MACD、KD、ADX、OBV，供前端技術指標卡片使用） |
  | `institutional_flow_label` | enum \| null   | `institutional_accumulation` / `retail_chasing` / `distribution` / `neutral`                     |
  | `action_plan`              | object \| null | 持股版戰術行動（`action` 為 `續抱` / `減碼` / `出場`）                                           |
  | `action_plan_tag`          | enum \| null   | `opportunity` / `overheated` / `neutral`                                                         |
  | `data_sources`             | array          | 本次成功取得資料的來源列表                                                                       |
  | `errors`                   | array          | 錯誤碼陣列                                                                                       |

- **`position_analysis` 欄位細節**

  | 欄位                            | 類型           | 說明                                                                          |
  | ------------------------------- | -------------- | ----------------------------------------------------------------------------- |
  | `entry_price`                   | float          | 購入成本價（回傳確認）                                                        |
  | `profit_loss_pct`               | float          | 當前損益百分比（rule-based Python 計算）                                      |
  | `position_status`               | string         | `profitable_safe` / `at_risk` / `under_water`                                 |
  | `position_narrative`            | string         | 倉位狀態敘事（rule-based，供 LLM 讀取）                                       |
  | `recommended_action`            | string         | `Hold` / `Trim` / `Exit`（rule-based，LLM 不得覆寫）                          |
  | `trailing_stop`                 | float          | 動態防守價位（rule-based Python 計算）                                        |
  | `trailing_stop_reason`          | string         | 停利/停損邏輯說明                                                             |
  | `exit_reason`                   | string \| null | 出場/減碼理由；無觸發條件時為 `null`                                          |
  | `distance_to_trailing_stop_pct` | float \| null  | 現價距離動態防守位百分比；正值代表仍在防守位上方                              |
  | `distance_to_support_pct`       | float \| null  | 現價距離 20 日支撐位百分比；正值代表仍在支撐上方                              |
  | `unrealized_pnl`                | float \| null  | 若 request 有 `quantity`，回傳未實現損益金額；未提供數量時為 `null`           |
  | `holding_days`                  | int \| null    | 若 request 有 `entry_date`，回傳持有天數；未提供或日期格式無法解析時為 `null` |

> **`recommended_action` 判斷規則（rule-based，後端計算）**：
>
> - `flow_label = distribution` 且 `profit_loss_pct > 0` → `Trim`
> - `flow_label = distribution` 且 `profit_loss_pct <= 0` → `Exit`
> - `technical_signal = bearish` 且 `close < trailing_stop` → `Exit`
> - `close < trailing_stop` 且 OBV / MACD / KD 動能轉弱 → `Exit`
> - `position_status = under_water` 且 `profit_loss_pct < -10%` → `Exit`
> - 獲利中且 `obv_signal` 為 `bearish_divergence` / `price_volume_weak` → `Trim`
> - 獲利達 10% 且 `kd_zone = overbought`、`bollinger_position = near_upper`，但 ADX/OBV/MACD 未形成強趨勢續航 → `Trim`
> - 其他 → `Hold`

> **持股診斷 LLM 邊界**：`/analyze/position` 與 `/analyze` 共用 LangGraph 分析流程與 `signal_summary`；差異是 request 內含 `entry_price` 時，`analyze_node` 會額外建立 `position_context`，讓 LLM 以成本價、損益百分比、動態防守價、距離防守線、距離支撐、未實現損益、持有天數、`recommended_action` 與 `exit_reason` 解釋持股狀態。`recommended_action` / `trailing_stop` / `exit_reason` 仍由 Python rule-based 計算，LLM 不得覆寫。

> **持股診斷快取邊界**：`/analyze/position` 的 L1 full_result 快取命中必須比對 `entry_price` / `entry_date` / `quantity`。同一檔股票若成本價、日期或數量不同，會強制重跑持股診斷，避免回傳其他成本基準的 `position_analysis`。

---

### `GET /portfolio`

- **用途**：列出目前登入使用者的 active 持股清單。
- **Response 200**

```json
[
  {
    "id": 42,
    "symbol": "2330.TW",
    "entry_price": 900.0,
    "quantity": 1000,
    "entry_date": "2026-01-15",
    "notes": "長期核心持股"
  }
]
```

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
  "notes": "長期核心持股"
}
```

- **Response 201**

```json
{
  "id": 42,
  "symbol": "2330.TW"
}
```

- **欄位說明**
  - `symbol`：股票代碼，必填；新增前會以 yfinance 輕量驗證代號是否存在
  - `entry_price`：購入成本價，必填
  - `entry_date`：購入日期，必填，ISO 8601 日期字串
  - `quantity`：持有數量，選填，未提供時預設 0
  - `notes`：備註，選填

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

### `DELETE /portfolio/{portfolio_id}`

- **用途**：刪除個人持股紀錄，並同步刪除該使用者該股票的 `daily_analysis_log`。
- **權限邊界**：只能刪除目前登入使用者自己的持股；非擁有者回傳 `403`。
- **Response 204**：無 response body。

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
