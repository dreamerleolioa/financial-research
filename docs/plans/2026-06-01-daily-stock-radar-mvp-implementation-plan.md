# Daily Stock Radar MVP 實作計劃

> status: planned  
> date: 2026-06-01  
> spec reference: `docs/specs/daily-stock-radar-spec.md`

## Goal

建立 Daily Stock Radar MVP，讓系統在台股收盤且資料源更新後，由 GitHub Actions 呼叫 Zeabur 後端內部端點，產生隔日觀察清單。MVP 必須用 deterministic rule-based pipeline 選出候選，保存可追溯分數與規則依據，並在 React 前端提供每日雷達頁、bucket 篩選與細節抽屜。

本計劃採 tests-first。每個 phase 先寫失敗測試或 fixture 契約，再實作最小可通過版本，最後跑相關測試與手動驗收。

## Guardrails

1. 全部前端文案、API 欄位語意與解釋文字只能使用「觀察、追蹤、留意、風險」語言，不輸出買進、賣出、加碼、出場建議、目標價或勝率承諾。
2. Daily Radar 不使用 AI 或 LLM 做股票選擇、排名、bucket 判定或風險扣分。解釋文字由 rule-based templates 產生。
3. Daily Radar 不把 `TaiwanStockHoldingSharesPer`、stockholder distribution 或其他週頻大戶持股資料放進每日排名。
4. 生產部署目標是 Zeabur。排程文件與 secrets 命名不得假設其他部署平台。
5. 前置濾網是 hard gate。低流動性、資料缺漏、過熱、弱勢結構、融資擁擠或 stale data 不可用高分補償。
6. 同一 `run_date` 重跑必須有明確 idempotency 策略，MVP 採「同日 latest 覆寫候選，保留 run log 狀態」作為預設。
7. 外部 API 成本要受控。Stage 1 只讀本地快取或低成本資料，Stage 2 才對候選補齊詳細資料。

## Architecture Touchpoints

| 面向 | 實作位置 | 目的 |
| --- | --- | --- |
| Backend FastAPI | `backend/src/ai_stock_sentinel/main.py`, `backend/src/ai_stock_sentinel/daily_radar/router.py` | 掛載 internal trigger endpoint 與 public read API |
| DB models and migration | `backend/src/ai_stock_sentinel/db/models.py`, `backend/alembic/versions/*_add_daily_radar_tables.py` | 新增 `daily_radar_runs` 與 `daily_radar_candidates` |
| Data sources | `backend/src/ai_stock_sentinel/daily_radar/data_loader.py`, `backend/src/ai_stock_sentinel/data_sources/*` | 讀取 OHLCV、法人、融資、大盤與已快取原始資料 |
| Scoring service | `backend/src/ai_stock_sentinel/daily_radar/prefilter.py`, `scoring.py`, `explanations.py`, `cooldown.py`, `service.py` | 前置濾網、bucket 評分、風險扣分、解釋與冷卻處理 |
| Internal trigger endpoint | `POST /internal/daily-radar/run` | GitHub Actions 使用 shared token 觸發每日掃描 |
| Public read API | `GET /daily-radar/latest`, `GET /daily-radar/{run_date}`, `GET /daily-radar/symbol/{symbol}` | 前端查詢最新、指定日期與單股歷史 |
| Frontend React page | `frontend/src/pages/DailyRadarPage.tsx`, `frontend/src/lib/dailyRadarApi.ts`, `frontend/src/App.tsx` | 每日雷達頁、bucket tabs、列表與細節抽屜 |
| GitHub Actions schedule to Zeabur | `.github/workflows/daily-radar.yml` | 收盤後只呼叫 Zeabur 的 `POST /internal/daily-radar/run`，資料載入與必要補齊由 Daily Radar service 內部負責 |

## TDD Strategy

1. 先定義 fixtures：建立固定 OHLCV、法人、融資、大盤、歷史候選資料，確保四個 buckets 各至少一筆可命中。
2. 先寫後端 unit tests：前置濾網、bucket scoring、risk penalties、cooldown、explanation templates 都要可在無外部網路下測試。
3. 先寫 persistence tests：run 建立、candidate 寫入、同日重跑、依日期查詢、依 symbol 查詢，全部用測試 DB 或現有 test session fixture。
4. 先寫 API tests：internal endpoint auth、成功 run、stale data、public read endpoints，mock service 層避免測試打外部 API。
5. 先寫前端狀態測試：loading、empty、error、stale、completed、bucket 篩選、detail drawer、文案禁詞。
6. 實作只做到測試所要求的最小功能。每個 phase 結束後再補 integration 或手動驗收。

## Test Matrix

| 層級 | 測試檔案 | 覆蓋內容 | 驗收指令 |
| --- | --- | --- | --- |
| Backend unit | `backend/tests/test_daily_radar_prefilter.py` | 流動性、股價、資料完整度、過熱、弱勢、融資、stale hard gate | `cd backend && make test` |
| Backend unit | `backend/tests/test_daily_radar_scoring.py` | 四個 bucket 分數、primary bucket、secondary buckets、risk penalties | `cd backend && make test` |
| Backend unit | `backend/tests/test_daily_radar_explanations.py` | rule-based 摘要、證據點、風險標籤、禁用交易命令文案 | `cd backend && make test` |
| Backend unit | `backend/tests/test_daily_radar_cooldown.py` | `new`, `repeat`, `upgraded`, `cooled_down` 判定 | `cd backend && make test` |
| Backend persistence | `backend/tests/test_daily_radar_repository.py` | run log、candidate 寫入、同日重跑、日期與 symbol 查詢 | `cd backend && make test` |
| Backend API | `backend/tests/test_daily_radar_api.py` | shared token auth、internal run、latest、date、symbol endpoints | `cd backend && make test` |
| Frontend unit | `frontend/src/pages/DailyRadarPage.test.tsx` | loading、empty、error、stale、completed、bucket tabs、detail drawer | `cd frontend && pnpm test` if available |
| Frontend build | existing frontend scripts | 型別、route、API client import、production build | `cd frontend && pnpm build` |
| Schedule | `.github/workflows/daily-radar.yml` | cron、Zeabur URL、internal token secrets、單一 `POST /internal/daily-radar/run` 呼叫 | GitHub Actions dry run or workflow syntax check if available |

## Phase 0: 契約與測試資料先行

### Task 0.1: 建立後端 Daily Radar fixture 契約

**Files likely touched:**

* Create: `backend/tests/fixtures/daily_radar/ohlcv.json`
* Create: `backend/tests/fixtures/daily_radar/institutional_flow.json`
* Create: `backend/tests/fixtures/daily_radar/margin.json`
* Create: `backend/tests/fixtures/daily_radar/market_context.json`
* Create: `backend/tests/fixtures/daily_radar/history_candidates.json`

**Tests first:**

1. 新增 fixture loader 測試到 `backend/tests/test_daily_radar_scoring.py`。
2. 測試固定 fixture 能產生四種 bucket 的可預期輸入：`institutional_accumulation`、`price_volume_strengthening`、`bottoming_reversal`、`support_retest`。

**Acceptance criteria:**

1. Fixtures 不需要網路即可載入。
2. 每筆 fixture 都有 `symbol`、`record_date`、OHLCV、法人、融資、技術指標與 data date。
3. 測試資料明確包含 stale data、data gap、overextended、margin crowding case。

### Task 0.2: 定義 API response schema 測試

**Files likely touched:**

* Create: `backend/tests/test_daily_radar_api_contract.py`
* Create: `backend/src/ai_stock_sentinel/daily_radar/schemas.py`

**Tests first:**

1. 測試 `DailyRadarRunResponse` 必含 `run_date`、`status`、`data_dates`、`market_context`、`candidates`。
2. 測試 candidate 必含 `symbol`、`name`、`primary_bucket`、`secondary_buckets`、`observation_score`、`risk_labels`、`repeat_status`、`explanation`、`score_breakdown`、`matched_rules`。
3. 測試欄位命名使用 observation 語意，不出現 recommendation 或 buy/sell 語意。

**Acceptance criteria:**

1. Schema 可被 FastAPI response model 使用。
2. JSON example 與 `docs/specs/daily-stock-radar-spec.md` 第 10.3 節一致。

## Phase 1: DB Schema 與 Migration

### Task 1.1: 新增 ORM model 測試

**Files likely touched:**

* Modify: `backend/tests/test_db_models.py`
* Modify: `backend/src/ai_stock_sentinel/db/models.py`

**Tests first:**

1. 測試 `DailyRadarRun` table name 為 `daily_radar_runs`。
2. 測試 `DailyRadarCandidate` table name 為 `daily_radar_candidates`。
3. 測試 `DailyRadarCandidate` 與 `DailyRadarRun` 以 `run_id` 關聯。
4. 測試 JSON 欄位可保存 `bucket_scores`、`risk_labels`、`matched_rules`、`score_breakdown`、`input_snapshot`、`data_dates`。

**Implementation notes:**

1. `daily_radar_runs` 欄位：`id`、`run_date`、`market`、`status`、`started_at`、`finished_at`、`universe_count`、`prefilter_count`、`candidate_count`、`errors`、`created_at`。
2. `daily_radar_candidates` 欄位：`id`、`run_id`、`symbol`、`name`、`primary_bucket`、`secondary_buckets`、`observation_score`、`bucket_scores`、`risk_labels`、`matched_rules`、`explanation`、`repeat_status`、`score_breakdown`、`input_snapshot`、`data_dates`、`created_at`。
3. Indexes：`run_date`、`symbol`、`primary_bucket`、`observation_score`，並對 `(run_id, symbol)` 加 unique constraint。

**Acceptance criteria:**

1. ORM import 不破壞既有 tests。
2. 同一 run 不可有重複 symbol candidate。
3. 查詢需求支援依日期、依 symbol、依 bucket 查詢。

### Task 1.2: 新增 Alembic migration 測試與 migration

**Files likely touched:**

* Create: `backend/alembic/versions/*_add_daily_radar_tables.py`
* Create: `backend/tests/test_daily_radar_repository.py`

**Tests first:**

1. Repository 測試先描述資料表行為，包含建立 run、寫入 candidates、查 latest completed run。
2. 測試同日重跑策略：同一 `run_date` 建立新 run log 後，public latest 只讀最新 completed run 的 candidates。

**Acceptance criteria:**

1. Migration 可建立兩張新表與必要 indexes。
2. Repository tests 可在測試 DB 通過。
3. Migration 不修改既有表欄位語意。

## Phase 2: Backend Scoring Service

### Task 2.1: 建立 Daily Radar module skeleton 與 types

**Files likely touched:**

* Create: `backend/src/ai_stock_sentinel/daily_radar/__init__.py`
* Create: `backend/src/ai_stock_sentinel/daily_radar/types.py`
* Create: `backend/src/ai_stock_sentinel/daily_radar/constants.py`
* Create: `backend/tests/test_daily_radar_types.py`

**Tests first:**

1. 測試四個 bucket constants 與 spec 名稱一致。
2. 測試 risk labels 只允許 `overextended`、`flow_conflict`、`margin_crowding`、`market_weakness`、`data_gap`。
3. 測試 repeat status 只允許 `new`、`repeat`、`upgraded`、`cooled_down`。

**Acceptance criteria:**

1. 後續 scoring、API、frontend types 都可共用同一組 enum 語意。
2. 不新增 buy/sell recommendation enum。

### Task 2.2: 實作 data loader 與 Stage 1 prefilter

**Files likely touched:**

* Create: `backend/src/ai_stock_sentinel/daily_radar/data_loader.py`
* Create: `backend/src/ai_stock_sentinel/daily_radar/prefilter.py`
* Create: `backend/tests/test_daily_radar_prefilter.py`

**Tests first:**

1. 低於 20 日平均成交金額門檻的 symbol 回傳 `prefilter_status = rejected` 與原因。
2. 收盤價低於最低股價門檻的 symbol 被排除。
3. 近 60 個交易日 OHLCV 缺漏過多時被排除。
4. 短期乖離、RSI、布林位置過熱時被排除或降級，依 spec hard gate 規則記錄原因。
5. 長期弱勢結構與融資擁擠被排除。
6. 核心資料日期落後容忍值時標示 stale，不進入高分 ranking。

**Implementation notes:**

1. Stage 1 以 `stock_raw_data` 或既有快取為主，不對全市場逐檔打昂貴外部 API。
2. Universe 先支援靜態 symbol list 或既有資料源，排除 ETF、權證、特別股與資料不完整標的。
3. Prefilter 回傳 `prefilter_status`、`prefilter_reasons`、`data_dates`。

**Acceptance criteria:**

1. Prefilter 對固定 fixture 結果穩定。
2. 每個 rejected case 都有中文原因碼與除錯資訊。
3. Stage 1 預設輸出 Top 100 或 Top 200 候選，門檻可由設定調整。

### Task 2.3: 實作 bucket scoring 與 composite observation score

**Files likely touched:**

* Create: `backend/src/ai_stock_sentinel/daily_radar/scoring.py`
* Modify: `backend/tests/test_daily_radar_scoring.py`

**Tests first:**

1. `institutional_accumulation`：法人連續買超、投信或外資方向一致、價格未過熱時加分。
2. `price_volume_strengthening`：成交量高於均量、收盤突破整理區、OBV 或 MFI 同步轉強時加分。
3. `bottoming_reversal`：低點不再破、MACD 改善、KD 低位翻正時加分。
4. `support_retest`：回測 MA20、MA60、前高或區間支撐後止跌時加分。
5. Risk penalties：過熱、籌碼分歧、融資擁擠、大盤弱勢、資料不足都要降低分數並保留原因。
6. Primary bucket 取 bucket score 最高者，secondary buckets 保留其他命中 bucket。

**Acceptance criteria:**

1. `observation_score` 介於 0 到 100。
2. `score_breakdown` 清楚列出 bucket 分、cross confirmation、market context、freshness、risk penalties。
3. 分數用途只作排序，欄位與文案不得稱為推薦分數或勝率。

### Task 2.4: 實作 rule-based explanations

**Files likely touched:**

* Create: `backend/src/ai_stock_sentinel/daily_radar/explanations.py`
* Create: `backend/tests/test_daily_radar_explanations.py`

**Tests first:**

1. 每筆候選至少產生一句 setup 摘要。
2. 每筆候選有三到五個 evidence points。
3. 每筆候選有一到三個 risk labels。
4. 每筆候選有隔日觀察重點。
5. Copy guard 測試禁止「買進」、「賣出」、「加碼」、「出場」、「必買」、「目標價」、「推薦」。

**Acceptance criteria:**

1. 解釋完全由模板與 matched rules 組成。
2. 同一 fixture 重跑產生相同 explanation。
3. 文案符合觀察清單定位。

### Task 2.5: 實作 cooldown handling

**Files likely touched:**

* Create: `backend/src/ai_stock_sentinel/daily_radar/cooldown.py`
* Create: `backend/tests/test_daily_radar_cooldown.py`

**Tests first:**

1. 近 N 個交易日未入選，今日命中時為 `new`。
2. 連續入選且分數與 bucket 沒明顯變化時為 `repeat`。
3. 分數提高或新增更強 bucket 時為 `upgraded`。
4. 近期入選後訊號消退時為 `cooled_down`，預設不進主要列表。

**Acceptance criteria:**

1. Cooldown 只依歷史 candidates 與今日分數判定，不呼叫 LLM。
2. 前端可用 `repeat_status` 顯示「首次觀察」、「連續觀察」或「訊號升級」。

### Task 2.6: 實作 repository 與 orchestration service

**Files likely touched:**

* Create: `backend/src/ai_stock_sentinel/daily_radar/repository.py`
* Create: `backend/src/ai_stock_sentinel/daily_radar/service.py`
* Modify: `backend/tests/test_daily_radar_repository.py`
* Create: `backend/tests/test_daily_radar_service.py`

**Tests first:**

1. Service 建立 run 時先寫入 `status = running`。
2. 成功完成後寫入 candidates，更新 `status = completed`、counts、`finished_at`。
3. 資料過舊時 run status 為 `stale_data`，不產生高分候選。
4. 部分資料缺漏時 candidate 保留 `data_gap` risk label 與 `data_dates`。
5. 單一 symbol 失敗不讓整個 run 無聲失敗，errors 有摘要。

**Acceptance criteria:**

1. `run_daily_radar(run_date, market)` 可端到端產生可查詢結果。
2. Public read 只讀 completed 或 stale_data run，不讀 running run。
3. Run log 保存 universe count、prefilter count、candidate count、errors。

## Phase 3: FastAPI Internal Endpoint 與 Public Read API

### Task 3.1: 加入 shared token internal auth

**Files likely touched:**

* Modify: `backend/src/ai_stock_sentinel/config.py`
* Create: `backend/src/ai_stock_sentinel/daily_radar/auth.py`
* Create: `backend/tests/test_daily_radar_api.py`

**Tests first:**

1. 沒有 `Authorization` 或 `X-Internal-Token` 時，`POST /internal/daily-radar/run` 回 401 或 403。
2. Token 錯誤時拒絕。
3. Token 正確時呼叫 service。

**Implementation notes:**

1. 環境變數建議：`DAILY_RADAR_INTERNAL_TOKEN`。
2. GitHub Actions secret 與 Zeabur environment variable 使用同一 token。
3. Token 不寫入 repo、不寫入 workflow 明文。

**Acceptance criteria:**

1. Internal endpoint 不能公開無驗證觸發。
2. Public read endpoints 不需要 internal token。

### Task 3.2: 實作 `POST /internal/daily-radar/run`

**Files likely touched:**

* Create: `backend/src/ai_stock_sentinel/daily_radar/router.py`
* Modify: `backend/src/ai_stock_sentinel/main.py` or `backend/src/ai_stock_sentinel/api.py`
* Modify: `backend/tests/test_daily_radar_api.py`

**Tests first:**

1. Request body 可接受 optional `run_date` 與 `market`。
2. 未給 `run_date` 時使用後端交易日判定或當日日期，並保留在 response。
3. Service 成功時 response 回傳 run summary。
4. Service 回報 stale data 時 response 不偽裝為 completed。

**Acceptance criteria:**

1. Endpoint 可被 GitHub Actions 呼叫。
2. Response 可讓 workflow log 看出 run id、status、counts、errors。

### Task 3.3: 實作 public read API

**Files likely touched:**

* Modify: `backend/src/ai_stock_sentinel/daily_radar/router.py`
* Modify: `backend/tests/test_daily_radar_api.py`

**Tests first:**

1. `GET /daily-radar/latest` 回最新 completed run 與 candidates。
2. `GET /daily-radar/{run_date}` 回指定日期 latest completed run。
3. `GET /daily-radar/symbol/{symbol}` 回該 symbol 歷史入選紀錄。
4. 沒有資料時回明確 empty response，不回 500。

**Acceptance criteria:**

1. API response 與 Phase 0 schema contract 一致。
2. 支援 bucket filter 與 limit 參數，如果實作簡單且不擴 scope。
3. 所有 response 都包含 `data_dates` 或 run-level freshness 資訊。

## Phase 4: Frontend Daily Radar Page

### Task 4.1: 建立 API client 與 TypeScript types

**Files likely touched:**

* Create: `frontend/src/lib/dailyRadarApi.ts`
* Create: `frontend/src/lib/dailyRadarTypes.ts`
* Create: `frontend/src/lib/dailyRadarApi.test.ts` if test setup exists

**Tests first:**

1. API client 正確呼叫 `${import.meta.env.VITE_API_URL}/daily-radar/latest`。
2. Types 包含 bucket、risk label、repeat status、candidate、run response。
3. Error response 轉為前端可顯示錯誤狀態。

**Acceptance criteria:**

1. 前端不直接散落 hardcoded endpoint 字串。
2. Types 與後端 response schema 對齊。

### Task 4.2: 加入 route 與 page shell

**Files likely touched:**

* Create: `frontend/src/pages/DailyRadarPage.tsx`
* Modify: `frontend/src/App.tsx`
* Modify: `frontend/src/main.tsx` if route registry lives there

**Tests first:**

1. Component test 驗證 `/daily-radar` 顯示頁面標題「Daily Stock Radar」或「每日觀察雷達」。
2. Loading 狀態顯示資料載入中。
3. Error 狀態顯示可重試訊息。

**Acceptance criteria:**

1. Route 可由瀏覽器直接開啟。
2. Page shell 顯示最新掃描日期、資料日期、掃描狀態、候選數。

### Task 4.3: 實作 bucket tabs 與 candidate list

**Files likely touched:**

* Modify: `frontend/src/pages/DailyRadarPage.tsx`
* Create: `frontend/src/components/DailyRadarBucketTabs.tsx` if component split is useful
* Create: `frontend/src/components/DailyRadarCandidateList.tsx` if component split is useful

**Tests first:**

1. 四個 bucket tabs 顯示中文名稱與數量。
2. 預設依 `observation_score` 排序。
3. 點選 bucket 後只顯示該 primary bucket 候選。
4. Candidate list 顯示 symbol、name、primary bucket、觀察分數、risk labels、repeat status。

**Acceptance criteria:**

1. 高風險標籤在列表可見，不能只藏在 detail drawer。
2. `repeat` 標的顯示連續觀察語意，不把它描述成新訊號。

### Task 4.4: 實作 detail drawer

**Files likely touched:**

* Modify: `frontend/src/pages/DailyRadarPage.tsx`
* Create: `frontend/src/components/DailyRadarDetailDrawer.tsx` if component split is useful

**Tests first:**

1. 點選 candidate 開啟 detail drawer。
2. Drawer 顯示 score breakdown、matched rules、data dates、input snapshot 摘要、隔日觀察重點。
3. Drawer 提供連到 `/analyze` 的單股完整分析入口。

**Acceptance criteria:**

1. 使用者可追溯候選被選出的原因。
2. Link out 只說「查看單股完整分析」或同等觀察語言，不說買賣建議。

### Task 4.5: 完成 loading, empty, error, stale states 與 copy guardrails

**Files likely touched:**

* Modify: `frontend/src/pages/DailyRadarPage.tsx`
* Create: `frontend/src/pages/DailyRadarPage.test.tsx` if test setup exists

**Tests first:**

1. Empty state 文案為「今日沒有通過濾網的高品質 setup」或同等語意。
2. Stale state 明確顯示資料日期落後，不顯示高分推薦語氣。
3. Copy guard 測試頁面文字不含交易命令禁詞。

**Acceptance criteria:**

1. 使用者能分辨 empty、error、stale data。
2. 所有文案符合 spec 第 2.2 節文字護欄。

## Phase 5: GitHub Actions Schedule to Zeabur

### Task 5.1: 新增 Daily Radar cron workflow

**Files likely touched:**

* Create: `.github/workflows/daily-radar.yml`

**Tests first:**

1. Workflow syntax check if available。
2. Dry run plan 驗證 secrets 名稱存在於文件與 Zeabur 設定清單。

**Implementation notes:**

1. Workflow 使用 cron 在台股收盤且資料源通常更新後執行，建議先設定台灣時間晚間。
2. Workflow 只呼叫 Zeabur 後端 `POST /internal/daily-radar/run`。
3. Daily Radar service 內部負責資料載入與必要補齊、Stage 1 prefilter、Stage 2 detailed scoring。
4. 若未來需要拆分預取，另建 `POST /internal/daily-radar/prefetch`，不得直接重用 `/internal/fetch-raw-data`。
5. Secrets 建議：`ZEABUR_BACKEND_URL`、`DAILY_RADAR_INTERNAL_TOKEN`。
6. Header 使用 `Authorization: Bearer ${{ secrets.DAILY_RADAR_INTERNAL_TOKEN }}` 或後端約定的 `X-Internal-Token`。

**Acceptance criteria:**

1. Workflow 不包含 token 明文。
2. Workflow endpoint 指向 Zeabur backend URL，不出現其他部署平台假設。
3. Workflow log 可看出 daily radar run 的 HTTP status、run id、status、counts 與 errors。
4. 內部資料載入、prefilter、scoring 狀態需由 run response 或後端 log 追蹤。

## Phase 6: Docs Sync, Rollout, Acceptance

### Task 6.1: 同步 API 與 env 文件

**Files likely touched:**

* Modify later: `docs/specs/backend-api-technical-spec.md`
* Modify later: `README.md` if user asks for docs sync in a later delegation
* Modify later: `backend/.env.example` if env examples are tracked

**Tests first:**

1. 文件檢查 checklist 確認 internal endpoint、public read API、env vars、Zeabur schedule 都被記錄。

**Acceptance criteria:**

1. 本 delegation 不修改 README 或 spec index。
2. 實作完成後，後續 docs sync 需把落地 API contract 回填到 backend technical spec。

### Task 6.2: Rollout plan

**Steps:**

1. 本機用 fixtures 跑 `run_daily_radar`，確認四個 buckets 各至少一筆候選。
2. 本機啟動 FastAPI，使用 shared token 呼叫 `POST /internal/daily-radar/run`。
3. 本機開前端 `/daily-radar`，確認最新 run、bucket tabs、candidate list、detail drawer。
4. Zeabur 設定 `DAILY_RADAR_INTERNAL_TOKEN` 與資料庫連線。
5. GitHub repository secrets 設定 `ZEABUR_BACKEND_URL` 與 `DAILY_RADAR_INTERNAL_TOKEN`。
6. 先手動 dispatch workflow，確認單一 `POST /internal/daily-radar/run` 成功。
7. 觀察 run log，確認資料載入與必要補齊、prefilter、scoring、candidate persistence 都完成。
8. 觀察三個交易日 run log，確認 candidate count、stale guard、errors 摘要合理。

**Acceptance criteria:**

1. GitHub Actions 可呼叫 Zeabur 內部端點完成每日 run。
2. 後端可用 deterministic rule-based pipeline 產出候選清單。
3. 每筆候選都有 primary bucket、observation score、risk labels、matched rules、score breakdown、explanation、data dates。
4. 結果可持久化並依 latest、date、symbol 查詢。
5. React 前端可呈現最新 Daily Radar、bucket 篩選、candidate list、detail drawer。
6. 所有選股、排名與 bucket 判定不呼叫 LLM。
7. 文案符合觀察清單定位，不出現買賣命令式建議。

## Verification Commands For Implementation Sessions

後續實作 session 每完成一個 phase，至少執行對應指令：

```bash
cd backend
make test
```

```bash
cd frontend
pnpm test
```

如果 frontend 沒有 test script，改跑可用的型別或 build 指令：

```bash
cd frontend
pnpm build
```

排程與 Zeabur 驗證使用 workflow dispatch 或同等 curl 驗證：

```bash
curl -X POST "$ZEABUR_BACKEND_URL/internal/daily-radar/run" \
  -H "Authorization: Bearer $DAILY_RADAR_INTERNAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"market":"TW"}'
```

## Out Of Scope

1. AI 或 LLM 股票選擇、排名、bucket 判定。
2. Weekly big-holder 或 shareholding radar。
3. 借券訊號。
4. 完整產業相對強弱排名。
5. 直接買進、賣出、加碼、出場、目標價或勝率建議。
6. 把 `TaiwanStockHoldingSharesPer` 或 stockholder distribution 放進每日排名。
7. 改寫 `/analyze/position` 的持股診斷語意。
8. 本文件建立以外的 README 或 spec index 編輯。
