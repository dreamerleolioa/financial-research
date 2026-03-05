# financial-research

AI Stock Sentinel 的基礎研究專案（Python / LangChain / yfinance）。

## 核心分析維度 (Core Analysis Dimensions)

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
- Phase 3（分析能力強化）：100%（Provider 抽象層 + ContextGenerator + Skeptic Mode + 信心分數多維加權 + Anthropic LLM 串接完成）
- Phase 4（前端儀表板）：95%（核心功能完成；Action Plan 燈號尚未實作）

測試：229 tests passed（截至 2026-03-05）

---

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
			quality_gate.py
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
docs/
	ai-stock-sentinel-architecture-spec.md
	backend-api-technical-spec.md
	implementation-task-breakdown.md
	progress-tracker.md
	development-execution-playbook.md
	plans/
		2026-03-04-multi-dimension-analysis.md
		2026-03-05-news-summary-quality.md
		2026-03-05-news-display-split.md
		2026-03-05-deep-analysis-upgrade.md
		2026-03-06-spec-gap-fix-day1.md
		2026-03-06-news-scope-and-display-items.md
		2026-03-07-spec-gap-fix-day2.md
```

---

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

若未設定 `ANTHROPIC_API_KEY`，系統會自動 fallback：
- 股票分析：回傳提示訊息（不會中斷）
- 新聞清潔：使用 heuristic 規則輸出 JSON

---

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
- 信心指數圓形元件（動態，含 `cross_validation_note`）
- 快照資訊（symbol / current_price / volume）
- AI 萃取純數據摘要（`cleaned_news`）
- LLM 分析報告（`analysis_detail`：summary + risks + technical_signal 標籤）
- 戰術行動 Action Plan（策略方向 / 入場區間 / 停損 / 持股期間）
- 新聞卡片（RSS 標題、日期、原文連結；來自 `news_display`）
- 新聞摘要品質提示（`quality_score < 60` 時顯示警告）
- 分析路徑圖（step timeline）
- 錯誤 banner + loading 狀態

### 1) Crawler Agent（股票 + 可選新聞清洗）

啟動預設流程（`2330.TW`）：

```bash
cd backend
make run
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

### 3) FastAPI 服務

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

### 4) 執行測試

```bash
cd backend
make test
```

---

## 輸出格式

POST `/analyze` 回傳 JSON 欄位：

| 欄位 | 說明 |
|------|------|
| `snapshot` | 股票快照（price / volume / recent_closes 等） |
| `analysis` | LLM 四步驟分析文字（Skeptic Mode；向後相容） |
| `analysis_detail` | LLM 結構化輸出（`summary` / `risks` / `technical_signal`） |
| `sentiment_label` | 消息面情緒標籤（`positive` / `negative` / `neutral`；從 `cleaned_news` 浮出） |
| `technical_signal` | 技術面訊號（`bullish` / `bearish` / `sideways`） |
| `institutional_flow` | 籌碼面訊號（`institutional_accumulation` / `distribution` / `retail_chasing` / `neutral` / `unknown`） |
| `cleaned_news` | 結構化新聞摘要（有新聞輸入時才有值） |
| `cleaned_news_quality` | 新聞摘要品質（`quality_score` 0–100 / `quality_flags`） |
| `news_display_items` | 前端顯示用近期新聞列表（最多 5 筆，每筆含 `title` / `date` / `source_url`；直接取 RSS 原始欄位，不經 LLM 清潔） |
| `confidence_score` | 信心分數 0–100（`signal_confidence` 別名，向後相容） |
| `signal_confidence` | 訊號強度分數（多維加權計算） |
| `data_confidence` | 資料完整度分數（0 / 33 / 67 / 100，依三維資料是否成功取得計算） |
| `cross_validation_note` | 三維交叉驗證備注（rule-based 固定字串） |
| `strategy_type` | 策略方向（`short_term` / `mid_term` / `defensive_wait`） |
| `entry_zone` | 建議入場區間（具體價格數值） |
| `stop_loss` | 防守底線（具體停損價位） |
| `holding_period` | 預期持股期間（具體時間窗，如「7-10 交易日」） |
| `action_plan` | 戰術行動摘要（`action` / `target_zone` / `defense_line` / `momentum_expectation`；rule-based 計算，資料不足時為 `null`） |
| `data_sources` | 實際成功抓取的資料來源列表（如 `["google-news-rss", "yfinance", "twse-openapi"]`） |
| `errors` | 錯誤陣列（每項含 `code`、`message`，正常為空陣列） |

Cleaner Agent 輸出 JSON 欄位固定為：
- `date`
- `title`
- `mentioned_numbers`
- `sentiment_label`（`positive` / `neutral` / `negative`）

---

## 待實作項目

依優先序排列（詳細規格見 `docs/plans/`）：

1. **P4-8** 消息面職責邊界 + 多筆新聞列表（`news_display` → `news_display_items` 最多 5 筆陣列）
2. **P4-7** 前端 `data_confidence < 60` 資料不足提示
3. **SG-1** 技術位階指標（`high_20d` / `low_20d` / `support_20d` / `resistance_20d`；讓 `entry_zone` / `stop_loss` 輸出實際價格）
4. **P4-6** Action Plan 燈號（`opportunity` / `overheated` / `neutral`；後端 rule-based 計算）
5. **SG-3/4** AnalyzeResponse 欄位完整性 + AnalysisDetail 結構強化
6. **SG-5** LLM Prompt 補齊消息面三維輸入
7. **SG-6** `data_confidence` 語義修正（`neutral` 情緒不應計為資料缺失）
