# AI Stock Sentinel 進度追蹤

> 更新日期：2026-03-05

## 目前完成度（高層）

- **Phase 1（MVP Backend）**：100%（技術債清理完成：CLI 改走 graph 流程，StockCrawlerAgent / build_agent() 已移除）
- **Phase 2（LangGraph 回圈）**：100%（骨架 + judge 邏輯 + RSS 新聞抓取 + 新聞清潔接進 graph + graph 接進 API 完成）
- **Phase 3（分析能力強化）**：100%（Provider 抽象層 + preprocess_node + ContextGenerator + 策略建議模板 + Skeptic Mode + 信心分數 + 成本安全鎖 + Anthropic LLM 串接 + API 新欄位 + AnalysisDetail 結構化輸出 + code fence hotfix + Protocol 回傳型別對齊完成）
- **Phase 4（前端儀表板）**：95%（核心功能完成；新聞顯示資料拆分已完成）

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
- [ ] CS-4：信心分數定義 `data_confidence`（資料完整度，0–100）與 `signal_confidence`（訊號強度，0–100）分開回傳，前端可顯示「資料不足」提示而非固定 50
- [ ] CS-5：整合 `derive_technical_score` 進 `_derive_technical_signal`；補齊完整測試覆蓋

### 下一輪修正（Action Plan 燈號）

> 計劃文件：`docs/plans/2026-03-05-deep-analysis-upgrade.md`（Session 4）  
> 架構決策：燈號由**後端 rule-based Python 計算**並回傳 `action_plan_tag`；前端不含條件判斷，僅做 enum → emoji/文字顯示。

- [ ] 後端：實作 `calculate_action_plan_tag(rsi14, flow_label, confidence_score)` 純 Python（`opportunity` / `overheated` / `neutral`；任一輸入為 None 則降級回 `neutral`）
- [ ] 後端：`GraphState` 新增 `action_plan_tag` 欄位；`AnalyzeResponse` 新增 `action_plan_tag: str | null` 與 `institutional_flow: str | null`
- [ ] 後端：補齊單元測試（三情境 + None 安全 + API 欄位驗證）
- [ ] 前端：Action Plan 卡片標題旁顯示燈號標籤（`opportunity` → 🟢 機會 / `overheated` → 🔴 過熱 / `neutral` → 🔵 中性）
- [ ] 前端：`action_plan_tag` 為 null 時不顯示標籤，不崩潰

### 後續優化（多篇新聞情緒彙整）

> **背景**：目前 `fetch_news_node` 抓回多篇但只取第一篇組 `news_content` 送 LLM 清潔，情緒標籤代表性不足。新聞雖為落後指標，但多篇彙整後的市場情緒仍有參考價值。

- [ ] `fetch_news_node` 改取前 N 篇（建議 3~5 篇）組成多段 `news_content`，每篇以結構化標籤分隔
- [ ] `clean_node` / `FinancialNewsCleaner` 支援多篇輸入，輸出彙整後的 `sentiment_label`（majority vote 或加權）
- [ ] `news_display` 改為陣列（`news_display_items: list[NewsDisplay]`），前端顯示多篇標題+連結
- [ ] 補齊相關測試與 API 欄位更新

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

1. **優先執行**：信心分數可靠性優化（CS-1 ~ CS-5，依 `docs/plans/2026-03-05-deep-analysis-upgrade.md`）
2. **後續**：Action Plan 燈號（後端 rule-based + 前端顯示）
3. **後續**：Phase 5 準備 — Docker / Railway 部署
