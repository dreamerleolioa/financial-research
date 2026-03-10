# financial-research

AI Stock Sentinel 的基礎研究專案（Python / LangChain / yfinance）。

## 核心分析維度 (Core Analysis Dimensions)

為了達成「理性偵察」的目標，系統將針對每一標的進行三位一體的數據掃描：

| 維度       | 追蹤指標                                  | AI 觀察重點                                        |
| ---------- | ----------------------------------------- | -------------------------------------------------- |
| **消息面** | RSS 財經新聞、法說會摘要                  | 提取事實、過濾誇大形容詞、識別情緒雜訊             |
| **技術面** | $MA_{5/20/60}$、$Vol$、$BIAS$、$RSI_{14}$ | 判斷當前股價位階，識別「利多出盡」或「底部起漲」   |
| **籌碼面** | 三大法人買賣超、融資餘額                  | 追蹤聰明錢流向，判斷籌碼是集中於大戶還是分散至散戶 |

> **嚴格規範**：技術指標與籌碼數據必須由 Python 函式（`pandas` / `yfinance`）精確計算後，再交由 LLM 進行定性分析。**禁止 LLM 自行估算任何數值。**

---

## 需求與架構文件

- 技術架構需求文件：`docs/ai-stock-sentinel-architecture-spec.md`
- 後端 API 技術規格：`docs/backend-api-technical-spec.md`
- 實作任務拆解：`docs/implementation-task-breakdown.md`
- 進度追蹤：`docs/progress-tracker.md`
- 開發執行手冊：`docs/development-execution-playbook.md`

## 目前進度摘要

- Phase 1（MVP Backend）：100%
- Phase 2（LangGraph 回圈）：100%
- Phase 3（分析能力強化）：100%
- Phase 4（前端儀表板）：100%
- Phase 5（基本面估值）：100%
- Phase 6（持股診斷）：100%（`POST /analyze/position` + 前端我的持股分頁）

測試：360 tests passed（截至 2026-03-10）

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
			position_scorer.py
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
			fundamental/
				interface.py
				finmind_provider.py
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
		pages/
			PositionPage.tsx
.github/
	workflows/
		deploy.yml        # CI/CD：測試 → GitHub Pages + Render
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
		2026-03-07-dimensional-analysis.md
		2026-03-10-cicd-github-pages-render.md
		2026-03-10-position-diagnosis.md
		2026-03-10-strategy-action-plan-deepening.md
```

---

## 部署

Push to `main` 自動觸發：後端跑測試 → 前端 build 並部署到 GitHub Pages → 觸發 Render 重新部署。

> Render 免費方案閒置 15 分鐘後會 sleep，第一次呼叫需等約 30 秒喚醒。

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
ANTHROPIC_MODEL="claude-sonnet-4-5"
CORS_ORIGINS="http://localhost:5173,https://<username>.github.io"
```

> `.env` 不進版控。換電腦時複製 `backend/.env.example` 建立：`cp backend/.env.example backend/.env`

若未設定 `ANTHROPIC_API_KEY`，系統會自動 fallback：

- 股票分析：回傳提示訊息（不會中斷）
- 新聞清潔：使用 heuristic 規則輸出 JSON

### CI/CD（GitHub Actions）

| 類型     | 名稱                     | 用途                      |
| -------- | ------------------------ | ------------------------- |
| Secret   | `RENDER_DEPLOY_HOOK_URL` | 觸發 Render 重新部署      |
| Variable | `VITE_API_URL`           | 前端 build 時注入後端 URL |

### 生產環境（Render Environment Variables）

| 名稱                | 值                                                   |
| ------------------- | ---------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Anthropic API key                                    |
| `ANTHROPIC_MODEL`   | `claude-sonnet-4-5`                                  |
| `CORS_ORIGINS`      | `http://localhost:5173,https://<username>.github.io` |

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

**個股分析 tab**
- 股票代碼輸入框 + 一鍵分析
- 信心指數圓形元件（動態，含 `cross_validation_note`；`data_confidence < 60` 時顯示資料不足提示）
- 快照資訊（symbol / current_price / volume）
- 分析報告四維小卡（技術面 / 籌碼面 / 基本面 / 消息面）+ 綜合仲裁全寬卡
- 戰術行動 Action Plan（策略方向 / 入場區間 / 停損 / 持股期間；含 `action_plan_tag` 燈號 badge：🟢 機會 / 🔴 過熱 / 🔵 中性）
- 新聞卡片（RSS 標題、日期、原文連結；多筆列表來自 `news_display_items`，最多 5 筆）
- 新聞摘要品質提示（`quality_score < 60` 時顯示警告）
- 錯誤 banner + loading 狀態

**我的持股 tab**
- 持股診斷表單（股票代碼 + 購入成本價 + 選填日期 / 數量）
- 倉位狀態卡（獲利安全區 / 成本邊緣 / 套牢防守；顯示成本價 / 現價 / 損益%）
- 操作建議卡（續抱 / 減碼 / 出場；顯示動態防守位）
- 出場警示 banner（`exit_reason` 非 null 時紅色顯示）
- 三維分析卡（技術面防守 / 主力動向 / 消息面風險）+ 綜合研判

### FastAPI 服務

```bash
cd backend
make run-api
```

預設開啟：`http://127.0.0.1:8000`

- `GET /health`
- `POST /analyze`
- `POST /analyze/position`

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

| 欄位                    | 說明                                                                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `snapshot`              | 股票快照（price / volume / recent_closes 等）                                                                            |
| `analysis`              | LLM 四步驟分析文字（Skeptic Mode；向後相容）                                                                             |
| `analysis_detail`       | LLM 結構化輸出（`summary` / `risks` / `technical_signal` / `institutional_flow` / `sentiment_label`）                    |
| `sentiment_label`       | 消息面情緒標籤（`positive` / `negative` / `neutral`；從 `cleaned_news` 浮出）                                            |
| `technical_signal`      | 技術面訊號（`bullish` / `bearish` / `sideways`）                                                                         |
| `institutional_flow`    | 籌碼面訊號（`institutional_accumulation` / `distribution` / `retail_chasing` / `neutral` / `unknown`）                   |
| `cleaned_news`          | 結構化新聞摘要（有新聞輸入時才有值）                                                                                     |
| `cleaned_news_quality`  | 新聞摘要品質（`quality_score` 0–100 / `quality_flags`）                                                                  |
| `news_display_items`    | 前端顯示用近期新聞列表（最多 5 筆，每筆含 `title` / `date` / `source_url`；直接取 RSS 原始欄位，不經 LLM 清潔）          |
| `action_plan_tag`       | 綜合行動燈號（`opportunity` / `overheated` / `neutral`；rule-based 計算，任一輸入為 null 時降級回 `neutral`）            |
| `confidence_score`      | 信心分數 0–100（`signal_confidence` 別名，向後相容）                                                                     |
| `signal_confidence`     | 訊號強度分數（多維加權計算）                                                                                             |
| `data_confidence`       | 資料完整度分數（0 / 33 / 67 / 100，依三維資料是否成功取得計算）                                                          |
| `cross_validation_note` | 三維交叉驗證備注（rule-based 固定字串）                                                                                  |
| `strategy_type`         | 策略方向（`short_term` / `mid_term` / `defensive_wait`）                                                                 |
| `entry_zone`            | 建議入場區間（具體價格數值）                                                                                             |
| `stop_loss`             | 防守底線（具體停損價位）                                                                                                 |
| `holding_period`        | 預期持股期間（具體時間窗，如「7-10 交易日」）                                                                            |
| `action_plan`           | 戰術行動摘要（`action` / `target_zone` / `defense_line` / `momentum_expectation`；rule-based 計算，資料不足時為 `null`） |
| `data_sources`          | 實際成功抓取的資料來源列表（如 `["google-news-rss", "yfinance", "twse-openapi"]`）                                       |
| `position_analysis`     | 持股診斷結果（`/analyze/position` 才有值；含 `profit_loss_pct` / `position_status` / `trailing_stop` / `recommended_action` / `exit_reason`） |
| `errors`                | 錯誤陣列（每項含 `code`、`message`，正常為空陣列）                                                                       |

### POST `/analyze/position`

額外必填欄位：`entry_price`（float）。回傳同上，`position_analysis` 欄位為：

| 欄位 | 說明 |
| --- | --- |
| `entry_price` | 購入成本價 |
| `profit_loss_pct` | 損益百分比（Python 計算，非 LLM） |
| `position_status` | `profitable_safe` / `at_risk` / `under_water` |
| `trailing_stop` | 動態防守位（依獲利區間規則計算） |
| `trailing_stop_reason` | 防守位計算邏輯說明 |
| `recommended_action` | `Hold` / `Trim` / `Exit`（4 規則 rule-based） |
| `exit_reason` | 出場理由（僅 `Exit` 時非 null） |

---

