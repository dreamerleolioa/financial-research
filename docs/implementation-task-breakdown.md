# AI Stock Sentinel 實作任務拆解（Execution Plan）

> 版本：v1.1  
> 更新日期：2026-03-04

## 0) 範圍說明

本文件依據 [ai-stock-sentinel-architecture-spec.md](ai-stock-sentinel-architecture-spec.md) 拆解成可執行工程任務，供開發排程與追蹤。

---

## Phase 1：核心資料流（MVP Backend）

### P1-1 股票快照抓取
- **目標**：可輸入股票代碼，取得基本股價快照
- **任務**
  - `yfinance` 抓取 current/open/high/low/volume/recent closes
  - 轉成統一資料模型
- **DoD**
  - CLI 可執行，回傳 `snapshot` JSON
  - 支援 `--symbol`
- **完成記錄**：`YFinanceCrawler` + `StockSnapshot` 已實作，CLI 可執行

### P1-2 新聞清潔工（Cleaner Agent）
- **目標**：輸入新聞內容，輸出乾淨 JSON
- **任務**
  - 定義 schema：`date/title/mentioned_numbers/sentiment_label`
  - 實作 LLM 結構化輸出
  - 無 API key 時 fallback heuristic
- **DoD**
  - 支援 `--text` / `--file` / stdin
  - 任何輸入都能穩定輸出 schema
- **完成記錄**：`FinancialNewsCleaner` 已實作，LLM + heuristic fallback，支援 `--text` / `--file` / stdin

### P1-3 Crawler + Cleaner 整合
- **目標**：同一流程同時輸出股票快照與新聞清潔結果
- **任務**
  - 將 `news_cleaner` 接入 `StockCrawlerAgent`
  - `main.py` 增加 `--news-text` / `--news-file`
- **DoD**
  - 單一指令可得到 `snapshot + analysis + cleaned_news`
- **完成記錄**：`StockCrawlerAgent` 整合 `news_cleaner`，`main.py` 支援 `--news-text` / `--news-file`

---

## Phase 2：LangGraph 協作與補抓回圈

### P2-1 導入 LangGraph 狀態機
- **目標**：從線性流程升級為可回饋流程
- **任務**
  - 定義 graph state（symbol/news/fundamental/data_sufficiency）
  - 建立節點：`crawl -> clean -> analyze -> judge -> (loop)`
- **DoD**
  - 可觸發「資料不足」分支並重新抓取
- **完成記錄**：GraphState + stub 節點 + loop guard 已實作，測試覆蓋，2026-03-03

### P2-2 資料完整性判斷節點
- **目標**：分析前自動判斷資料是否足夠
- **任務**
  - 規則：必要欄位缺失、新聞過舊、數字不足
  - 產出 flags：`requires_news_refresh`, `requires_fundamental_update`
- **DoD**
  - 有明確缺失理由與重跑次數上限
- **完成記錄**：judge_node 三條規則（snapshot 缺失、新聞過舊、數字不足）已實作，reason flags 加入 GraphState，測試覆蓋，2026-03-03

### P2-4 將 Graph 接進 API
- **目標**：讓 `/analyze` 真正走 LangGraph 回圈，而非舊的線性流程
- **任務**
  - `api.py` 改用 `build_graph` 取代 `StockCrawlerAgent`
  - 對齊 `AnalyzeResponse` 回傳欄位（graph 最終 state → response）
  - 更新 API 測試
- **DoD**
  - `POST /analyze` 觸發後，judge/fetch_news/retry 回圈實際執行
  - 測試覆蓋（mock graph 或 mock 各節點依賴）
- **完成記錄**：`api.py` 改用 `get_graph` dependency（`build_graph_deps()` in `main.py`），`AnalyzeResponse` 欄位不含 `raw_news_items`，graph errors 傳遞到 response，測試 8 個（mock compiled graph），2026-03-03

### P2-3 新聞資料源擴充（RSS）
- **目標**：不只靠手動輸入新聞
- **任務**
  - Google News RSS 抓取器
  - 標準化來源 metadata
- **DoD**
  - 指定 symbol 可拉回至少 N 篇新聞
- **完成記錄**：RssNewsClient（stdlib，無外部依賴）+ fetch_news_node + GraphState.raw_news_items 已實作，測試覆蓋（6 tests in test_rss_news_client.py + 5 fetch_news_node tests in test_graph_nodes.py），2026-03-03

---

## Phase 3：分析能力強化

> 本階段以三個分析模式為核心設計依據（完整規格見 [architecture-spec §3.2](ai-stock-sentinel-architecture-spec.md)）：
> - **模式一**：交叉驗證邏輯（Cross-Verification）
> - **模式二**：技術指標定性化（Quant to Qual）
> - **模式三**：籌碼歸屬分析（Institutional Flow Profiling）

### P3-0 籌碼資料源確認（前置、Blocking P3-3）
- **目標**：確認三大法人 + 融資融券資料源可用性，解除 P3-3 的 blocking 依賴
- **任務**
  - 建立 Provider 抽象層：`InstitutionalFlowProvider`
  - 實作 `FinMindProvider`（Primary）
  - 實作 `TwseOpenApiProvider`（Fallback #1，上市優先）
  - 實作 `TpexProvider`（Fallback #2，上櫃補齊）
  - 在 Provider Router 內加入 `.TW/.TWO` 市場自動分流邏輯（`.TW`→TWSE，`.TWO`→TPEX）
  - 固定資料源優先序：`FinMind -> TWSE OpenAPI -> TPEX`
  - Provider 層強制 Schema Mapping（統一輸出欄位），並加入限流/欄位漂移防禦
  - 在 `backend/utils/` 新增驗證腳本，先驗證 `2330.TW` 能抓到三大法人欄位
  - 補一個上櫃標的 smoke test（例：`6488.TWO`）驗證分流生效
  - 記錄資料源選擇依據與限制（更新頻率、限流、欄位完整度）
- **DoD**
  - 可抓到 2330 最近 5 日：`foreign_buy`、`investment_trust_buy`、`dealer_buy`、`margin_delta`（僅 smoke test；正式分析視窗至少 `days>=20`，建議 `days=60`）
  - 上市/上櫃路徑皆可運作（至少各 1 檔驗證）
  - Provider Router 可在 Primary 失敗時依固定優先序自動切換 Fallback
  - 不同資料源皆輸出一致 schema；欄位漂移不造成流程中斷
  - 確定採用的套件/API，記錄在進度追蹤文件

### P3-1 去情緒化分析流程
- **目標**：輸出「事實」與「情緒詞標記」（對應模式一的新聞面訊號來源）
- **任務**
  - 建立情緒詞字典（中英）
  - 抽取數字 + 情緒詞標記 + 事實敘述
  - Cleaner 輸出需包含結構化 `sentiment_label`（`positive / negative / neutral`），供後續交叉驗證使用
- **DoD**
  - 回傳結構包含 `facts`, `emotional_terms`, `fact_only_summary`, `sentiment_label`
  - `sentiment_label` 為明確分類值，不是自由文字

### P3-2 Quant to Qual 預處理節點（模式二）
- **目標**：在 LangGraph 流程中新增 `preprocess_node`，於 `analyze_node` 之前執行，將技術指標數值轉換為 LLM 可直接理解的敘事描述
- **任務**
  - 實作 `generate_technical_context(df_price, inst_data)` 作為 ContextGenerator（純 rule-based）
    - 產出 `technical_context`（技術面敘事）
    - 產出 `institutional_context`（法人敘事）
  - 實作 `quantify_to_narrative(technical: dict) -> str`（純 rule-based，不呼叫 LLM）
    - 乖離率（BIAS）：定性為「短線弱勢 / 乖離過大 / 正常」
    - RSI：定性為「超買 / 超賣 / 中性」
    - 均線排列：定性為「多頭 / 空頭 / 糾結」
    - 成交量變化：定性為「量能放大 / 萎縮 / 正常」
  - 在 LangGraph builder 中插入 `preprocess_node`（位置：`calculate_indicators_node` → `preprocess_node` → `analyze_node`）
  - Prompt template 改為接受敘事字串，移除直接傳入原始數字的欄位
- **DoD**
  - `quantify_to_narrative` 有獨立單元測試（覆蓋邊界值：BIAS <-5、>8；RSI <30、>70）
  - LangGraph 流程跑完後，`analyze_node` 的 prompt context 中不含裸數值，只含定性描述

### P3-2b Tool Use 計算工具（原 P3-2）
- **目標**：Agent 可呼叫計算工具驗證指標，確保數值來源可追溯
- **任務**
  - `calculate_technical_indicators(symbol, period)`：MA5/20/60、BIAS、RSI14、Volume Change、20 日高低點
  - `calculate_price_levels(symbol, window=20)`：`high_20d`、`low_20d`、`support_20d`、`resistance_20d`
  - `calculate_bias(close, ma)`：乖離率公式
  - `estimate_pe_percentile(symbol, pe)`：本益比歷史百分位
  - `calculate_growth_rate(current, previous)`：YoY / MoM
  - 接入 Analysis Agent 的 tool call
- **DoD**
  - 至少 4 個工具有對應單元測試（含 `calculate_price_levels`）
  - `entry_zone` 與 `stop_loss` 來源可追溯至工具輸出（不可由 LLM 自行生成數值）

### P3-3 籌碼歸屬分析 + 信心分數（模式一 + 模式三）
- **前置依賴**：P3-0 完成（資料源確認）
- **目標**：實作三維訊號交叉驗證，輸出有依據的信心分數
- **任務**
  - 實作 `fetch_institutional_flow(symbol, days)` 工具
    - 輸出：`foreign_net_cumulative`, `trust_net_cumulative`, `dealer_net_cumulative`, `three_party_net`, `consecutive_buy_days`, `margin_balance_delta_pct`, `flow_label`
    - `flow_label` 由 rule-based Python 決定，不由 LLM 判斷
  - 實作 `adjust_confidence_by_divergence(base_score, news_sentiment, inst_flow, technical_signal)`
    - `base_score` 從 **50**（中性基準）開始，clamp 至 [0, 100]
    - 三維共振 → +15；利多出貨背離 → -20；散戶追高 → -15；利空不跌 → +10
    - 回傳 `(adjusted_score, cross_validation_note)`
    - **純 Python，不呼叫 LLM**；`cross_validation_note` 為 rule-based 固定字串
  - Graph flow：`preprocess → score → analyze → strategy → END`
    - `score_node` 在 `analyze_node` **之前**執行，讓 LLM prompt 可讀取 `confidence_score` 與 `cross_validation_note`
    - LLM 在 `analyze_node` 中讀取這兩個欄位，用於生成 `risks` / `summary`，**不得修改分數**
  - 更新 system prompt 為 Skeptic Mode：強制「提取 → 對照 → 衝突檢查 → 僅輸出事實與邏輯推論」
  - `GraphState` 新增欄位：`confidence_score`, `cross_validation_note`
- **DoD**
  - `adjust_confidence_by_divergence` 對四種情境均有測試（共振 / 利多出貨 / 散戶追高 / 利空不跌）
  - `fetch_institutional_flow` 可抓到真實資料並輸出 `flow_label`
  - 最終輸出包含 `confidence_score`（0~100）與 `cross_validation_note`（非空字串）
  - 最終輸出包含 `strategy_type`、`entry_zone`、`stop_loss`、`holding_period`（Task C3 已完成）

---

## Phase 4：前端儀表板（React + Tailwind）

### P4-1 前端基礎專案
- **目標**：建立可執行前端骨架
- **任務**
  - 建立 React + TypeScript + Tailwind
  - 股票輸入框 + 查詢按鈕
- **DoD**
  - 可輸入 symbol 並呼叫後端 API
- **完成記錄**：React + TypeScript + Tailwind 骨架已建立，API 串接完成（2026-03-04 Session 7）

### P4-2 核心展示元件
- **目標**：呈現 AI 分析價值
- **任務**
  - 信心指數圓形元件
  - 雜訊過濾左右對照
  - 分析路徑（step timeline）
- **DoD**
  - 三個元件可顯示真實後端資料
- **完成記錄**：信心指數圓弧改用 `confidence_score`（真實資料，動畫過渡）；快照資訊、cleaned_news 摘要改為真實資料驅動（2026-03-04 Session 7）

### P4-3 體驗優化
- **目標**：提升可讀性與可追溯性
- **任務**
  - loading 狀態（按鈕 disabled + 文字）
  - error banner（errors[0] 顯示於頁面頂部）
- **DoD**
  - loading / error 狀態正確顯示，不阻斷主頁
- **完成記錄**：loading 狀態、紅色 error banner 均已實作（2026-03-04 Session 7）

### P4-4 操作建議卡片（Action Plan）
- **目標**：讓使用者輸入代碼後可直接看到可執行策略
- **任務**
  - 在頁面底部新增全寬「戰術行動（Action Plan）」卡片
  - 展示欄位：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
  - `cross_validation_note` 顯示於信心指數卡片下方
- **DoD**
  - 卡片可正確顯示「策略方向 / 建議入場區間 / 防守底線 / 預期持股期間」
  - null 值顯示 —，不崩潰
- **完成記錄**：Action Plan 全寬 2×2 卡片、`cross_validation_note` 灰色小字均已實作（2026-03-04 Session 7）

---

## Cross-cutting（跨階段）

### C-1 API 服務層（FastAPI）
- 建立 `/analyze`、`/health`
- 後續可加 `/events`（SSE）

### C-2 可觀測性與日誌
- 統一 request_id
- 節點耗時、失敗原因

### C-3 測試策略
- 單元測試：cleaner、計算工具
- 整合測試：crawler -> cleaner -> analyzer

### C-4 完成即補測試（新增）
- **目標**：每一項功能完成後，立即補上對應測試，避免功能與測試脫鉤
- **任務**
  - 功能 PR 必須包含至少一個對應測試（單元或整合）
  - 若暫時無法補測試，需在 PR 註明原因與補測試 ETA
  - Checkpoint 需檢查「本週新增功能是否都有測試」
- **DoD**
  - 每個已完成任務都能對應到至少一個測試案例
  - CI/本地測試可驗證新增功能行為

### C-5 策略模板（Task C3，新增）
- **目標**：強制 AI 輸出具備防守思維的策略欄位，而非空泛建議
- **任務**
  - 建立策略模板對應表（`short_term` / `mid_term` / `defensive_wait`）
  - 以 Python 規則綁定 `entry_zone`、`stop_loss`、`holding_period`
  - Prompt 明確要求輸出「防守底線與觸發條件」（例如破 MA60）
- **DoD**
  - `analysis_detail` 固定帶出策略欄位且不為空
  - 單元測試覆蓋至少 2 種訊號組合對應的策略模板

---

## 建議執行順序（短版）

1. 完成 P2-1 + P2-2（先有 LangGraph 回圈）
2. 完成 P2-3（新聞來源自動化）
3. **[High] 先做 P3-0**（Provider 抽象 + 2330.TW 實測，解除 P3-3 blocking）
4. **[Medium] 完成 P3-2**（`preprocess_node` + `generate_technical_context` + `quantify_to_narrative`）
5. **[Medium] 完成 P3-3**（Skeptic Prompt + 衝突規則算分 + `fetch_institutional_flow`）
6. 完成 P3-1（Cleaner 輸出 `sentiment_label`）
7. 完成 P3-2b（Tool Use 計算工具）
8. **[Low] 完成 P4-1~P4-3**（前端展示）
