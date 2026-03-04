# financial-research

AI Stock Sentinel 的基礎研究專案（Python / LangChain / yfinance）。

## 📈 核心分析維度 (Core Analysis Dimensions)

為了達成「理性偵察」的目標，系統將針對每一標的進行三位一體的數據掃描：

| 維度 | 追蹤指標 | AI 觀察重點 |
|------|----------|-------------|
| **消息面** | RSS 財經新聞、法說會摘要 | 提取事實、過濾誇大形容詞、識別情緒雜訊 |
| **技術面** | $MA_{5/20/60}$、$Vol$、$BIAS$、$RSI_{14}$ | 判斷當前股價位階，識別「利多出盡」或「底部起漲」 |
| **籌碼面** | 三大法人買賣超、融資餘額 | 追蹤聰明錢流向，判斷籌碼是集中於大戶還是分散至散戶 |

> **嚴格規範**：技術指標與籌碼數據必須由 Python 函式（`pandas` / `yfinance`）精確計算後，再交由 LLM 進行定性分析。**禁止 LLM 自行估算任何數值。**

---

## 需求與架構文件

- 技術架構需求文件：`docs/ai-stock-sentinel-architecture-spec.md`
- 後端 API 技術規格：`docs/backend-api-technical-spec.md`
- 實作任務拆解：`docs/implementation-task-breakdown.md`
- 進度追蹤：`docs/progress-tracker.md`
- 開發執行手冊：`docs/development-execution-playbook.md`

## 目前進度摘要

- Phase 1（MVP Backend）：100%（技術債清理完成，CLI 改走 graph 流程）
- Phase 2（LangGraph 回圈）：100%（骨架 + judge + RSS + clean + API 接入完成）
- Phase 3（分析能力強化）：約 85%（Provider 抽象層 + ContextGenerator + Skeptic Mode + 信心分數 + Anthropic LLM 串接完成）
- Phase 4（前端儀表板）：約 90%（API 串接、Action Plan 卡片、LLM 分析報告卡片完成）

## 專案目前內容

目前後端已完成兩個可執行功能：

1. 股票 Crawler Agent
	 - 使用 `yfinance` 抓取股票快照（預設 `2330.TW`）
	 - 可選擇用 LLM 做股票觀察分析
	 - 可額外接收財經新聞文字並清洗成結構化 JSON

2. 財經新聞 Cleaner Agent
	 - 輸入新聞文字（檔案、CLI 字串或 stdin）
	 - 輸出固定 JSON：`date`、`title`、`mentioned_numbers`、`sentiment_label`

## 目錄結構

```text
backend/
	Makefile
	requirements.txt
	agent.py
	src/ai_stock_sentinel/
		analysis/
			interface.py
			langchain_analyzer.py
			news_cleaner.py
			context_generator.py
			confidence_scorer.py
			strategy_generator.py
		data_sources/
			yfinance_client.py
			rss_news_client.py
			institutional_flow/
				interface.py
				router.py
				finmind_provider.py
				twse_provider.py
				tpex_provider.py
				tools.py
		graph/
			builder.py
			nodes.py
			state.py
		config.py
		models.py
		main.py
		api.py
frontend/
	package.json
	vite.config.ts
	src/
		App.tsx
		index.css
```

## 環境需求

- Python 3.10+
- 已建立 `backend/venv`
- Node.js 20+

## 安裝與啟動

```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt
```

或使用 Makefile：

```bash
cd backend
make install
```

## 環境變數（LLM）

啟用 Claude 功能時，請在 `backend/.env` 設定：

```bash
ANTHROPIC_API_KEY="your_api_key"
ANTHROPIC_MODEL="claude-sonnet-4-5"
```

### 換電腦開發時要注意

- `.env` 不會進版控（也不應進版控），所以在新電腦上**需要手動重建一份** `backend/.env`。
- 建議直接複製 `backend/.env.example` 建立：`cp backend/.env.example backend/.env`

### Claude API Key 取得方式

1. 前往 `https://console.anthropic.com/`
2. 登入後進入 **API Keys** 頁面
3. 建立（Create Key）或使用既有 key，並立即複製保存

若未設定 `ANTHROPIC_API_KEY`，系統會自動 fallback：
- 股票分析：回傳提示訊息（不會中斷）
- 新聞清潔：使用 heuristic 規則輸出 JSON

## 使用方式

### 0) 前端儀表板（React + Tailwind）

```bash
cd frontend
pnpm install
pnpm dev
```

預設開啟：`http://localhost:5173`

目前前端已包含：
- 股票代碼輸入框 + 一鍵分析
- 信心指數圓形元件（動態，含 cross_validation_note）
- 快照資訊（symbol / current_price / volume）
- AI 萃取純數據摘要（cleaned_news）
- LLM 分析報告（四步驟全文）
- 戰術行動 Action Plan（策略方向 / 入場區間 / 停損 / 持股期間）
- 分析路徑圖（step timeline）
- 錯誤 banner + loading 狀態

### 1) Crawler Agent（股票 + 可選新聞清洗）

啟動預設流程（`2330.TW`）：

```bash
cd backend
make run
```

等價指令：

```bash
cd backend
PYTHONPATH=src ./venv/bin/python -m ai_stock_sentinel.main
```

指定股票代碼：

```bash
PYTHONPATH=src ./venv/bin/python -m ai_stock_sentinel.main --symbol 2317.TW
```

附加新聞文字（直接字串）：

```bash
PYTHONPATH=src ./venv/bin/python -m ai_stock_sentinel.main \
	--news-text "2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%"
```

附加新聞文字（檔案）：

```bash
PYTHONPATH=src ./venv/bin/python -m ai_stock_sentinel.main --news-file ./news.txt
```

### 2) Cleaner Agent（只做新聞清潔）

```bash
cd backend
./venv/bin/python agent.py --text "2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%"
```

讀檔：

```bash
./venv/bin/python agent.py --file ./news.txt
```

stdin：

```bash
cat news.txt | ./venv/bin/python agent.py
```

### 3) FastAPI 服務（新增）

```bash
cd backend
make run-api
```

預設開啟：`http://127.0.0.1:8000`

- `GET /health`
- `POST /analyze`

範例：

```bash
curl -X POST http://127.0.0.1:8000/analyze \
	-H "Content-Type: application/json" \
	-d '{"symbol":"2330.TW","news_text":"2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%"}'
```

### 4) 執行測試（新增）

```bash
cd backend
make test
```

## 輸出格式

POST `/analyze` 回傳 JSON 欄位：
- `snapshot`: 股票快照（price / volume / recent_closes 等）
- `analysis`: LLM 四步驟分析文字（Skeptic Mode）
- `cleaned_news`: 結構化新聞摘要（有新聞輸入時才有值）
- `confidence_score`: 信心分數 0–100（rule-based 計算）
- `cross_validation_note`: 三維交叉驗證備注
- `strategy_type`: 策略方向（`short_term` / `mid_term` / `defensive_wait`）
- `entry_zone`: 建議入場區間
- `stop_loss`: 防守底線（停損）
- `holding_period`: 預期持股期間
- `errors`: 錯誤陣列（每項含 `code`、`message`，正常為空陣列）

Cleaner Agent 輸出 JSON 欄位固定為：
- `date`
- `title`
- `mentioned_numbers`
- `sentiment_label`（`positive` / `neutral` / `negative`）
