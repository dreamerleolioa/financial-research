# financial-research

AI Stock Sentinel 是一套個股研究與投資紀律輔助系統。後端以 Python / FastAPI / LangGraph / SQLAlchemy 建立可回放的確定性資料與分析流程，前端以 React / Vite / Tailwind 呈現新倉分析、持股管理、結案復盤與 Daily Radar 盤後觀察清單。線上產品不呼叫外部 LLM；需要延伸判讀時，可由使用者複製結構化摘要到自己選擇的對話工具。

## 核心分析維度 (Core Analysis Dimensions)

為了達成「理性偵察」的目標，系統將針對每一標的進行三維數據掃描：

| 維度       | 追蹤指標                                  | 系統判讀重點                                        |
| ---------- | ----------------------------------------- | -------------------------------------------------- |
| **技術面** | MA5/20/60、均線斜率、MACD 動能斜率、ATR/布林波動分位、量價與支撐壓力 | 同時判斷位階、趨勢是否加速、波動 regime 與訊號衝突 |
| **籌碼面** | 三大法人買賣超、融資餘額                  | 追蹤聰明錢流向，判斷籌碼是集中於大戶還是分散至散戶 |
| **基本面** | 本益比區間、歷史估值帶                    | 判斷當前股價相對估值位階，識別高估或低估區間       |

> **分析邊界**：技術指標、籌碼、基本面、風險語言與策略 trace 全部由版本化 Python 規則計算。Production 不持有或使用 Anthropic / OpenAI API key。

---

## 需求與架構文件

目前長期系統事實集中在 `docs/specs/`，短期執行計劃不再作為架構事實來源。

- 技術架構需求文件：`docs/specs/ai-stock-sentinel-architecture-spec.md`
- 後端 API 技術規格：`docs/specs/backend-api-technical-spec.md`
- Daily Radar 規格：`docs/specs/daily-stock-radar-spec.md`
- 持股診斷規格：`docs/specs/ai-stock-sentinel-position-diagnosis-spec.md`
- 自動化審核規格：`docs/specs/ai-stock-sentinel-automation-review-spec.md`
- 階段性 roadmap：`docs/specs/ai-stock-sentinel-execution-roadmap-spec.md`
- 規格導覽與維護規則：`docs/specs/README.md`
- 開發執行手冊：`docs/development-execution-playbook.md`
- 後端自學導覽：`docs/backend-self-study-guide.md`

## 目前架構摘要

- `/analyze`：單股新倉研究流程，使用 LangGraph 串接 yfinance、法人籌碼與基本面 provider，由 Python rule-based code 產生技術指標、風險語言、行動 trace 與信心分數，不呼叫外部模型。
- `/analyze/position`：持股診斷流程，重用單股資料抓取與分析基礎，但語意是續抱、減碼、出場風險檢查，不是新倉建議。
- `/watchlist`：個人關注列表，保存尚未進入持股的觀察標的，可從 Analyze 與 Daily Radar 加入，並在列表內單筆或一鍵批次快速查看技術指標與複製摘要；它不代表進場、部位或交易紀錄。
- `/portfolio`：持股、加碼、結案、事件 ledger、進場脈絡、lifecycle plan、single trade review 與 group-level lifecycle review。結案事件可明確保存原因、計畫遵循與信心水準；group lifecycle review v4 以獨立 `outcome`、`process_quality` 與四面向狀態區分交易結果、操作品質與真正資料不足。結案回顧採 closed-only、事件日前 completed data、source fingerprint、版本唯讀保護與短 transaction 並行鎖為契約。Single Trade Review 的 request-scoped provider refresh 具 timeout／容量／TTL／final trading-bar coverage 邊界，重複 refresh 回 `409`／`Retry-After`，外部 I/O 不持有 DB lock 且不寫回正式 `StockRawData`；Lifecycle Review 不另抓 provider，只讀 Daily Radar 已保存的 final `price_history` 與 completed indicators。OHLC 先依各自日期排除事件日，再以共同交易日對齊；full-exit 當日收盤不納入已持有路徑。事後補填或進場後已修改的 plan 不參與歷史違規或決策品質評分，市場行情缺口也不得被描述成使用者未記錄原因。
- `/daily-radar`：盤後觀察雷達，內部 workflow 產生 multi-track universe、刷新試驗版 Daily AVWAP evidence snapshot、歸檔官方未還原市場行情，並以 adjusted selected-symbol OHLCV 執行 deterministic Stage 1/2 scoring；官方 unadjusted archive 不直接取代 adjusted technical history。流程保存 run、candidate、score breakdown、replayable evidence 與 forward validation 結果。
- `phase1_avwap`：試驗版 Daily AVWAP 觀察層，針對 active holdings、watchlist 與 Daily Radar selected candidates 建立日頻 AVWAP snapshot。Snapshot 是全域市場 cache，只保存 market bars / generic anchors / data quality，不保存使用者持股 entry date 或 avg cost；Portfolio risk summary 會在 read projection 時用 portfolio domain 的持股資料計算 holding-specific state。此功能只透過既有 Analyze、Portfolio risk summary、Daily Radar response 顯示，不新增 public endpoint、不改 Daily Radar scoring。
- `shared_background_contexts`：共用背景脈絡 cache，保存 weekly major holders、lending、full margin 等背景資料。`official_first` 模式下，融資融券與借券優先使用 TWSE/TPEX 官方整表資料，只有 dataset 失敗才退回 FinMind；各 consumer 仍只以 read/reference 方式使用。

---

## 目錄結構

```text
backend/
	Makefile
	pyproject.toml
	uv.lock
	scripts/
		backtest_win_rate.py    # 勝率回測 CLI 腳本
		daily_radar_forward_validation.py
		daily_radar_rule_ablation.py
		daily_radar_calibration.py
	src/ai_stock_sentinel/
		analysis/
			confidence_scorer.py
			strategy_generator.py
			position_scorer.py
			metrics.py
			position_lifecycle.py
			trade_review.py
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
			background_context.py
			default_background_context.py
			finmind_background_context.py
			tdcc_background_context.py
			forward_validation.py
			rule_governance.py
			repository.py
			router.py
			universe.py
			institutional_universe_provider.py
			raw_data.py
			data_loader.py
			prefilter.py
			scoring.py
			service.py
		phase1_avwap/
			calculator.py
			provider.py
			projection.py
			repository.py
			service.py
			universe.py
		db/
			models.py               # DailyAnalysisLog / StockRawData / StockAnalysisCache / DailyRadarRun / DailyRadarCandidate
			session.py
		graph/
			builder.py
			nodes.py
			state.py
		portfolio/
			entry_record_contract.py
			fees.py
			risk_summary.py
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
		deploy.yml                         # Backend tests + GitHub Pages frontend deploy
		daily-radar.yml                    # Daily Radar 內部排程
		daily-radar-chip-context.yml       # Shared background context 更新
		analysis-forward-validation.yml    # general analysis 每日驗證
		monthly-analysis-calibration.yml   # 雙軌月度 calibration artifact
		investment-discipline-release-gate.yml
docs/
	backend-self-study-guide.md
	specs/
		README.md
		ai-stock-sentinel-architecture-spec.md
		backend-api-technical-spec.md
		daily-stock-radar-spec.md
		ai-stock-sentinel-automation-review-spec.md
		ai-stock-sentinel-position-diagnosis-spec.md
		ai-stock-sentinel-execution-roadmap-spec.md
	development-execution-playbook.md
```

---

## 部署

Push to `main` 自動觸發：後端跑測試 → 前端 build 並部署到 GitHub Pages。後端正式執行環境由 Zeabur URL 提供給前端與 internal workflows。

Daily Radar 另有 GitHub Actions workflow，可手動執行或於台灣市場交易日收盤後排程執行。正式 workflow 會分段 POST 到 `${ZEABUR_BACKEND_URL}/internal/daily-radar/*`，用 `DAILY_RADAR_INTERNAL_TOKEN` 做內部 API 驗證，且每個 step 都由 workflow 明確帶入 `run_date`。Scheduled run 會用 GitHub Actions run API 讀取該次 workflow run 不變的原始 `created_at`，再回推 `github.event.schedule` 對應的 UTC cron slot；因此 runner 延遲、跨過台灣午夜或對舊 run 按 Re-run 都會保留原本 intended trading date。手動執行可指定 `run_date`，未指定時則固定使用該 workflow run 首次建立當下的台北日期，Re-run 不會改日。一般下游 job 啟動前，workflow 先呼叫 `POST /internal/daily-radar/market-session`，以 TWSE `MI_INDEX` 確認該 `run_date` 是否開市；週末、國定假日或颱風停市會讓 scheduled pipeline 正常 skip，provider 失敗或回應無法判斷則 fail closed，不會被誤當成休市。明確日期範圍的 `refresh-market-bars`、`backfill-institutional-flows` 與唯讀 `replay-institutional-universe` 是手動 maintenance exceptions，不依賴目前 `run_date` 的 `market_open`。台灣時間 17:30 `refresh-institutional-flows` 歸檔 TWSE/TPEX 全市場法人日報；18:00 `prepare-universe` 從 archive 分開建立外資當日、投信當日、外資近期累積、投信近期累積四條軌道並保存 capped 250 selected symbols；18:30 `refresh-market-bars` 寫入 TWSE/TPEX 官方整表 OHLCV；19:00 `refresh-avwap`；20:00 `refresh-lending`；21:30 `refresh-full-margin`；22:30 `refresh-ohlcv`；22:40 `refresh-managed-raw-data` 補齊 active positions 與近期分析標的的 final raw rows；23:00 `refresh-ai-evidence` 針對同日全部 final 支援台股 raw rows 補齊技術、官方法人、融資與官方快取基本面，並記錄各 evidence lane 的剩餘缺漏；23:30 `refresh-market-context`；隔日 00:30 `run-scoring` 仍對同一個 intended trading date 做 scoring，只讀 DB cache/snapshot 並持久化 Daily Radar candidates。`refresh-managed-raw-data` 與 `refresh-ai-evidence` 都不改 prepared selected symbols、universe tracks 或 canonical candidates，也不屬於 scoring required steps；`run-scoring` 要求 `refresh-institutional-flows`、`refresh-lending`、`refresh-full-margin`、`refresh-ohlcv`、`refresh-market-context` 完成。`refresh-avwap` 是 optional evidence step，失敗時 candidate detail 仍保留 `phase1_avwap_context.freshness` 與 `missing_reason`，不阻塞雷達發佈。另有台灣時間週二至週六 07:00 的 AVWAP 補修排程，會補跑前一個 intended trading date 的 `refresh-avwap`，成功後重跑同日 `run-scoring`，讓 public read 透過同日期最新完成 run 看到補齊後版本。

基本面另由 `.github/workflows/fundamental-data.yml` 於台灣時間週一至週五 07:15 刷新官方快照，並以 bounded backfill 接續未完成的 managed/latest AI-pool symbols。TWSE/TPEX 財報與股利以 payload hash 版本化保存；歷史 EPS 缺漏時每批最多 10 檔，先查 MOPS 官方歷史季 EPS，失敗或歷史仍不足才降級 FinMind 財報，股利歷史仍由 FinMind 回補。新 backfill job 可用 `backfill_raw_pool_date` 鎖定已完成 `refresh-ai-evidence` 的指定日期；跨 workflow 續跑只帶回 server-owned `backfill_after_symbol` 與 `backfill_job_id`。

Daily Radar 的 live 資料載入有 request budget：法人 universe 只讀已歸檔的 TWSE `T86` / TPEX `3itrade_hedge` 市場級報表，分成外資／投信當日與近期累積四條軌道，不做逐檔法人 request；舊 `TWT38U` / `TWT44U` 只保留相容用途。Phase 1 AVWAP 上市 `.TW` 使用 TWSE `STOCK_DAY` 逐月 single-symbol query 補齊 lookback window，上櫃 `.TWO` 保留 FinMind `TaiwanStockPrice` fallback，正式 `refresh-avwap` 會合併 selected symbols、active holdings 與 watchlist symbols 後刷新，非 `.TW/.TWO` 會以 `skipped_symbol_reasons.unsupported_phase1_avwap_market` 記錄而不呼叫 provider，但 snapshot 仍只保存 market data，不保存使用者持股成本或進場日；FinMind `TaiwanStockSecuritiesLending`、`TaiwanStockMarginPurchaseShortSale` 分成不同小時刷新，每段都讀同一批 selected symbols，其中 lending / full-margin 會先重用同日 fresh `shared_background_contexts`；yfinance 對 selected universe 中缺少 final raw row，或 final row 缺少必要且為有限數值的 OHLCV / compatibility indicators、canonical `technical_profile`、`price_history`、資料日期的 symbols 做一次 batch download，只有通過 candidate/replay 完整度的既有 `StockRawData` 才會重用，並在 refresh 後回寫 prepared universe 的技術面 tracks；market index 只抓固定 benchmark（TW: `TAIEX` / `^TWII`，US: `SPX` / `^GSPC`）。Portfolio AVWAP read path 可使用 requested date 當日或以前最新 fresh snapshot，但最多回看 7 個 calendar days，超過時回 `phase1_snapshot_stale`。`run-scoring` 不打外部資料源，只讀 `daily_radar_prepared_runs`、`phase1_avwap_snapshots`、`shared_background_contexts`、`stock_raw_data` 與 prepared market context，並在評分前拒絕空 selected universe、再次確認每個 selected symbol 都有完整 raw row；`weekly_major_holders` 維持週頻 GitHub Actions workflow 呼叫 `/internal/daily-radar/chip-context/update` 更新 cache。Phase 2B 起，Daily Radar detail 可顯示 shared background context labels，但 labels 不參與分數或排序。Phase 2C/2D 起，`/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 以 read/reference 方式讀取 shared context；它只作 evidence、caveat 與資料品質 trace，不覆寫 deterministic action、verdict、classification 或 lifecycle replay。

CI/CD 與排程現況以 `.github/workflows/` 與 `docs/specs/ai-stock-sentinel-architecture-spec.md` 的 workflow 地圖為準。

---

## 環境需求

- Python 3.14
- 後端依賴使用 `uv` 管理
- Node.js 22+（CI 使用 Node 22；前端用 pnpm 10）

## 安裝與啟動

```bash
cd backend
uv sync
```

或使用 Makefile：

```bash
cd backend
make install
```

## 環境變數

### 本機開發（backend/.env）

```bash
FINMIND_API_TOKEN="your-finmind-api-token"
FINMIND_MAX_CONCURRENT_REQUESTS="8"
FINMIND_HOLDING_SHARES_PER_ENABLED="false"
CORS_ORIGINS="http://localhost:5173,https://<username>.github.io"
GOOGLE_CLIENT_ID="your_google_client_id"    # Google OAuth 登入用
GOOGLE_CLIENT_SECRET="your_google_client_secret"
GOOGLE_OAUTH_REDIRECT_URIS="http://localhost:5173/login/callback,https://<username>.github.io/<repo>/login/callback"
JWT_SECRET="your_jwt_secret"
DATABASE_URL="postgresql://..."             # 本機可用 SQLite
DAILY_RADAR_INTERNAL_TOKEN="..."            # Daily Radar 內部執行 API 用
DAILY_RADAR_BACKGROUND_PROVIDER_MODE="finmind_only" # finmind_only | official_first | official_only
DAILY_RADAR_TW_OHLCV_PROVIDER_MODE="yfinance_only"  # yfinance_only | official_first | official_only
FUNDAMENTAL_PROVIDER_MODE="finmind_only"             # finmind_only | official_cache_first | official_cache_only
```

> `.env` 不進版控。換電腦時複製 `backend/.env.example` 建立：`cp backend/.env.example backend/.env`

### CI/CD（GitHub Actions）

| 類型     | 名稱                         | 用途                                              |
| -------- | ---------------------------- | ------------------------------------------------- |
| Secret   | `ZEABUR_BACKEND_URL`         | Daily Radar workflow 呼叫的 Zeabur 後端 URL       |
| Secret   | `DAILY_RADAR_API_BASE_URL`   | 一般分析 forward validation 與月度 calibration workflow 呼叫的後端 URL |
| Secret   | `DAILY_RADAR_INTERNAL_TOKEN` | Daily Radar workflow 呼叫內部 API 的 Bearer token |
| Secret   | `CALIBRATION_REPORT_PASSPHRASE` | 月度 production calibration artifact 的 AES-256 對稱加密密碼 |
| Variable | `VITE_API_URL`               | 前端 build 時注入後端 URL                         |
| Variable | `VITE_GOOGLE_CLIENT_ID`      | 前端 Google OAuth client ID                       |

### 生產環境（Zeabur Environment Variables）

| 名稱                | 值                                                   |
| ------------------- | ---------------------------------------------------- |
| `FINMIND_API_TOKEN` | FinMind 使用者頁取得的 API token                     |
| `FINMIND_MAX_CONCURRENT_REQUESTS` | 單一 backend process 共用的 FinMind HTTP 並行上限，預設 `8` |
| `FINMIND_HOLDING_SHARES_PER_ENABLED` | 是否啟用 sponsor 限定的持股分級資料，預設 `false` |
| `CORS_ORIGINS`      | `http://localhost:5173,https://<username>.github.io` |
| `GOOGLE_CLIENT_ID`  | Google OAuth client ID                               |
| `GOOGLE_CLIENT_SECRET` | Google OAuth code flow client secret              |
| `GOOGLE_OAUTH_REDIRECT_URIS` | 逗號分隔的 Google OAuth callback 精確 allowlist；應包含 GitHub Pages base path |
| `JWT_SECRET`        | JWT 簽名密鑰                                         |
| `DATABASE_URL`      | PostgreSQL 連線字串                                  |
| `DAILY_RADAR_INTERNAL_TOKEN` | 與 GitHub Actions secret 同一組 token |
| `DAILY_RADAR_BACKGROUND_PROVIDER_MODE` | 籌碼背景來源模式；部署初始值 `finmind_only` |
| `DAILY_RADAR_TW_OHLCV_PROVIDER_MODE` | 台股日線來源模式；部署初始值 `yfinance_only` |
| `FUNDAMENTAL_PROVIDER_MODE` | 基本面來源模式；部署初始值 `finmind_only` |

#### Data migration 部署門檻

`1b2c3d4e5f6a_repair_synthetic_split_ledger_quantity` 會修正既有 portfolio/event facts；`2c3d4e5f6a7b_align_calibration_sample_identity` 會鎖定並 canonicalize calibration samples/results。這一版不可讓任何舊版 backend、`/analyze` capture、forward-validation 或 portfolio writer 與 migration 做 rolling overlap。

部署時必須依序執行：

1. 進入 maintenance mode 並停止所有舊版 backend instances；只封鎖 portfolio writes 不足以保護 calibration migration。
2. 建立可還原的 migration 前資料庫備份，先盤點 calibration tables 的 row count、duplicate identity 數量與可接受維護窗口，確認後暫時設定 `CALIBRATION_MIGRATION_BACKUP_CONFIRMED=2c3d4e5f6a7b`。`2c3d4e5f6a7b` 會刪除非 canonical 的歷史 calibration duplicates，缺少此精確確認值時 migration 會中止；它也刻意禁止 Alembic downgrade，需要回退時必須還原備份。
3. 部署新版；`backend/zbpack.json` 的 production start command 會先執行 `uv run alembic upgrade head`，再以 `uv run alembic current --check-heads` 確認目前 DB 已套用所有 head。Calibration migration 的 exclusive lock 最多等待 10 秒，整個 migration statement 最多執行 5 分鐘，任一逾時都會 fail closed；應先找出並結束阻塞 transaction 或重新評估資料規模，再重新部署，不得移除 timeout 或繞過 migration。任一步驟失敗都不得啟動 Uvicorn；本次輸出應為 `2c3d4e5f6a7b (head)`。確認 migration 完成後即可移除一次性 confirmation variable，後續啟動不會再次執行已套用的 revision。
4. 啟動新版 backend，確認所有舊版 instances 已退出後，再重新開放 API traffic 與背景 calibration workflows。

Portfolio migration 的 compare-and-lock 與 calibration migration 的 exclusive table lock 只保護各自 DB transaction 內的競態，不能取代上述跨版本 write quiescence。

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
- 有方向的綜合訊號強度與資料品質提示（含 `cross_validation_note`；`confidence_score` 以 50 為中性基準，badge 分為 `>= 80` 強烈偏多、`60–79` 偏多、`41–59` 中性／混合、`21–40` 偏空、`<= 20` 強烈偏空，並以 `x / 100` 呈現，不借用 `action_plan.conviction_level`，也不使用百分比暗示勝率；`data_confidence < 60` 時仍顯示資料不足百分比）
- 快照資訊（symbol / current_price / volume）
- 技術面、籌碼面與基本面資料卡，以及 rule-based 綜合判讀
- 戰術行動 Action Plan（策略方向 / 入場區間 / 停損 / 持股期間；含 `action_plan_tag` 燈號 badge：🟢 機會 / 🔴 過熱 / 🔵 中性）
- 分析結果可加入關注列表，作為後續觀察標的，不寫入持股紀錄
- 錯誤 banner + loading 狀態
- 底層保留 `GET /history/{symbol}`、`historyApi.ts` 與 `ConfidenceChart.tsx`，供後續嵌入個股歷史分析趨勢

**關注列表（`/watchlist`）**

- 保存目前登入使用者有興趣但尚未進入持股的股票
- 支援新增 / 移除股票、編輯單筆觀察備註，以及拖拉調整列表順序
- 支援在列表內展開 raw 技術指標快查，透過 `POST /analyze` 搭配 `persist_result: false` 取得 deterministic 指標且不寫入分析紀錄；可單筆查詢，也可一鍵批次補查尚未載入的關注標的
- 技術快查面板可複製完整指標摘要，方便帶到其他 AI agent 做深度分析
- Analyze 結果與 Daily Radar 候選標的都可加入關注列表
- 與 `/portfolio` 分離，不代表進場、部位、加碼或交易紀錄

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
- 完整交易復盤顯示在固定視窗中，標題、交易識別與上一筆／下一筆控制不隨內容捲動；只有復盤內容區捲動，關閉後焦點回到目前交易的觸發按鈕
- Trade Review 可建立 request-scoped provider snapshot；Lifecycle Review 不另行抓取 provider，而是唯讀使用 Daily Radar 每日盤後保存的 final `StockRawData`。事件日前指標先使用 completed `recent_*`，不足時回退到有日期的 `technical.price_history`，仍不足才使用資料日早於事件日的 persisted `technical.indicators`
- Trade Review 與 Lifecycle Review 的持久化 market evidence 會依交易日 compact；所有 dated trailing series 先於 outer bars 合併，同日重疊歷史以較新非空值更新並保留缺少欄位，partial outer bar 只能補缺。`price_history` 與可作 fallback 的 MA20、MA60、RSI14、量比也納入 evidence 與 source fingerprint，避免畫面可用來源和保存來源不一致
- 市場行情缺口只讓受影響的進場、部位管理或風險出場面向顯示證據不足，不得把已記錄的操作原因誤判為紀錄品質不足；資料品質欄位會顯示事件類型、事件日期與缺少的指標

**Daily Radar（`/daily-radar`）**

- 每日觀察候選清單
- bucket、觀察等級、風險標籤與規則命中原因；`observation_score` 只作內部排序、校準與 advanced trace，不代表勝率或交易建議
- candidate trace 包含 market regime、relative strength 或缺資料原因、scoring/rule version、score breakdown、data dates、replayable evidence、shared background context cache trace 與 background context labels
- 候選標的可加入關注列表；此動作只保存觀察標的，不影響 Daily Radar scoring、ranking 或 forward validation

**登入（`/login`）**

- Google OAuth authorization-code 登入流程；redirect 前會保存一次性 `state`，callback 必須比對並消耗後才可交換 code
- Backend 只接受 `GOOGLE_OAUTH_REDIRECT_URIS` 的精確 callback；未設定時才 fallback 到 `CORS_ORIGINS` 中可信 origin 的 `/login/callback` 路徑

### FastAPI 服務

```bash
cd backend
make run-api
```

預設開啟：`http://127.0.0.1:8000`

所有 API 啟動入口都會先執行 Alembic upgrade；migration 缺少人工確認、逾時或執行失敗時必須直接中止啟動，不得只記錄錯誤後在舊 schema 上提供服務。資料庫已位於 head 時不需要保留一次性的 migration confirmation variable。

- `GET /health`
- `POST /analyze` — 新倉策略分析
- `POST /analyze/position` — 持股操作建議
- `POST /internal/daily-radar/market-session`：以 TWSE `MI_INDEX` 判斷指定 `run_date` 是開市或休市，供正式 workflow 在 scheduled pipeline 與一般手動 step 前做 fail-closed guard；三個明確歷史日期的 maintenance steps 不依賴當日 `market_open`，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/refresh-institutional-flows`：歸檔指定交易日 TWSE/TPEX 全市場法人日報；`prepare-universe` 只讀完整 archive，分開建立外資／投信當日與近期累積四條軌道，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/backfill-institutional-flows`：以最多 11 個含首尾 calendar days 的明確範圍回補法人 archive，重用完整日期並修復損壞 snapshot，拒絕未來日期，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/institutional-universe-replay`：唯讀比較四條分法人軌道與 archive-combined legacy proxy；至少 5 個完整市場日才標記可供人工審閱，永不自動修改 live universe 或 scoring，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/prepare-universe`：保存當日 selected universe，正式排程 capped 250 symbols，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/refresh-market-bars`：以 TWSE/TPEX 官方整表行情刷新 unadjusted `taiwan_daily_bars`，支援最多 180 個 calendar days 的 bounded backfill；此 archive 供 AVWAP 與基本面季末價格使用，不直接取代 adjusted technical history，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/refresh-avwap` / `refresh-lending` / `refresh-full-margin` / `refresh-ohlcv` / `refresh-managed-raw-data` / `refresh-ai-evidence` / `refresh-market-context`：分段刷新 Daily Radar 與 managed symbols 所需資料 cache；`refresh-managed-raw-data` 以持倉優先補齊近期使用標的，只輸出聚合計數且不阻擋 scoring；`refresh-ai-evidence` 只補同日完整 AI raw pool 並輸出缺漏審計，不改 scoring membership，皆需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/fundamentals/refresh`：刷新 TWSE/TPEX 官方財報與股利版本庫，允許 dataset-level partial success，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/fundamentals/backfill`：對 managed/specified symbols 做歷史基本面回填；EPS 依序使用 MOPS 官方歷史季資料與 FinMind fallback，股利由 FinMind 回補。每次最多 10 檔；GitHub workflow 每次最多六批，未完成時須用回傳 cursor 續跑，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/run-scoring`：只讀已準備資料並持久化 Daily Radar run/candidates；會要求 lending、full-margin、OHLCV、market context refresh step 完成，AVWAP 缺漏只保留為 optional evidence caveat，需 `DAILY_RADAR_INTERNAL_TOKEN`
- `POST /internal/daily-radar/run`：保留一鍵手動相容入口；正式排程使用上述分段 workflow
- `POST /internal/daily-radar/chip-context/update`：更新 shared background context cache，背景資料包含 weekly major holders、lending 與 full margin
- `POST /internal/daily-radar/forward-validation/run`：執行 Daily Radar 成熟候選 forward validation，寫入可回放 validation result
- `POST /internal/analysis-calibration/forward-validation/run`：執行 final `/analyze` 樣本的成熟 5 / 10 / 20 日驗證
- `POST /internal/daily-radar/rule-review/monthly`：產生 Daily Radar 六個成熟月份 training / holdout 調權報表
- `POST /internal/analysis-calibration/monthly`：產生一般分析 confidence 六個成熟月份 training / holdout 調權報表

Daily Radar due validation 已接在 `.github/workflows/daily-radar.yml` 的 OHLCV／market context 後；一般分析由 `.github/workflows/analysis-forward-validation.yml` 每日執行，月報則由 `.github/workflows/monthly-analysis-calibration.yml` 每月執行。一般分析第一版 calibration 只收 `.TW`／`.TWO` 的 final `/analyze` 樣本，統一使用 TW／TAIEX，其他市場分析不寫入台股校準 cohort。

兩軌共用 feature-neutral `ai_stock_sentinel.calibration.forward_validation` 處理交易窗口、價格正規化、benchmark 完整性與 outcome 計算，各自只提供 feature adapter；月報先以 DB aggregation 選出最近六個 5／10／20 日皆成熟的月份，optimizer 只載入所選月份的 replay / validation 明細，Daily Radar 的當月 rule diagnostics 另以單月 bounded query 載入。自動修改資格要求每個窗口都有足夠 distinct signal／candidate、training 至少 20 個日期 block、holdout 至少 5 個 blocks，且整體與每個入選月份的逐窗口 validated coverage、replay coverage 均達 90%；Daily Radar 涉及排名或 counterfactual 的治理另要求每個交易日／窗口 replay ranking pool 100% 完整。每個 horizon 另有獨立 holdout 非劣性 gate。一般分析與 Daily Radar 都會在 scoring／bootstrap 前檢查整批 replay workload，超限時 fail closed；一般分析只採目前 strategy/config version，且資料庫以 strategy/config version 鎖定同一 market／symbol／日期唯一的 point-in-time sample；validation 的 `signal_date` 與 `benchmark_symbol` 必須和該 sample 完全一致，否則不得計入 watermark 或 optimizer。Daily Radar 缺少 validation result 時明確標記 missing，先決定 Top 20 再接 outcome，並只對 live-score 規則執行同輸入 counterfactual replay；context-only 群組標記為不適用。兩軌報告都只提出建議，不直接變更 live scoring。

Final `/analyze` cache 會保存去識別化的精簡 replay payload；若首次 calibration capture 暫時失敗，後續 final cache hit 會以同一 payload 冪等補寫。舊 cache 沒有正式 replay payload 時維持跳過，不會從輸出猜測輸入。

月報只上傳 AES-256 加密的 Actions artifact，內含兩軌 JSON、Markdown Actions 與 manifest；不會寫入 public issue 或 main branch，也不會直接修改權重。Artifact 只保留 30 天，因此每月產生後需在到期前下載並自行保存；可每六個成熟月份再提交給 Codex 做一次人工調權審查。下載後用 `gpg --decrypt --output analysis-calibration.tar.gz <artifact>.tar.gz.gpg` 解密，再以 `tar -xzf analysis-calibration.tar.gz` 展開。
- `GET /daily-radar/latest`：讀取最新 Daily Radar 候選清單
- `GET /daily-radar/{run_date}`：讀取指定日期 Daily Radar 候選清單
- `GET /daily-radar/symbol/{symbol}`：讀取指定標的 Daily Radar 歷史
- `GET /history/{symbol}` — 查詢歷史分析記錄
- `GET/POST /auth/*` — Google OAuth 登入流程
- `GET /watchlist` — 列出目前登入使用者的關注股票清單
- `POST /watchlist` — 新增關注股票；同一使用者同一 symbol 具冪等語義，已存在時回傳既有項目
- `PUT /watchlist/{item_id}` — 更新關注項目的觀察備註
- `PUT /watchlist/reorder` — 以完整 item id 清單調整目前登入使用者的關注列表順序
- `DELETE /watchlist/{item_id}` — 移除關注項目
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
| `analysis`                 | 已停用的舊 LLM 相容欄位；固定為空字串                                                                                                                                                              |
| `analysis_detail`          | 已停用的舊 LLM 相容欄位；固定為 `null`                                                                                                                                                             |
| `technical_indicators`     | 技術指標顯性輸出：既有均線/量價/通道指標，加上 MA20 5 日斜率、MA60 10 日斜率、MACD 柱體 3 日斜率、ATR%/布林帶寬 60 日分位、波動 regime 與訊號衝突；新增欄位目前只作 evidence，不改分數                                                                               |
| `sentiment_label`          | 已停用的舊消息面相容欄位；固定為 `null`                                                                                                                                                            |
| `institutional_flow_label` | 籌碼面標籤（`institutional_accumulation` / `distribution` / `retail_chasing` / `neutral`）                                                                                                         |
| `cleaned_news`             | 已停用的舊新聞相容欄位；固定為 `null`                                                                                                                                                              |
| `cleaned_news_quality`     | 已停用的舊新聞品質相容欄位；固定為 `null`                                                                                                                                                          |
| `news_display_items`       | 已停用的舊新聞列表相容欄位；固定為空陣列                                                                                                                                                           |
| `action_plan_tag`          | 綜合行動燈號（`opportunity` / `overheated` / `neutral`；rule-based 計算，任一輸入為 null 時降級回 `neutral`）                                                                                      |
| `confidence_score`         | 有方向的訊號強度 0–100（`signal_confidence` 別名；50 中性、低於 50 偏空、高於 50 偏多）                                                                                                             |
| `signal_confidence`        | 有方向的訊號強度分數（多維加權計算；不代表勝率或不分方向的一致性）                                                                                                                                 |
| `data_confidence`          | 資料完整度分數（0 / 50 / 100，依技術面與籌碼面兩個啟用維度是否成功取得計算）                                                                                                                       |
| `cross_validation_note`    | 技術面與籌碼面的交叉驗證備注（rule-based 固定字串）                                                                                                                                                |
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
