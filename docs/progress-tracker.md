# AI Stock Sentinel 進度追蹤

> 更新日期：2026-03-09（Session 8 完成，302 tests passed）

## 目前完成度（高層）

- **Phase 1（MVP Backend）**：100%（技術債清理完成：CLI 改走 graph 流程，StockCrawlerAgent / build_agent() 已移除）
- **Phase 2（LangGraph 回圈）**：100%（骨架 + judge 邏輯 + RSS 新聞抓取 + 新聞清潔接進 graph + graph 接進 API 完成）
- **Phase 3（分析能力強化）**：100%（Provider 抽象層 + preprocess_node + ContextGenerator + 策略建議模板 + Skeptic Mode + 信心分數 + 成本安全鎖 + Anthropic LLM 串接 + API 新欄位 + AnalysisDetail 結構化輸出 + code fence hotfix + Protocol 回傳型別對齊完成）
- **Phase 4（前端儀表板）**：100%（核心功能完成；Action Plan 燈號 badge；data_confidence 提示；分維度分析小卡完成）

---

## 已完成 ✅

### 專案與環境
- [x] Git 儲存庫初始化
- [x] `backend/venv` 建立與依賴安裝
- [x] `Makefile`（`make install`, `make run`）
- [x] `.gitignore` 建立

### Crawler / Cleaner 核心
- [x] `yfinance` 抓取股票快照（預設 `2330.TW`）
- [x] Stock 分析介面（有 LLM 時分析，無金鑰 fallback）
- [x] 財經新聞清潔器 schema（`date/title/mentioned_numbers/sentiment_label`）
- [x] 新聞清潔器支援 `--text` / `--file` / stdin
- [x] 新聞清潔已整合到 graph `clean_node`（輸出 `cleaned_news`；原 `StockCrawlerAgent` 已移除）

### 文件
- [x] README（安裝、執行、參數、輸出）
- [x] 技術架構需求文件
- [x] 任務拆解文件（本次新增）
- [x] 開發手冊新增「完成即補測試」規範
- [x] 後端 API 技術規格文件（`docs/backend-api-technical-spec.md`）

### API / 測試（本次新增）
- [x] 新增 FastAPI `/health`、`/analyze`
- [x] 新增 API 合約測試（健康檢查、成功路徑、驗證錯誤）

### 前端儀表板（基礎）
- [x] React + TypeScript + Tailwind 專案初始化
- [x] 股票代碼輸入框（MVP）
- [x] 信心指數元件（靜態）
- [x] 雜訊過濾左右對照（靜態）
- [x] 分析路徑圖（靜態）

---

## 進行中 / 待完成 ⏳

> 文件規則提醒：每次完成計劃中的任務，需當日回寫對應 `docs/plans/*.md`；若該需求尚無計劃文件，需先補產計劃文件再標記完成。

### 本次需求變更（2026-03-03）

- [x] 已同步更新需求文件與規劃文件：
	- `docs/ai-stock-sentinel-architecture-spec.md`
	- `docs/implementation-task-breakdown.md`
	- `docs/plans/2026-03-04-multi-dimension-analysis.md`
- [x] 資料源優先序定案：`FinMindProvider`（Primary）→ `TwseOpenApiProvider`（Fallback #1）→ `TpexProvider`（Fallback #2）
- [x] 上市/上櫃碎片化對策定案：`InstitutionalFlowProvider` 內建 `.TW/.TWO` 自動分流（`.TW`→TWSE，`.TWO`→TPEX）
- [x] 防禦性編程定案：Provider 層強制 Schema Mapping；需處理限流（rate limit）與欄位漂移（field drift），確保輸出 JSON schema 一致

### 本次需求變更（2026-03-04）

- [x] 規格補強定案：技術位階必須納入 `high_20d` / `low_20d` / `support_20d` / `resistance_20d`
- [x] 策略欄位定案：`analysis_detail` 新增 `strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
- [x] 前端展示定案：`analysis_detail` 區塊新增「戰術行動（Action Plan）」卡片
- [x] 明日計劃調整定案：Task C 新增子任務 `Task C3`（策略建議模板）
- [x] 需求補強定案：新增「新聞摘要品質門檻（Quality Gate）」避免時間戳/日期碎片誤導
- [x] 規劃文件定案：新增新聞摘要品質修正計劃（`docs/plans/2026-03-05-news-summary-quality.md`）

### 本次需求變更（2026-03-05）

- [x] 決策定案：`cleaned_news` 拆分為兩份資料
  - `cleaned_news`（保留）：供 LLM pipeline 消費（`sentiment_label`、`mentioned_numbers` 等）
  - `news_display`（新增）：供前端顯示，含乾淨 RSS 標題、ISO 日期、來源 URL
- [x] 前端顯示決策定案：新聞卡片移除 `mentioned_numbers` chips（對使用者無顯示價值）
- [x] 規劃文件定案：新增 `docs/plans/2026-03-05-news-display-split.md`
- [x] 治本修正完成：`fetch_news_node` 改用結構化格式組 `news_content`（標題:/摘要: 標籤），防止 LLM 把時間戳誤識為標題
- [x] 治標修正完成：`quality_gate_node` 偵測 `TITLE_LOW_QUALITY` 時，從 `raw_news_items` 回填 RSS 原始標題到 `cleaned_news`
- [x] 新聞摘要品質優化（NQ-1 ~ NQ-6）：全部完成（198 tests passed）

### Phase 2：LangGraph
- [x] 建立 LangGraph 狀態機（GraphState + 節點 stub + builder）
- [x] loop guard（max_retries）骨架
- [x] 完整性判斷節點（snapshot 缺失、新聞過舊、數字不足）
- [x] 新聞 RSS 自動抓取（RssNewsClient + fetch_news_node，無外部依賴）
- [x] 將 graph 接進 `/analyze` API（`build_graph()` 取代 `StockCrawlerAgent`，judge/fetch_news/retry 回圈在 API 請求中真正執行，測試覆蓋，2026-03-03）
- [x] 新聞清潔接進 graph（`clean_node` 插入 judge → analyze 之間，`cleaned_news` 路徑打通，測試覆蓋，2026-03-03）

### Phase 1：技術債
- [x] 移除 `StockCrawlerAgent` / `build_agent()`，CLI 改走 graph 流程（`agents/crawler_agent.py` 已移除，`main.py` 改用 `build_graph()`，測試覆蓋，2026-03-03）

### Phase 3：分析深化（多維偵察升級）

> 計劃文件：`docs/plans/2026-03-04-multi-dimension-analysis.md`

- ~~Task 1~~：`AnalysisDetail` 以 dataclass 形式交付（非 Pydantic）；`TechnicalData`/`InstitutionalData` 未獨立建立，改以 dict + Schema Mapping 方式實作於 Provider 層
- ~~Task 2~~：技術指標以 `calc_bias` / `calc_rsi` / `ma` 獨立函式形式交付（`context_generator.py`），非 `YFinanceCrawler` 方法
- [x] Task 3（P3-0 / Task A）：籌碼資料源 Provider 抽象層 + Router + Schema Mapping（2026-03-04）
  - `InstitutionalFlowProvider` 介面、`FinMindProvider`（Primary）、`TwseOpenApiProvider`（Fallback #1）、`TpexProvider`（Fallback #2）
  - Router 固定優先序（`FinMind → TWSE OpenAPI → TPEX`）+ `.TW/.TWO` 自動分流
  - Provider 層強制 Schema Mapping（統一 `InstitutionalFlowData` 輸出），欄位漂移告警
  - `fetch_institutional_flow` 工具函式（`tools.py`）；全部 Provider 失敗回傳 `error` dict，不拋例外
  - 測試：42 個（Router / Provider 介面 / Tool / Schema / fallback / twse_only 跳過邏輯）
- [x] Task 4 部分（P3-2 / Task B）：`GraphState` 新增 `technical_context`、`institutional_context`、`institutional_flow` 欄位（2026-03-04）
- [x] Task 5 部分（P3-2 / Task B）：新增 `preprocess_node`（`graph/nodes.py`）（2026-03-04）
  - 從 `snapshot.recent_closes` 建立 df_price，呼叫 `generate_technical_context`
  - 異常時記錄 `PREPROCESS_ERROR` 且流程不中斷
- [x] Task 6 部分（P3-2 / Task B）：`graph/builder.py` 串接 `clean → preprocess → analyze`（2026-03-04）
- [x] P3-2 ContextGenerator（`analysis/context_generator.py`）（2026-03-04）
  - `generate_technical_context(df_price, inst_data)` 純 rule-based
  - 產出 `technical_context`（BIAS/RSI/均線/量能敘事）與 `institutional_context`（法人籌碼敘事）
  - 測試：29 個（BIAS/RSI 邊界值、敘事觸發條件、preprocess_node 整合）
- [x] Task C1（P3-C1/C2）：Skeptic Mode Prompt 升級（`langchain_analyzer.py`）（2026-03-04）
  - 四步驟強制流程（提取→對照→衝突檢查→輸出事實與推論）
  - `analyze()` 新增 `technical_context` / `institutional_context` / `confidence_score` / `cross_validation_note` 參數
  - `analyze_node` 從 state 取上述欄位傳入 analyzer
  - `StockAnalyzer` Protocol 同步升級
- [x] Task C2（P3-C1/C2）：信心分數計算（`analysis/confidence_scorer.py`）（2026-03-04）
  - `adjust_confidence_by_divergence(base_score, news_sentiment, inst_flow, technical_signal)` 純 rule-based
  - 四規則優先序（三維共振+15 / 利多出貨-20 / 散戶追高-15 / 利空不跌+10），clamp [0,100]
  - `GraphState` 新增 `confidence_score` / `cross_validation_note` 欄位
  - `score_node` 新增（串接在 preprocess 後、analyze 前）
  - `builder.py` 更新：`preprocess → score → analyze → strategy → END`
  - 測試：11 個（4 主要情境 + 優先序 + clamp + custom base）；全套 128 passed
- [x] Task 7：LLM 結構化輸出（AnalysisDetail）（2026-03-04 Session 9）
  - `AnalysisDetail` dataclass 新增至 `models.py`（`summary` / `risks` / `technical_signal`）
  - `langchain_analyzer.py` System Prompt 改為要求輸出 JSON；`analyze()` 回傳 `AnalysisDetail`
  - `_parse_analysis()` 靜態方法：`json.loads()` 解析；失敗 fallback 為 `AnalysisDetail(summary=raw, risks=[], technical_signal="sideways")`
  - `GraphState` 新增 `analysis_detail` 欄位；`analyze_node` 寫入 `analysis_detail`（保留 `analysis=summary` 向後相容）
  - `AnalyzeResponse` 新增 `analysis_detail: dict | None`；`api.py` 從 result 取值（dataclass → asdict 轉換）
  - 前端 `App.tsx`：`AnalysisDetail` interface；LLM 分析報告卡片優先顯示 `analysis_detail`（summary + risks + technical_signal 標籤）；fallback 維持純文字
  - 測試：新增 4 個（`_parse_analysis` 合法 JSON / 非 JSON fallback、analyze 端對端 mock、API analysis_detail 欄位）；全套 136 passed
- ~~Task 7.1~~：策略欄位已由 rule-based `strategy_node` 獨立完成，整合無必要
- ~~Task 7.2~~：同上
- [x] Task 7.5：LLM Provider 串接（主用 Claude）（2026-03-04 Session 6）
  - `config.py` 新增 `anthropic_api_key` / `anthropic_model`（預設 `claude-sonnet-4`）
  - `build_graph_deps()` 優先使用 `ChatAnthropic`，無 key 或無套件則 fallback 至 OpenAI
  - `requirements.txt` 新增 `langchain-anthropic>=0.3.0`
- [x] Task 7.6：LLM 呼叫前成本安全鎖（`langchain_analyzer.py`）（2026-03-04 Session 6）
  - `_estimate_cost()` 方法：字串長度 / 4 = token 估算，費率 $3/M，超過 $1 USD → `ValueError`
  - 正常請求（~640 tokens）不觸發；測試 3 個全通過
- [x] Task 8：`api.py` `AnalyzeResponse` 新增 strategy/confidence 欄位（2026-03-04 Session 6）
  - 新增：`confidence_score`、`cross_validation_note`、`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
  - `initial_state` 補齊 Phase 3 全部欄位（api.py + main.py CLI 路徑）
  - 測試：`test_analyze_response_includes_strategy_fields` 通過
- [x] Task 9：整合測試（2026-03-04 Session 6）
  - `test_graph_builder.py` `_initial_state()` 補齊 Phase 3 欄位
  - `test_main.py` `required_keys` 補齊 Phase 3 欄位驗證
  - `make test` → 132 passed
- [x] Task C3（P3-C3）：策略建議模板（`analysis/strategy_generator.py`）（2026-03-04）
  - `generate_strategy(technical_context_data, inst_data)` 純 rule-based
  - 映射規則：`short_term`（利多+RSI<30）/ `mid_term`（法人吸籌+均線多頭）/ `defensive_wait`（訊號衝突/高乖離）
  - `GraphState` 新增 `strategy_type` / `entry_zone` / `stop_loss` / `holding_period` 欄位
  - `graph/nodes.py` 新增 `strategy_node`；`builder.py` 串接 `analyze → strategy → END`
  - 技術指標輔助函式升為 public API（`calc_bias` / `calc_rsi` / `ma`）
  - 測試：23 個（全訊號組合 + 邊界值 + 優先序 + None 安全）；全套 117 passed

### Phase 4：前端
- [x] 串接後端 API（symbol -> 真實分析結果）（2026-03-04 Session 7）
  - `handleAnalyze()` 呼叫 `POST http://localhost:8000/analyze`，結果存入 React state
  - 網路錯誤自動填入 `NETWORK_ERROR` ErrorDetail
- [x] 元件改為真實資料驅動（非靜態假資料）（2026-03-04 Session 7）
  - 信心指數圓弧改用 `confidence_score`（null 時顯示 `—`，有動畫過渡）
  - 「原始新聞」欄改為「快照資訊」顯示 `symbol` / `current_price` / `volume`
  - AI 萃取摘要改用 `cleaned_news`，null 時顯示提示文字
- [x] 補上錯誤狀態與載入狀態（2026-03-04 Session 7）
  - 按鈕 `disabled` + 顯示「分析中...」during loading
  - `errors[0]` 存在時頁面頂部顯示紅色 banner（含 `code` + `message`）
- [x] `analysis_detail` 新增「戰術行動（Action Plan）」卡片（2026-03-04 Session 7）
  - 全寬卡片，2×2 grid：策略方向 / 建議入場區間 / 防守底線 / 預期持股期間
  - `strategy_type` 對應中文標籤（short_term → 短線操作 等）
  - `cross_validation_note` 顯示於信心指數卡片下方（灰色小字，null 則不顯示）
- [x] 前端顯示 `analysis` 文字（LLM 四步驟分析結果）（2026-03-04 Session 8）
  - 新增「LLM 分析報告」卡片於 Action Plan 上方
  - `analysis` 有值時 `<pre>` 顯示；空或 null 時顯示「本次無 LLM 分析結果。」
- [x] 前端顯示 `analysis_detail` 結構化輸出（2026-03-04 Session 9）
  - `analysis_detail` 存在時：顯示 `technical_signal` 標籤（看多/看空/盤整）、`summary` 段落、`risks` 條列
  - `analysis_detail` 為 null 時 fallback 維持 `analysis` 純文字
- [x] 信心指數卡片：`data_confidence < 60` 時卡片下方顯示「資料不足，分數僅供參考」灰色提示（計劃文件：`docs/plans/2026-03-07-spec-gap-fix-day2.md` Session 4）（2026-03-07）

### 下一輪修正（新聞摘要品質）

> 計劃文件：`docs/plans/2026-03-05-news-summary-quality.md`

- [x] NQ-1：在 `clean_node` / Cleaner 增加 `title` 品質檢查（禁止純時間戳、純 URL；命中標記 `TITLE_LOW_QUALITY`）
- [x] NQ-2：日期正規化（優先 ISO 8601；失敗保留 `unknown` + 標記 `DATE_UNKNOWN`）
- [x] NQ-3：`mentioned_numbers` 新增財經語意過濾規則，降低日期碎片噪音（命中標記 `NO_FINANCIAL_NUMBERS`）
- [x] NQ-4：rule-based `quality_score`（0–100，clamp）+ API 回傳 `cleaned_news_quality`（`quality_score` / `quality_flags`）
- [x] NQ-5：前端低品質摘要提示（`quality_score < 60` 或 flags 非空 → 顯示「摘要品質受限」）
- [x] NQ-6：補齊單元測試（品質規則覆蓋）與整合測試（API 欄位格式驗證）

### 下一輪修正（新聞顯示資料拆分）

> 計劃文件：`docs/plans/2026-03-05-news-display-split.md`

- [x] ND-1：`GraphState` 新增 `news_display` 欄位（`title: str`、`date: str | null`、`source_url: str | null`）
- [x] ND-2：`quality_gate_node` 從 `raw_news_items[0]` 組出 `news_display`（含 RFC 2822 → ISO 日期正規化）
- [x] ND-3：`api.py` `AnalyzeResponse` 新增 `news_display` 欄位並回傳
- [x] ND-4：前端改讀 `news_display` 渲染新聞卡片（標題、日期、查看原文連結），移除 `mentioned_numbers` chips
- [x] ND-5：補齊測試（state 欄位、node 輸出、API 欄位）（205 tests passed）

### 下一輪修正（信心分數可靠性優化）

> 計劃文件：`docs/plans/2026-03-05-deep-analysis-upgrade.md`（Session 5）  
> **問題根源（已確認）**：多數查詢固定回傳 50，因為三個輸入訊號同時退化為預設值：
> 1. `technical_signal` 判斷 `bullish` 要求 `close > ma5 > ma20 AND 50 ≤ RSI ≤ 70` 同時成立，條件過嚴，多數情況退化為 `sideways`
> 2. `institutional_flow.flow_label` 沒有 API key / 限流時固定回傳 `neutral`
> 3. 四條規則為「精確命中」才調分，三個訊號均為 `neutral/sideways` 時 adjustment = 0

- [x] CS-1：`derive_technical_score()` 新函式，RSI/BIAS/均線排列各自獨立加權（[30,70] 映射）；5 新測試（2026-03-05）
- [x] CS-2：`adjust_confidence_by_divergence()` 改為多維加權模型，lookup table 取代 if-elif；partial match 可得部分調分；21→28 tests（2026-03-05）
- [x] CS-3：機構資料含 `error` 鍵時 `score_node` 改標記為 `"unknown"`，`_INST_FLOW_SCORES` 顯式加入 `"unknown": 0`；score_node 整合測試補齊（2026-03-05）
- [x] CS-4：`compute_confidence()` 整合回傳 `data_confidence`（資料完整度）與 `signal_confidence`（訊號強度）；`GraphState` / `AnalyzeResponse` 補齊兩欄位；`confidence_score` 保留為 `signal_confidence` 別名向後相容（2026-03-05）
- [x] CS-5：`_derive_technical_signal()` 改呼叫 `derive_technical_score()` 加權函式，廢除舊式多 AND 條件判斷；`score_node` 全面整合（2026-03-05）

### 下一輪修正（Action Plan 燈號）

> 計劃文件：`docs/plans/2026-03-05-deep-analysis-upgrade.md`（Session 4）；實作計劃：`docs/plans/2026-03-06-spec-gap-fix-day1.md` Session 2
> 架構決策：燈號由**後端 rule-based Python 計算**並回傳 `action_plan_tag`；前端不含條件判斷，僅做 enum → emoji/文字顯示。
> 完成日期：2026-03-06（263 tests passed）

- [x] **T2-0（前提）**：`GraphState` 新增 `rsi14: float | None` 獨立欄位；`preprocess_node` 計算後同步寫入 state（供燈號判斷用，不從 narrative 字串反解）
- [x] 後端：實作 `calculate_action_plan_tag(rsi14, flow_label, confidence_score)` 純 Python（`opportunity` / `overheated` / `neutral`；任一輸入為 None 則降級回 `neutral`）
- [x] 後端：`GraphState` 新增 `action_plan_tag` 欄位；`AnalyzeResponse` 新增 `action_plan_tag: str | null` 與 `institutional_flow_label: str | null`
- [x] 後端：補齊單元測試（三情境 + None 安全 + `rsi14` float 寫入驗證 + API 欄位驗證）
- [x] 前端：Action Plan 卡片標題旁顯示燈號標籤（`opportunity` → 🟢 機會 / `overheated` → 🔴 過熱 / `neutral` → 🔵 中性）
- [x] 前端：`action_plan_tag` 為 null 時不顯示標籤，不崩潰

### 下一輪修正（LLM Prompt 缺少消息面輸入）

> **發現時間**：2026-03-05 規格比對
> **完成日期**：2026-03-07（295 tests passed）計劃文件：`docs/plans/2026-03-07-spec-gap-fix-day2.md` Session 5

- [x] `langchain_analyzer.py` `_HUMAN_PROMPT` 加入 `{news_summary}` 欄位（取 `cleaned_news.title` + `mentioned_numbers` + `sentiment_label`）
- [x] `analyze()` 簽名新增 `news_summary: str | None = None` 參數；`_estimate_cost()` 納入長度估算
- [x] `analyze_node` 從 `state["cleaned_news"]` 組合後傳入；None 時顯示「（本次無新聞摘要）」
- [x] `StockAnalyzer` Protocol 同步更新；補齊測試（含有/無 cleaned_news 兩情境）

### 下一輪修正（data_confidence 語義修正）

> **發現時間**：2026-03-05 規格比對
> **完成日期**：2026-03-07（295 tests passed）計劃文件：`docs/plans/2026-03-07-spec-gap-fix-day2.md` Session 6

- [x] `compute_confidence()` 修正「資料完整度」判斷邏輯：neutral/sideways 均視為有取得，只有 unknown 才計為未取得
- [x] 補齊測試（neutral 情緒 → data_confidence=100；sideways 技術 → data_confidence 不降低）

### 待優化缺口（2026-03-05 規格對比發現）

> **背景**：2026-03-05 對後端程式碼與 `ai-stock-sentinel-architecture-spec.md` 進行全面比對，發現下列四大缺口尚未實作。

#### 1. 技術位階指標（Support / Resistance）

> 規格要求 `calculate_price_levels()` 輸出 `high_20d`, `low_20d`, `support_20d`, `resistance_20d`，目前均未實作。
> 計劃文件：`docs/plans/2026-03-06-spec-gap-fix-day1.md` Session 1
> 完成日期：2026-03-06（263 tests passed）
> 實作備注：`StockSnapshot` 的四個位階欄位與 `__post_init__` 計算邏輯實作於 `models.py`（非計劃原定的 `yfinance_client.py`），架構上更合理。

- [x] `models.py` `StockSnapshot` 補齊 `high_20d` / `low_20d` / `support_20d` / `resistance_20d` 欄位計算（`__post_init__` 自動計算，近 20 日高低點 × 0.99/1.01）
- [x] `context_generator.py` `generate_technical_context()` 加入支撐/壓力位敘事段落（新增 `_price_level_narrative()`）
- [x] `strategy_generator.py` `entry_zone` / `stop_loss` 改以實際價格計算（如 `"892.0-905.0（support_20d ~ MA20）"` / `"865.4（近20日低點×0.97 或 MA60）"`）
- [x] `GraphState` 新增 `support_20d` / `resistance_20d` / `high_20d` / `low_20d` 選填欄位
- [x] **位階資料缺失 fallback**：`low_20d` / `ma60` 不可用時，`entry_zone` 回傳 `"資料不足，建議參考現價 +/- 5%"`；不允許虛構數值
- [x] 補齊測試（含 `test_strategy_template_contains_numeric_entry_zone`、`test_strategy_generation_fails_safe_when_price_levels_missing`、`test_strategy_fallback_uses_close_plus_minus_5pct_when_low20d_unavailable`）

#### 2. AnalyzeResponse 欄位完整性

> 規格輸出結構含多個頂層欄位，目前 API 回應缺漏。
> 計劃文件：`docs/plans/2026-03-07-spec-gap-fix-day2.md` Session 3
> **完成日期**：2026-03-07（295 tests passed）

- [x] `AnalyzeResponse` 新增頂層 `sentiment_label: str | null`（從 `cleaned_news.sentiment_label` 浮出）
- [x] `strategy_generator.py` 新增 `generate_action_plan()` rule-based 函式；`GraphState` 新增 `action_plan: dict | None`；`strategy_node` 計算並寫入
- [x] `AnalyzeResponse` 新增 `action_plan: dict | null`（含 `action` / `target_zone` / `defense_line` / `momentum_expectation`）
- [x] `AnalyzeResponse` 新增 `data_sources: list[str]`（依實際抓取成功的來源動態填入）

#### 3. AnalysisDetail 結構強化

> 目前 `AnalysisDetail` 只含 `summary` / `risks` / `technical_signal`，規格輸出尚有其他欄位。
> 計劃文件：`docs/plans/2026-03-07-spec-gap-fix-day2.md` Session 3
> **完成日期**：2026-03-07（295 tests passed）

- [x] `AnalysisDetail` 新增 `institutional_flow: str | None`（預設 None，向後相容）
- [x] `AnalysisDetail` 新增 `sentiment_label: str | None`（預設 None，向後相容）
- [x] LLM System Prompt 同步更新；`_parse_analysis()` None-safe

#### 4. DATE_UNKNOWN 信心分數懲罰

> 計劃文件：`docs/plans/2026-03-07-spec-gap-fix-day2.md` Session 7
> **完成日期**：2026-03-07（295 tests passed）

- [x] `compute_confidence()` 新增 `date_unknown` 參數，在 clamp 前扣 -3 分（僅影響 signal_confidence）
- [x] `score_node` 從 `cleaned_news_quality.quality_flags` 讀取 `DATE_UNKNOWN` 並傳入
- [x] `cross_validation_note` 末尾追加「（注意：新聞日期不明，時效性未驗證）」（追加不覆蓋）
- [x] 補齊測試（含/不含 `DATE_UNKNOWN` 兩情境；None-safe）

#### 5. 分析敘事結構化 — 分維度拆解（Session 8，中優先）

> **需求來源**：2026-03-07 PM 需求補強
> **背景**：目前 LLM 將三維資訊揉雜在單一 `summary` 段落，資訊密度過高、推論過程像黑盒子。分維度拆解可提升可讀性與可解釋性。
> **計劃文件**：`docs/plans/2026-03-07-dimensional-analysis.md`
> **架構規格**：`docs/ai-stock-sentinel-architecture-spec.md` v2.5 §3.2「分維度強制分段」、§4.1 元件 7

- [x] **任務 A**：`models.py` `AnalysisDetail` 新增四欄位：`tech_insight: str | None`、`inst_insight: str | None`、`news_insight: str | None`、`final_verdict: str | None`（向後相容，預設 None）（2026-03-09）
- [x] **任務 B**：`langchain_analyzer.py` System Prompt + JSON schema 更新——強制 LLM 分段輸出，禁止 `tech_insight` 混入籌碼/消息、`inst_insight` 混入技術/消息、`news_insight` 混入技術指標數值；`_parse_analysis()` None-safe（2026-03-09）
- [x] **任務 C**：前端 `App.tsx` UI 改版——「LLM 分析報告」改為三張維度小卡（技術面/籌碼面/消息面）+ 一張綜合仲裁全寬卡；各卡標題旁附維度燈號；null 時降級不崩潰（2026-03-09）
- [x] **任務 C 補強**：三維小卡永遠顯示（無結果時內容灰化）；`InsightText` 元件按句號/分號斷段排版（2026-03-09）

#### 6. 基本面 / 估值工具（低優先）

> 規格 Tool Use 章節列出下列工具，目前完全未實作；屬進階功能，優先序低。

- [ ] `estimate_pe_percentile(symbol, pe)` — 與歷史 PE 分佈比較，回傳百分位
- [ ] `calculate_growth_rate(current, previous)` — YoY / MoM 標準化計算
- [ ] `StockSnapshot` / `GraphState` 補齊 `fundamentals` 資料段（財報、EPS、P/E 等），需研究穩定資料源

---

### 下一輪修正（消息面職責邊界 + 多筆新聞列表）

> 計劃文件：`docs/plans/2026-03-06-news-scope-and-display-items.md`
> **背景**：
> 1. 規格釐清：新聞（消息面）僅負責市場情緒訊號（法說會、政策、法人評等等），不負責財務數字（EPS/營收/毛利率屬基本面）；對應調整 quality_score 計分與 LLM Prompt
> 2. `news_display`（單筆）升級為 `news_display_items`（最多 5 筆陣列），前端可展示多筆近期新聞連結

- [x] NM-1：`NO_FINANCIAL_NUMBERS` flag 計分貢獻改為 0，旗標保留但不扣 quality_score
- [x] NM-2：LLM System Prompt 移除「從新聞提取財務數字」要求，改為聚焦事件情緒語義
- [x] NM-3：`GraphState` 新增 `news_display_items: list[dict]`（保留 `news_display` 向後相容）
- [x] NM-4：`quality_gate_node` 迭代 `raw_news_items[:5]`，產出 `news_display_items` 陣列
- [x] NM-5：`api.py` `AnalyzeResponse` 欄位更新（`news_display_items`）
- [x] NM-6：前端新聞卡片改為多筆列表，每筆可點擊連結 + 公開資訊觀測站提示
- [x] NM-7：補齊測試（state 欄位、node 輸出、API 欄位、quality_score 計分）

---

## 目前可用指令

```bash
cd backend
make run
```

```bash
cd backend
PYTHONPATH=src ./venv/bin/python -m ai_stock_sentinel.main --symbol 2330.TW --news-text "2026-03-03 ..."
```

```bash
cd backend
./venv/bin/python agent.py --text "2026-03-03 ..."
```

---

## 下一步建議（按計劃文件順序）

### Day 1（`docs/plans/2026-03-06-spec-gap-fix-day1.md`）
1. **Session 1**：技術位階指標（T1-1 ~ T1-6）— `yfinance_client` 補齊 `high_20d/low_20d/support_20d/resistance_20d`，`context_generator` 加敘事，`strategy_generator` 改用實際價格
2. **Session 2**：Action Plan 燈號（T2-0 ~ T2-5）— 先補 `rsi14` 獨立欄位（T2-0），再實作 `calculate_action_plan_tag()`，最後前端顯示

### Day 2（`docs/plans/2026-03-07-spec-gap-fix-day2.md`，依賴 Day 1 完成）
3. **Session 3**：AnalyzeResponse 欄位完整性 + AnalysisDetail 結構強化（T3-1 ~ T3-5）
4. **Session 4**：前端 `data_confidence < 60` 提示（T4-1）— 可與其他 Session 並行
5. **Session 5**：LLM Prompt 補齊消息面輸入（T5-1 ~ T5-4）— 可與其他 Session 並行
6. **Session 6**：`data_confidence` 語義修正（T6-1）— 可與其他 Session 並行
7. **Session 7**：DATE_UNKNOWN 信心分數懲罰 — 可與其他 Session 並行

### Session 8（`docs/plans/2026-03-07-dimensional-analysis.md`）✅ 完成（2026-03-09，302 tests passed）
8. ~~**任務 A**：`models.py` `AnalysisDetail` 新增四欄位（`tech_insight` / `inst_insight` / `news_insight` / `final_verdict`）~~
9. ~~**任務 B**：`langchain_analyzer.py` System Prompt + JSON schema 強制分維度輸出~~
10. ~~**任務 C**：前端 `App.tsx` 分維度小卡 UI~~

### 其他待辦（可穿插）
- **長期**：Phase 5 準備 — Docker / Railway 部署；基本面/估值工具（`estimate_pe_percentile`、`calculate_growth_rate`）
