# AI Stock Sentinel 進度追蹤

> 更新日期：2026-03-04

## 目前完成度（高層）

- **Phase 1（MVP Backend）**：100%（技術債清理完成：CLI 改走 graph 流程，StockCrawlerAgent / build_agent() 已移除）
- **Phase 2（LangGraph 回圈）**：100%（骨架 + judge 邏輯 + RSS 新聞抓取 + 新聞清潔接進 graph + graph 接進 API 完成）
- **Phase 3（分析能力強化）**：約 65%（Provider 抽象層 + preprocess_node + ContextGenerator + 策略建議模板 + Skeptic Mode + 信心分數完成）
- **Phase 4（前端儀表板）**：約 35%（前端骨架與核心視覺元件已完成）

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

- [ ] Task 1：新增 `TechnicalData`、`InstitutionalData`、`AnalysisDetail` Pydantic 模型（`models.py`）
- [ ] Task 2：`YFinanceCrawler` 新增 `calculate_technical_indicators()`（Pandas：MA/BIAS/RSI/量比）
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
- [ ] Task 7：`langchain_analyzer.py` 升級為三維交叉驗證 Prompt，輸出結構化 `AnalysisDetail`
- [ ] Task 7.1：`AnalysisDetail` 新增 `strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
- [ ] Task 7.2：策略模板映射（短線/中線/防守觀望）與持股期間規則化
- [ ] Task 7.5：LLM Provider 串接（主用 Claude）
	- [ ] 串接前提醒使用者提供：`ANTHROPIC_API_KEY`
	- [ ] 串接前提醒使用者確認：模型名稱（預設 `claude-sonnet-4`）
	- [ ] 串接前提醒使用者確認：成本/速度偏好（品質優先 / 成本優先 / 平衡）
	- [ ] 串接前提醒使用者確認：輸出格式（純文字或 JSON）
	- [ ] 串接前提醒使用者確認：失敗策略（timeout 與 retry 次數）
- [ ] Task 7.6：LLM 呼叫前成本安全鎖（`langchain_analyzer.py`）
	- 在呼叫 LLM 前估算 input token 數（字串長度 / 4），換算預估費用（sonnet-4：input $3/M）
	- 預估費用超過 $1 USD → 拋出 `ValueError`，說明估算 token 數與費用，不呼叫 LLM
	- 純字串長度估算，不安裝 tiktoken
- [ ] Task 8：`api.py` `AnalyzeResponse` 新增 `technical`、`institutional`、`analysis_detail`
- [ ] Task 8.1：`AnalyzeResponse.analysis_detail.action_plan` 子結構定義與回傳
- [ ] Task 9：整合測試（`make test` 全通過，覆蓋降級路徑）
- [x] Task C3（P3-C3）：策略建議模板（`analysis/strategy_generator.py`）（2026-03-04）
  - `generate_strategy(technical_context_data, inst_data)` 純 rule-based
  - 映射規則：`short_term`（利多+RSI<30）/ `mid_term`（法人吸籌+均線多頭）/ `defensive_wait`（訊號衝突/高乖離）
  - `GraphState` 新增 `strategy_type` / `entry_zone` / `stop_loss` / `holding_period` 欄位
  - `graph/nodes.py` 新增 `strategy_node`；`builder.py` 串接 `analyze → strategy → END`
  - 技術指標輔助函式升為 public API（`calc_bias` / `calc_rsi` / `ma`）
  - 測試：23 個（全訊號組合 + 邊界值 + 優先序 + None 安全）；全套 117 passed

### Phase 4：前端
- [ ] 串接後端 API（symbol -> 真實分析結果）
- [ ] 元件改為真實資料驅動（非靜態假資料）
- [ ] 補上錯誤狀態與載入狀態
- [ ] `analysis_detail` 新增「戰術行動（Action Plan）」卡片（操作方向/建議區間/防守底線/預期動能）

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

## 下一步建議（Top 3）

1. **Task 7.5**：串接 LLM Provider（需確認 `ANTHROPIC_API_KEY`，模型 `claude-sonnet-4`），讓 `analyze_node` 真正呼叫 LLM 並回傳 `summary` / `risks`
2. **Task 8**：`/analyze` API 回傳 `confidence_score` / `cross_validation_note` / `strategy_type` / `entry_zone` / `stop_loss` / `holding_period` 欄位
3. **Task 9**：整合測試（`make test` 全通過，覆蓋降級路徑）
