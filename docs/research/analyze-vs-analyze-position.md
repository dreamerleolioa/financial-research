# /analyze 與 /analyze/position 查詢邏輯差異

> 更新日期：2026-03-16

本文整理目前後端兩個分析端點的共同流程、分歧點，以及它們在 Graph、Prompt、快取與輸出上的差異。

## 1. 結論

- `/analyze` 回答的是：這檔股票現在整體怎麼看。
- `/analyze/position` 回答的是：我已經買了這檔股票，現在該續抱、減碼，還是出場。
- 兩者共用同一套 LangGraph 主流程，但只要 `entry_price` 存在，就會啟動持倉診斷分支，連帶改變 rule-based 計算、LLM prompt 與快取命中條件。

## 2. 共同流程

兩個 endpoint 都會走以下步驟：

1. 檢查股票代碼是否存在。
2. 回補昨日指標資料，載入昨日上下文。
3. 組裝 `GraphState`，丟進同一張 LangGraph 執行。
4. 由 graph 依序執行：技術面快照、外部資料抓取、新聞處理、前處理、信心分數、LLM 分析、策略輸出。
5. 將結果寫入：
   - `stock_analysis_cache`
   - `stock_raw_data`
   - `daily_analysis_log`

主要程式位置：

- API 入口：[backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py)
- Graph 組裝：[backend/src/ai_stock_sentinel/graph/builder.py](backend/src/ai_stock_sentinel/graph/builder.py)
- Graph state 定義：[backend/src/ai_stock_sentinel/graph/state.py](backend/src/ai_stock_sentinel/graph/state.py)

## 3. 主要差異總表

| 面向                     | `/analyze`                 | `/analyze/position`                                    |
| ------------------------ | -------------------------- | ------------------------------------------------------ |
| 核心目的                 | 一般個股分析               | 持倉診斷 / 出場防守                                    |
| Request                  | `symbol`、可選 `news_text` | `symbol`、`entry_price`、可選 `entry_date`、`quantity` |
| 初始 `news_content`      | 來自 `payload.news_text`   | 固定為空字串                                           |
| 初始 position 欄位       | 無                         | 有 `entry_price` / `entry_date` / `quantity`           |
| Position rule-based 計算 | 不啟動                     | 啟動                                                   |
| LLM Prompt               | 通用分析 prompt            | 通用 prompt + 持倉防守 prompt                          |
| 快取命中條件             | 一般 cache hit 即可        | 需有 `position_analysis` 才可直接命中                  |
| 回傳資料                 | 一般分析結果               | 一般分析結果 + `position_analysis`                     |

## 4. Request 與初始 State 差異

### `/analyze`

`AnalyzeRequest` 包含：

- `symbol`
- `news_text`（選填）

初始 state 重點：

- `symbol = payload.symbol`
- `news_content = payload.news_text`
- 不放 `entry_price`

程式位置：[backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py)

### `/analyze/position`

`PositionAnalyzeRequest` 包含：

- `symbol`
- `entry_price`
- `entry_date`（選填）
- `quantity`（選填）

初始 state 重點：

- `symbol = payload.symbol`
- `entry_price = payload.entry_price`
- `entry_date = payload.entry_date`
- `quantity = payload.quantity`
- `news_content = ""`

程式位置：[backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py)

## 5. 新聞處理差異

### `/analyze`

- 若前端有傳 `news_text`，graph 一開始就已有 `news_content`。
- 在 judge 階段，若新聞內容足夠，可能不需要補抓 RSS。

### `/analyze/position`

- 一開始 `news_content` 是空字串。
- judge 階段若 `cleaned_news` 仍為空，通常更容易進入 `requires_news_refresh = True`，由 graph 補抓 RSS。

判斷邏輯位置：[backend/src/ai_stock_sentinel/graph/nodes.py](backend/src/ai_stock_sentinel/graph/nodes.py)

## 6. Position 分支何時啟動

graph 內部不是靠 endpoint 名稱切換，而是靠 `entry_price` 是否存在。

- `entry_price is None`：走一般分析模式。
- `entry_price is not None`：額外啟動持倉診斷計算。

這個設計的直接效果是：

- `/analyze` 不會計算損益、防守位、出場理由。
- `/analyze/position` 會在 preprocess 與 strategy 階段多做一段持倉 rule-based 計算。

關鍵位置：

- preprocess 分支：[backend/src/ai_stock_sentinel/graph/nodes.py](backend/src/ai_stock_sentinel/graph/nodes.py)
- strategy 分支：[backend/src/ai_stock_sentinel/graph/nodes.py](backend/src/ai_stock_sentinel/graph/nodes.py)

## 7. Rule-Based 計算差異

### `/analyze`

主要輸出仍以：

- 技術面訊號
- 籌碼面訊號
- 基本面估值
- 新聞情緒
- 信心分數與策略建議

為主。

### `/analyze/position`

除了上述共同輸出，還會額外計算：

- `profit_loss_pct`
- `cost_buffer_to_support`
- `position_status`
- `position_narrative`
- `trailing_stop`
- `trailing_stop_reason`
- `recommended_action`
- `exit_reason`

規則實作位置：[backend/src/ai_stock_sentinel/analysis/position_scorer.py](backend/src/ai_stock_sentinel/analysis/position_scorer.py)

其中幾個核心規則如下：

- 獲利且法人出貨：偏向 `Trim`
- 虧損且法人出貨：偏向 `Exit`
- 技術面轉空且跌破防守位：偏向 `Exit`
- 深度套牢：偏向 `Exit`

## 8. LLM Prompt 差異

### `/analyze`

使用通用分析 prompt，重點是：

- 技術面
- 籌碼面
- 消息面
- 基本面
- 三維整合結論

### `/analyze/position`

當 `position_context` 存在時，分析器會把持倉專用 prompt 疊加到 system prompt 後面，明確要求模型：

- 從防守視角分析
- 聚焦續抱 / 減碼 / 出場
- 不得轉成找新買點的語氣
- 有出場理由時必須明確標記

同時 human prompt 也會額外注入：

- 購入成本價
- 當前損益
- 倉位狀態
- 動態防守位
- 系統建議動作

程式位置：[backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py](backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py)

## 9. 快取邏輯差異

### `/analyze`

- 只要一般快取命中條件成立，就可以直接回傳。

### `/analyze/position`

- 除了快取命中，還要確認 `cache.full_result.position_analysis` 已存在。
- 若只有一般分析結果、沒有 position 結果，會強制重跑 graph。

原因是同一支股票的一般分析快取，不能直接拿來回答「你的持倉該不該出場」。

快取判斷位置：[backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py)

## 10. Response 差異

兩者都回傳同一個 `AnalyzeResponse` schema，但 `/analyze/position` 會多出 `position_analysis` 內容。

### `/analyze` 常見重點欄位

- `snapshot`
- `analysis`
- `analysis_detail`
- `confidence_score`
- `strategy_type`
- `action_plan_tag`
- `fundamental_data`

### `/analyze/position` 額外欄位

- `position_analysis.entry_price`
- `position_analysis.profit_loss_pct`
- `position_analysis.position_status`
- `position_analysis.position_narrative`
- `position_analysis.recommended_action`
- `position_analysis.trailing_stop`
- `position_analysis.trailing_stop_reason`
- `position_analysis.exit_reason`

Response 組裝位置：[backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py)

## 11. 一句話版

- `/analyze`：分析股票本身。
- `/analyze/position`：分析你和這檔股票的關係。

前者關心「標的現在怎麼看」，後者關心「你現在持有這檔該怎麼辦」。

## 12. `is_final` 與 n8n 收盤後工作流

### 目前實作

- 盤中第一次查詢會寫入當日 `stock_analysis_cache`，且 `is_final = false`。
- 同一天盤中再次查詢時，L1 快取會直接命中，不重跑分析。
- 收盤後若使用者再次打 `/analyze` 或 `/analyze/position`，API 會發現當日快取仍為 `is_final = false`，因此強制重跑分析，並覆寫為 `is_final = true`。

目前這段判斷在 [backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py) 的 `_handle_cache_hit()`。

### 目前缺口

現有 `POST /internal/fetch-raw-data` 只會更新 `stock_raw_data`，不會更新 `stock_analysis_cache`，也不會把 `is_final` 改成 `true`。

也就是說：

- n8n 目前只能更新收盤後的原始資料。
- 若收盤後沒有任何使用者再次查詢該股票，`stock_analysis_cache` 可能仍停留在盤中版本（`is_final = false`）。

相關程式位置：[backend/src/ai_stock_sentinel/api.py](backend/src/ai_stock_sentinel/api.py)

### 正確目標行為

收盤後由 n8n 觸發的每日更新流程，應該把當日分析結果定稿化，但這件事不能只做「把 `is_final` 改成 `true`」這麼簡單。

正確做法應是：

1. n8n 先抓取收盤後的 technical / institutional / fundamental 原始資料。
2. 以收盤資料重新產出當日 analysis result。
3. 將完整定稿結果寫回 `stock_analysis_cache.full_result`。
4. 同步把 `stock_analysis_cache.is_final` 寫成 `true`。

原因是 `is_final` 代表「這筆分析是基於收盤後定稿資料產出的結論」，不是單純的時間標記。

如果 n8n 只更新 raw data，卻直接把 `stock_analysis_cache.is_final` 翻成 `true`，會導致：

- `full_result` 仍是盤中文案
- `recommended_action` 仍可能是盤中版本
- `analysis_detail` 仍未反映收盤後訊號

這樣資料語義會錯。

### 設計結論

- 你的判斷是對的：收盤後的 n8n 工作流最終應該讓當日結果成為 `is_final = true`。
- 但實作上不應只翻旗標，而是要由 n8n 觸發「收盤版分析重算 + cache 覆寫」。
- 若暫時不做 n8n 重算，則目前系統仍必須依賴收盤後第一次使用者查詢來完成定稿覆寫。
