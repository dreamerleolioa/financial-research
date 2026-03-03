# financial-research

AI Stock Sentinel 的基礎研究專案（Python / LangChain / yfinance）。

## 需求與架構文件

- 技術架構需求文件：`docs/ai-stock-sentinel-architecture-spec.md`
- 後端 API 技術規格：`docs/backend-api-technical-spec.md`
- 實作任務拆解：`docs/implementation-task-breakdown.md`
- 進度追蹤：`docs/progress-tracker.md`
- 開發執行手冊：`docs/development-execution-playbook.md`

## 目前進度摘要

- Phase 1（MVP Backend）：約 85%（可執行）
- Phase 2（LangGraph 回圈）：0%
- Phase 3（分析能力強化）：約 20%
- Phase 4（前端儀表板）：約 35%

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
		agents/
			crawler_agent.py
		analysis/
			interface.py
			langchain_analyzer.py
			news_cleaner.py
		data_sources/
			yfinance_client.py
		config.py
		models.py
		main.py
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

## 環境變數（可選）

啟用 LLM 功能時使用：

```bash
export OPENAI_API_KEY="your_api_key"
export OPENAI_MODEL="gpt-4o-mini"
```

若未設定 `OPENAI_API_KEY`，系統會自動 fallback：
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
- 股票代碼輸入框（MVP）
- 信心指數圓形元件
- 雜訊過濾左右對照
- 分析路徑圖（step timeline）

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

Crawler Agent 輸出 JSON 範例欄位：
- `snapshot`: 股票快照資料
- `analysis`: 股票分析結果（LLM 或 fallback 訊息）
- `cleaned_news`: 有提供新聞輸入時才會出現
- `errors`: 錯誤陣列（每項含 `code`、`message`，正常為空陣列）

Cleaner Agent 輸出 JSON 欄位固定為：
- `date`
- `title`
- `mentioned_numbers`
- `sentiment_label`（`positive` / `neutral` / `negative`）
