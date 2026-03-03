# AI Stock Sentinel 進度追蹤

> 更新日期：2026-03-03

## 目前完成度（高層）

- **Phase 1（MVP Backend）**：100%（技術債清理完成：CLI 改走 graph 流程，StockCrawlerAgent / build_agent() 已移除）
- **Phase 2（LangGraph 回圈）**：100%（骨架 + judge 邏輯 + RSS 新聞抓取 + 新聞清潔接進 graph + graph 接進 API 完成）
- **Phase 3（分析能力強化）**：約 20%（有基礎清潔與情緒標籤）
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
- [ ] Task 3：新增 `data_sources/institutional_client.py`（法人買賣超、融資融券，含降級策略）
- [ ] Task 4：`GraphState` 擴充 `technical`、`institutional`、`analysis_detail` 欄位
- [ ] Task 5：`graph/nodes.py` 新增 `fetch_technical_node`、`fetch_institutional_node`；升級 `analyze_node`
- [ ] Task 6：`graph/builder.py` 流程加入 `fetch_technical → fetch_institutional` 兩步
- [ ] Task 7：`langchain_analyzer.py` 升級為三維交叉驗證 Prompt，輸出結構化 `AnalysisDetail`
- [ ] Task 7.5：LLM Provider 串接（主用 Claude）
	- [ ] 串接前提醒使用者提供：`ANTHROPIC_API_KEY`
	- [ ] 串接前提醒使用者確認：模型名稱（預設 `claude-sonnet-4`）
	- [ ] 串接前提醒使用者確認：成本/速度偏好（品質優先 / 成本優先 / 平衡）
	- [ ] 串接前提醒使用者確認：輸出格式（純文字或 JSON）
	- [ ] 串接前提醒使用者確認：失敗策略（timeout 與 retry 次數）
- [ ] Task 8：`api.py` `AnalyzeResponse` 新增 `technical`、`institutional`、`analysis_detail`
- [ ] Task 9：整合測試（`make test` 全通過，覆蓋降級路徑）

### Phase 4：前端
- [ ] 串接後端 API（symbol -> 真實分析結果）
- [ ] 元件改為真實資料驅動（非靜態假資料）
- [ ] 補上錯誤狀態與載入狀態

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

1. P3-0：先完成籌碼資料源確認（Provider abstraction + `fetch_institutional_flow("2330.TW", days=5)`）
2. P3-2：完成 Preprocess Node（`generate_technical_context`、`quantify_to_narrative`）
3. P3-3：完成 Skeptic Mode + rule-based score（`confidence_score` / `cross_validation_note`）
