# financial-research

AI Stock Sentinel 的基礎研究專案（Python / LangChain / yfinance）。

## 核心分析維度 (Core Analysis Dimensions)

為了達成「理性偵察」的目標，系統將針對每一標的進行四維數據掃描：

| 維度       | 追蹤指標                                  | AI 觀察重點                                        |
| ---------- | ----------------------------------------- | -------------------------------------------------- |
| **消息面** | RSS 財經新聞、法說會摘要                  | 提取事實、過濾誇大形容詞、識別情緒雜訊             |
| **技術面** | $MA_{5/20/60}$、$Vol$、$BIAS$、$RSI_{14}$ | 判斷當前股價位階，識別「利多出盡」或「底部起漲」   |
| **籌碼面** | 三大法人買賣超、融資餘額                  | 追蹤聰明錢流向，判斷籌碼是集中於大戶還是分散至散戶 |
| **基本面** | 本益比區間、歷史估值帶                    | 判斷當前股價相對估值位階，識別高估或低估區間       |

> **嚴格規範**：技術指標與籌碼數據必須由 Python 函式（`pandas` / `yfinance`）精確計算後，再交由 LLM 進行定性分析。**禁止 LLM 自行估算任何數值。**

---

## 需求與架構文件

- 技術架構需求文件：`docs/ai-stock-sentinel-architecture-spec.md`
- 後端 API 技術規格：`docs/specs/backend-api-technical-spec.md`
- 自動化審核規格：`docs/ai-stock-sentinel-automation-review-spec.md`
- 持股診斷規格：`docs/ai-stock-sentinel-position-diagnosis-spec.md`
- 執行計劃目錄：`docs/plans/`
- 開發執行手冊：`docs/development-execution-playbook.md`
- 後續優化路線圖：`docs/research/post-new-position-strategy-optimization-roadmap.md`

## 目前進度摘要

- 目前以各需求對應的計劃文件與 spec 為準，不再維護集中式 phase 百分比追蹤。
- 最新工作請直接查看 `docs/plans/` 下對應日期的 implementation plan。

---

## 目錄結構

```text
backend/
	Makefile
	requirements.txt
	scripts/
		backtest_win_rate.py    # 勝率回測 CLI 腳本
	src/ai_stock_sentinel/
		analysis/
			interface.py
			langchain_analyzer.py
			news_cleaner.py
			quality_gate.py
			context_generator.py
			confidence_scorer.py
			strategy_generator.py
			position_scorer.py
			metrics.py
		auth/
			dependencies.py
			google_verifier.py
			jwt_handler.py
			router.py
		data_sources/
			yfinance_client.py
			rss_news_client.py
			finmind_token.py
			institutional_flow/
				interface.py
				router.py           # 三層 fallback：FinMind → TWSE → TPEX
				finmind_provider.py
				twse_provider.py
				tpex_provider.py
				tools.py
			fundamental/
				interface.py
				finmind_provider.py
				tools.py
		daily_radar/
			router.py
			universe.py
			institutional_universe_provider.py
			raw_data.py
			data_loader.py
			prefilter.py
			scoring.py
			service.py
		db/
			models.py               # DailyAnalysisLog / StockRawData / StockAnalysisCache / DailyRadarRun / DailyRadarCandidate
			session.py
		graph/
			builder.py
			nodes.py
			state.py
		portfolio/
			router.py
			history_router.py
		services/
			history_loader.py
		user_models/
			user.py
		config.py
		models.py
		main.py
		api.py
frontend/
	package.json
	vite.config.ts
	src/
		App.tsx
		pages/
			AnalyzePage.tsx
			PortfolioPage.tsx
			ClosedPortfolioPage.tsx
			DashboardPage.tsx
			DailyRadarPage.tsx
			LoginPage.tsx
			LoginCallbackPage.tsx
		components/
			ConfidenceChart.tsx
			InsightText.tsx
		lib/
			auth.ts
			formatters.ts
			historyApi.ts
			portfolioTypes.ts
			dailyRadarApi.ts
			dailyRadarTypes.ts
		stores/
			auth.tsx
			theme.ts
.github/
	workflows/
		deploy.yml              # CI/CD：測試 → GitHub Pages + Render
		daily-radar.yml        # Daily Radar 內部排程
docs/
	ai-stock-sentinel-architecture-spec.md
	specs/backend-api-technical-spec.md
	ai-stock-sentinel-automation-review-spec.md
	ai-stock-sentinel-position-diagnosis-spec.md
	development-execution-playbook.md
	plans/                      # 各功能 implementation plan（依日期命名）
	research/                   # 研究文件與優化路線圖
```

---

## 部署

Push to `main` 自動觸發：後端跑測試 → 前端 build 並部署到 GitHub Pages → 觸發 Render 重新部署。

> Render 免費方案閒置 15 分鐘後會 sleep，第一次呼叫需等約 30 秒喚醒。

Daily Radar 另有 GitHub Actions workflow，可手動執行或於台灣市場交易日收盤後排程執行。此 workflow 會 POST 到 `${ZEABUR_BACKEND_URL}/internal/daily-radar/run`，用 `DAILY_RADAR_INTERNAL_TOKEN` 做內部 API 驗證，request body 固定帶 `{ "market": "TW" }`。後端會自行選出 multi-track universe（保留法人雙軌，並加入本地 final `StockRawData` 可支撐的日頻技術 trigger tracks），對缺少資料的 selected symbols 做 yfinance OHLCV batch backfill，執行 Stage 1/2 rule-based scoring，並持久化 Daily Radar candidates。

Daily Radar 的 live 資料載入有 request budget：FinMind 法人資料只用 all-market 同日查詢與近期 date-range 查詢，不做逐檔 `stock_id`、`data_id` 或 `symbol` request；yfinance 只對 selected universe 中缺少 final raw row 的 symbols 做一次 batch download，既有 `StockRawData` 會重用。

CI/CD 設定說明：`docs/plans/2026-03-10-cicd-github-pages-render.md`

---

## 環境需求

- Python 3.10+
- 已建立 `backend/venv`
- Node.js 20+（前端用 pnpm 10）

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

## 環境變數

### 本機開發（backend/.env）

```bash
ANTHROPIC_API_KEY="your_api_key"
ANTHROPIC_MODEL="claude-sonnet-4-6"
CORS_ORIGINS="http://localhost:5173,https://<username>.github.io"
GOOGLE_CLIENT_ID="your_google_client_id"    # Google OAuth 登入用
JWT_SECRET="your_jwt_secret"
DATABASE_URL="postgresql://..."             # 本機可用 SQLite
DAILY_RADAR_INTERNAL_TOKEN="..."            # Daily Radar 內部執行 API 用
```

> `.env` 不進版控。換電腦時複製 `backend/.env.example` 建立：`cp backend/.env.example backend/.env`

若未設定 `ANTHROPIC_API_KEY`，系統會自動 fallback：

- 股票分析：回傳提示訊息（不會中斷）
- 新聞清潔：使用 heuristic 規則輸出 JSON

### CI/CD（GitHub Actions）

| 類型     | 名稱                         | 用途                                              |
| -------- | ---------------------------- | ------------------------------------------------- |
| Secret   | `RENDER_DEPLOY_HOOK_URL`     | 觸發 Render 重新部署                              |
| Secret   | `ZEABUR_BACKEND_URL`         | Daily Radar workflow 呼叫的 Zeabur 後端 URL       |
| Secret   | `DAILY_RADAR_INTERNAL_TOKEN` | Daily Radar workflow 呼叫內部 API 的 Bearer token |
| Variable | `VITE_API_URL`               | 前端 build 時注入後端 URL                         |

### 生產環境（Render Environment Variables）

| 名稱                | 值                                                   |
| ------------------- | ---------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Anthropic API key                                    |
| `ANTHROPIC_MODEL`   | `claude-sonnet-4-6`                                  |
| `CORS_ORIGINS`      | `http://localhost:5173,https://<username>.github.io` |
| `GOOGLE_CLIENT_ID`  | Google OAuth client ID                               |
| `JWT_SECRET`        | JWT 簽名密鑰                                         |
| `DATABASE_URL`      | PostgreSQL 連線字串                                  |

### Zeabur 後端環境變數（Daily Radar）

| 名稱                         | 值                                    |
| ---------------------------- | ------------------------------------- |
| `DAILY_RADAR_INTERNAL_TOKEN` | 與 GitHub Actions secret 同一組 token |

---

## 使用方式

### 前端儀表板（React + Tailwind）

```bash
cd frontend
pnpm install
pnpm dev
```

預設開啟：`http://localhost:5173`

目前前端已包含：

**新倉分析頁（`/analyze`）**

- 股票代碼輸入框 + 一鍵分析
- 訊號強度與資料品質提示（含 `cross_validation_note`；`data_confidence < 60` 時顯示資料不足提示；raw score 保留為內部排序、校準與 advanced trace）
- 快照資訊（symbol / current_price / volume）
- 分析報告四維小卡（技術面 / 籌碼面 / 基本面 / 消息面）+ 綜合仲裁全寬卡
- 戰術行動 Action Plan（策略方向 / 入場區間 / 停損 / 持股期間；含 `action_plan_tag` 燈號 badge：🟢 機會 / 🔴 過熱 / 🔵 中性）
- 新聞卡片（RSS 標題、日期、原文連結；多筆列表來自 `news_display_items`，最多 5 筆）
- 新聞摘要品質提示（`quality_score < 60` 時顯示警告）
- 錯誤 banner + loading 狀態
- 底層保留 `GET /history/{symbol}`、`historyApi.ts` 與 `ConfidenceChart.tsx`，供後續嵌入個股歷史分析趨勢

**持股管理頁（`/portfolio`）**

- 現有持股列表與持股診斷入口
- 倉位狀態卡（獲利安全區 / 成本邊緣 / 套牢防守；顯示成本價 / 現價 / 損益%）
- 操作建議卡（續抱 / 減碼 / 出場；顯示動態防守位）
- 出場警示 banner（`exit_reason` 非 null 時紅色顯示）
- 出場 / 結案流程：輸入出場日期、價格、股數、手續費與交易稅，後端計算已實現損益、報酬率與持有天數
- 四維分析卡（技術面防守 / 主力動向 / 消息面風險 / 基本面）+ 綜合研判

**已結案持股頁（`/portfolio/closed`）**

- 獨立頁面保留結案紀錄與歷史診斷，不再把出場等同刪除追蹤
- 期間篩選：1天 / 1週 / 1月 / 1季 / 1年，並顯示篩選後的 `已實現損益` 總計

**Daily Radar（`/daily-radar`）**

- 每日觀察候選清單
- bucket、觀察等級、風險標籤與規則命中原因；raw observation score 用於內部排序與 advanced trace

**登入（`/login`）**

- Google OAuth 登入流程

### FastAPI 服務

```bash
cd backend
make run-api
```

預設開啟：`http://127.0.0.1:8000`

- `GET /health`
- `POST /analyze` — 新倉策略分析
- `POST /analyze/position` — 持股操作建議
- `POST /internal/fetch-raw-data` — 觸發原始資料預取（內部用）
- `POST /internal/daily-radar/run`：執行 Daily Radar 自包含內部流程，包含 multi-track universe selection、selected-symbol OHLCV batch backfill、Stage 1/2 scoring 與 candidates persistence，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `GET /daily-radar/latest`：讀取最新 Daily Radar 候選清單
- `GET /daily-radar/{run_date}`：讀取指定日期 Daily Radar 候選清單
- `GET /daily-radar/symbol/{symbol}`：讀取指定標的 Daily Radar 歷史
- `GET /history/{symbol}` — 查詢歷史分析記錄
- `GET/POST /auth/*` — Google OAuth 登入流程
- `GET/POST /portfolio/*` — 持股管理、持股診斷歷史、出場結案與已結案紀錄

範例：

```bash
curl -X POST http://127.0.0.1:8000/analyze \
	-H "Content-Type: application/json" \
	-d '{"symbol":"2330.TW","news_text":"2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%"}'

curl -X POST http://127.0.0.1:8000/analyze/position \
	-H "Content-Type: application/json" \
	-d '{"symbol":"2330.TW","entry_price":980}'
```

### 4) 執行測試

```bash
cd backend
make test
```

---

## 輸出格式

### POST `/analyze`

回傳 JSON 欄位：

| 欄位                       | 說明                                                                                                                                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `snapshot`                 | 股票快照（price / volume / recent_closes 等）                                                                                                                                                      |
| `analysis`                 | LLM 四步驟分析文字（Skeptic Mode；向後相容）                                                                                                                                                       |
| `analysis_detail`          | LLM 結構化輸出（`summary` / `risks` / `technical_signal` / `institutional_flow` / `sentiment_label` / `tech_insight` / `inst_insight` / `news_insight` / `final_verdict` / `fundamental_insight`） |
| `technical_indicators`     | 技術指標顯性輸出（布林通道上中下軌、bandwidth、位階、MACD 線 / 訊號線 / 柱狀體 / bias）                                                                                                            |
| `sentiment_label`          | 消息面情緒標籤（`positive` / `negative` / `neutral`；從 `cleaned_news` 浮出）                                                                                                                      |
| `institutional_flow_label` | 籌碼面標籤（`institutional_accumulation` / `distribution` / `retail_chasing` / `neutral`）                                                                                                         |
| `cleaned_news`             | 結構化新聞摘要（有新聞輸入時才有值）                                                                                                                                                               |
| `cleaned_news_quality`     | 新聞摘要品質（`quality_score` 0–100 / `quality_flags`）                                                                                                                                            |
| `news_display_items`       | 前端顯示用近期新聞列表（最多 5 筆，每筆含 `title` / `date` / `source_url`；直接取 RSS 原始欄位，不經 LLM 清潔）                                                                                    |
| `action_plan_tag`          | 綜合行動燈號（`opportunity` / `overheated` / `neutral`；rule-based 計算，任一輸入為 null 時降級回 `neutral`）                                                                                      |
| `confidence_score`         | 信心分數 0–100（`signal_confidence` 別名，向後相容）                                                                                                                                               |
| `signal_confidence`        | 訊號強度分數（多維加權計算）                                                                                                                                                                       |
| `data_confidence`          | 資料完整度分數（0 / 33 / 67 / 100，依三維資料是否成功取得計算）                                                                                                                                    |
| `cross_validation_note`    | 三維交叉驗證備注（rule-based 固定字串）                                                                                                                                                            |
| `strategy_type`            | 策略方向（`short_term` / `mid_term` / `defensive_wait`）                                                                                                                                           |
| `entry_zone`               | 建議入場區間（具體價格數值）                                                                                                                                                                       |
| `stop_loss`                | 防守底線（具體停損價位）                                                                                                                                                                           |
| `holding_period`           | 預期持股期間（具體時間窗，如「7-10 交易日」）                                                                                                                                                      |
| `action_plan`              | 戰術行動摘要（`action` / `target_zone` / `defense_line` / `momentum_expectation`；rule-based 計算，資料不足時為 `null`）                                                                           |
| `data_sources`             | 實際成功抓取的資料來源列表（如 `["google-news-rss", "yfinance", "twse-openapi"]`）                                                                                                                 |
| `position_analysis`        | 持股診斷結果（`/analyze/position` 才有值；含 `profit_loss_pct` / `position_status` / `trailing_stop` / `recommended_action` / `exit_reason`）                                                      |
| `errors`                   | 錯誤陣列（每項含 `code`、`message`，正常為空陣列）                                                                                                                                                 |

### POST `/analyze/position`

額外必填欄位：`entry_price`（float）。回傳同上，`position_analysis` 欄位為：

| 欄位                   | 說明                                          |
| ---------------------- | --------------------------------------------- |
| `entry_price`          | 購入成本價                                    |
| `profit_loss_pct`      | 損益百分比（Python 計算，非 LLM）             |
| `position_status`      | `profitable_safe` / `at_risk` / `under_water` |
| `trailing_stop`        | 動態防守位（依獲利區間規則計算）              |
| `trailing_stop_reason` | 防守位計算邏輯說明                            |
| `recommended_action`   | `Hold` / `Trim` / `Exit`（4 規則 rule-based） |
| `exit_reason`          | 出場理由（僅 `Exit` 時非 null）               |

---
