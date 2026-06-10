# Daily Radar v2 Optimization Copy-Paste Prompts

這份文件提供 Daily Radar v2 優化需求的分階段實作指令。每次只貼一個 phase 給實作代理，完成後停止並回報驗證結果，不要一次執行多個 phase。

共通原則：Daily Radar ranking、bucket 判定、分數計算與候選排序必須維持 deterministic rule-based；不得使用 LLM 參與 ranking 或覆寫規則。所有輸出維持觀察語言，不產生交易指令、價格承諾、勝率包裝或保證性語句。Daily run 不得對全市場做 per-symbol expensive API calls。

Shared evidence/context layer rule：實作 replayable signal evidence 或 risk background labels 時，命名、schema、repository seam 與 trace key 必須設計成 Daily Radar、個股分析、持股診斷與 lifecycle review 都可共用的 consumer-neutral contract。本 prompt series 先以 Daily Radar v2 作為第一個 foundation consumer，接著在 Phase 2C/2D 明確接入 `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review。除非進入對應 phase，否則不可順手改其他 consumer、相關 UI 或交易建議文案。

## Phase 0 - Baseline, Source Naming, And Spec Alignment Decision

```text
COPY-PASTE PROMPT: Phase 0 - Baseline, Source Naming, And Spec Alignment Decision

請只執行 Daily Radar v2 Optimization 的 Phase 0: Baseline, Source Naming, And Spec Alignment Decision。

本階段目標：在實作 Daily Radar v2 前，先建立目前 live behavior baseline、確認資料來源命名決策，並記錄會影響後續 Phase 1/2 的規格同步事項。本階段也要盤點既有 replayable evidence/context seams 與 consumer boundaries。本階段以文件與測試/觀測準備為主，不實作 market regime、多軌 universe、relative strength 或 chip context cache。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/README.md
- docs/plans/2026-06-02-daily-radar-rollout-checklist.md
- README.md 的 Daily Radar 區段
- backend/src/ai_stock_sentinel/daily_radar/router.py
- backend/src/ai_stock_sentinel/daily_radar/universe.py
- backend/src/ai_stock_sentinel/daily_radar/institutional_universe_provider.py
- backend/src/ai_stock_sentinel/daily_radar/service.py
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/tests/test_daily_radar_api.py
- backend/tests/test_daily_radar_universe.py
- backend/tests/test_daily_radar_service.py
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_raw_data.py

本階段必須遵守：
1. 不可改 Daily Radar ranking、bucket 判定或 scoring 行為。
2. 不可新增 Phase 1/2 的實作功能。
3. 不可新增 expensive live external calls。
4. 不可把未完成行為寫成正式 spec 既定事實。
5. 不可把 TWSE RWD、FinMind all-market、institutional universe provider 命名混用到 trace 或 docs。
6. 不可修改 frontend UI。
7. 不可把個股分析、持股診斷或 lifecycle review 整合列成本階段交付；只記錄後續 Phase 2C/2D 需要的 consumer data needs 與 seam。

本階段必做範圍：
1. 確認 live `POST /internal/daily-radar/run` 目前使用 `TwseRwdInstitutionalUniverseProvider` 與 `select_dual_track_universe`。
2. 確認 live `market_context` 目前在 router 傳入空物件，並記錄為 Phase 1 gap。
3. 確認目前 universe tracks 只有 `same_day_institutional` 與 `recent_accumulation`。
4. 確認 `score_breakdown`、`input_snapshot`、`data_dates`、`matched_rules` 已存在，但尚未完整包含 scoring/rule version 與 relative strength trace。
5. 建立或更新一份 baseline/decision 文件，記錄：目前 request pattern、candidate trace 欄位、known gaps、資料來源命名決策與後續 spec sync 待辦。
6. 若發現 `docs/specs/daily-stock-radar-spec.md` 與 live code 命名不一致，只能以 planned note 或 open question 方式記錄；不要假裝已同步完成。
7. 記錄現有 `/analyze`、`/analyze/position`、portfolio diagnosis、lifecycle review 與 Daily Radar 的 evidence/context 邊界，並列出 Phase 2C/2D 接入 shared layer 時不能覆寫的既有欄位與行為。

驗證要求：
- 跑 Daily Radar 相關現有測試中與本階段文件/contract 相關的最小集合。
- 若只改文件，確認 git diff 僅包含文件變更。
- 回報目前 live request pattern 是否仍符合 budget。

完成後停止。不要執行 Phase 1A、Phase 1B、Phase 1C、Phase 1D、Phase 1E 或 Phase 2。

完成後請回報：修改了哪些文件、baseline 結論、source naming 決策或 open questions、跑了哪些測試、下一個建議 phase。
```

## Phase 1A - Market Index Wiring And Regime v1

```text
COPY-PASTE PROMPT: Phase 1A - Market Index Wiring And Regime v1

請只執行 Daily Radar v2 Optimization 的 Phase 1A: Market Index Wiring And Regime v1。

前提：Phase 0 已完成，且已確認 live `market_context` 目前為空物件。

本階段目標：接上 market index OHLCV，產生 deterministic market regime v1，讓 live run response、candidate trace 與 persistence 不再只有空的 `market_context`。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- Phase 0 baseline/decision 文件
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/daily_radar/router.py
- backend/src/ai_stock_sentinel/daily_radar/service.py
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/raw_data.py
- backend/src/ai_stock_sentinel/daily_radar/data_loader.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/tests/test_daily_radar_api.py
- backend/tests/test_daily_radar_service.py
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_raw_data.py
- backend/tests/fixtures/daily_radar/market_context.json

本階段必須遵守：
1. Market regime 必須 rule-based，不可使用 LLM。
2. 不可新增全市場 per-symbol expensive calls。
3. Market index OHLCV 只能用明確 index symbol 的低成本資料取得或既有 cache/seam。
4. 若 index 資料不足或 stale，必須明確標示 missing/stale reason，不可偽造 constructive regime。
5. 不可在本階段實作 multi-track universe、relative strength ranking、backtest calibration 或 Phase 2 chip context。
6. 不可破壞現有 Daily Radar API response shape。

本階段必做範圍：
1. 新增 market index context 計算 seam，例如 market context builder/provider 或等效模組。
2. 以 market index OHLCV 產生 regime v1，至少能表達 constructive、neutral、risk-off 或等效狀態。
3. `POST /internal/daily-radar/run` 必須把非空 market context 傳入 `run_daily_radar`。
4. Candidate `score_breakdown.market_context`、`input_snapshot.market_context` 與 run response `market_context` 必須能看到 regime、index symbol、data date、missing/stale reason 或 freshness。
5. 若 market weakness，被 scoring 作為風險標籤或扣分依據時，trace 必須可解釋。
6. 更新必要 schema/tests/fixtures，維持 deterministic output。

TDD / 測試要求：
- 先新增或更新 market context tests，覆蓋 constructive、neutral、risk-off、missing/stale 資料。
- API test 確認 live run 不再把 `market_context={}` 傳入 service。
- Scoring test 確認 market weakness 會產生可追溯 risk/penalty，但資料不足不會偽造訊號。
- Service/API persistence test 確認 response 與 candidate trace 能讀回 market context。
- 執行 Daily Radar 相關最小測試集合。

Manual QA：
- 以測試 client 或本機 API seam 觸發一次 Daily Radar run，確認 response `market_context` 非空且候選 trace 可讀。

完成後停止。不要執行 Phase 1B、Phase 1C、Phase 1D、Phase 1E 或 Phase 2。

完成後請回報：修改了哪些檔案、market regime 規則、資料不足如何標示、跑了哪些測試/QA、是否有 pre-existing unrelated failures、下一個建議 phase。
```

## Phase 1B - Multi-Track Daily Trigger Universe

```text
COPY-PASTE PROMPT: Phase 1B - Multi-Track Daily Trigger Universe

請只執行 Daily Radar v2 Optimization 的 Phase 1B: Multi-Track Daily Trigger Universe。

前提：Phase 1A 已完成，market context 已接上。

本階段目標：保留既有 institutional tracks，新增由日頻 OHLCV/technical data 可支撐的 price-volume、reversal、support-retake 或等效 daily trigger tracks，讓同一 run 的候選來源不再只由法人雙軌構成。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- Phase 0 baseline/decision 文件
- backend/src/ai_stock_sentinel/daily_radar/universe.py
- backend/src/ai_stock_sentinel/daily_radar/institutional_universe_provider.py
- backend/src/ai_stock_sentinel/daily_radar/router.py
- backend/src/ai_stock_sentinel/daily_radar/raw_data.py
- backend/src/ai_stock_sentinel/daily_radar/data_loader.py
- backend/src/ai_stock_sentinel/daily_radar/prefilter.py
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/tests/test_daily_radar_universe.py
- backend/tests/test_daily_radar_api.py
- backend/tests/test_daily_radar_service.py
- backend/tests/test_daily_radar_raw_data.py

本階段必須遵守：
1. 保留既有 `same_day_institutional` 與 `recent_accumulation` tracks。
2. 新 tracks 必須只使用 daily trigger signal：OHLCV、technical indicators、已允許的 institutional all-market data、market context。
3. 不可把 weekly major holders、lending、full margin details 加入 Phase 1 ranking。
4. 不可在 daily run 對全市場逐檔做 expensive API calls。
5. 不可使用 LLM 選股、排名、bucket 判定或分數覆寫。
6. 不可破壞現有 selected-symbol OHLCV backfill 重用 `StockRawData` 的行為。

本階段必做範圍：
1. 擴充 universe track type 與 entry trace，支援 price-volume、reversal、support-retake 或等效名稱。
2. 設計 deterministic multi-track selection：排序、去重、track priority、track limit 必須可測。
3. 新 tracks 需能在 `DailyRadarUniverseEntry.track_metrics` 或 equivalent trace 中保留來源與分數。
4. Router/service 需能把 multi-track universe 的 track evidence 帶入 raw data/institutional payload/input snapshot。
5. Candidate trace 必須能看出該 candidate 命中哪些 universe tracks。
6. 若資料不足，需有 prefilter reason 或 missing data trace，不可靜默排除。

TDD / 測試要求：
- Universe unit tests：多軌合併、去重、排序、primary_track、tracks、track_metrics。
- API/service integration tests：run candidate universe 來源多元，不只法人雙軌。
- Request budget tests 或 mock provider assertions：新 tracks 不增加 forbidden external call pattern。
- Existing `test_daily_radar_universe.py`、`test_daily_radar_api.py`、`test_daily_radar_service.py` 相關測試仍通過。

Manual QA：
- 用 fixture 或測試 provider 跑一次 daily radar，確認候選 trace 可看到新增 tracks，且 selected symbols 後續 OHLCV backfill 行為仍只針對 selected universe。

完成後停止。不要執行 Phase 1C、Phase 1D、Phase 1E 或 Phase 2。

完成後請回報：新增哪些 tracks、去重/排序規則、修改檔案、跑了哪些測試/QA、request budget 是否維持、下一個建議 phase。
```

## Phase 1C - Relative Strength And Replayable Evidence Traceability

```text
COPY-PASTE PROMPT: Phase 1C - Relative Strength And Replayable Evidence Traceability

請只執行 Daily Radar v2 Optimization 的 Phase 1C: Relative Strength And Replayable Evidence Traceability。

前提：Phase 1A 已完成；Phase 1B 建議已完成，但若尚未完成，請只在現有 universe 上實作 relative strength 與 traceability，不要順手補 multi-track universe。

本階段目標：為每檔候選計算 relative strength vs market，並保存 scoring version、rule version、input data dates、score breakdown 與 replayable signal evidence，使任一 candidate 都能回放當時規則與輸入摘要。欄位命名與 trace key 應盡量 consumer-neutral，讓 Phase 2C/2D 的個股分析、持股診斷與 lifecycle review 能沿用；本階段只接 Daily Radar。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- Phase 0 baseline/decision 文件
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/service.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/daily_radar/schemas.py
- backend/src/ai_stock_sentinel/db/models.py
- frontend/src/lib/dailyRadarTypes.ts
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_repository.py
- backend/tests/test_daily_radar_api_contract.py
- backend/tests/test_daily_radar_api.py

本階段必須遵守：
1. Relative strength 必須由候選與 benchmark 可對齊的交易日資料計算，不可由 LLM 或文字推估。
2. 資料不足時必須保存 missing reason，不可填 0 假裝中性。
3. `observation_score` 仍是內部排序與 trace 用途，不可包裝成勝率。
4. 不可新增 weekly major holders、lending、full margin details 到 scoring。
5. 不可做 backtest calibration 流程；本階段只補 relative strength 與 version trace。
6. 不可破壞既有 API consumers；需要新增欄位時維持向後相容。
7. 不可改 `/analyze`、`/analyze/position`、portfolio diagnosis 或 lifecycle review 行為。

本階段必做範圍：
1. 新增 deterministic relative strength 計算 seam，記錄 benchmark symbol、lookback window、candidate return、benchmark return、relative value、data dates。
2. 將 relative strength 接入 scoring，可作排序調整或 score component，但必須在 `score_breakdown` 中清楚顯示權重與影響。
3. 在 candidate trace 保存 `scoring_version`、`rule_version`、relative strength details、input data dates。
4. 定義 replayable signal evidence 的 consumer-neutral trace shape，例如 evidence type、source、as_of_date、freshness、missing_reason、replay_key 或 equivalent 欄位。
5. 若需要 DB schema 擴充，新增 migration；若使用既有 JSONB 欄位，新增 repository/API tests 鎖定 contract。
6. 更新 backend schema 與 frontend shared types，讓 API contract 能表示新增 trace。
7. 更新 explanations 時只能使用觀察語言，描述相對強弱與風險，不給交易指令。

TDD / 測試要求：
- Relative strength unit tests：不同 lookback、交易日不對齊、資料不足、benchmark stale。
- Scoring tests：relative strength component 正確加分/扣分或排序調整，且 score breakdown 可追溯。
- Replayable evidence tests：trace shape 可表示 source、as_of_date、freshness、missing_reason、replay_key、applicable_consumers，且不要求其他 consumers 在本階段接入。
- Repository/API contract tests：candidate 持久化後可讀回 scoring/rule version 與 relative strength trace。
- Frontend typecheck/build 使用專案既有命令執行，若有前端 type 變更。
- Existing Daily Radar scoring/API/repository tests 仍通過。

Manual QA：
- 用 fixture 或測試資料跑一次 Daily Radar，抽查候選 response 包含 benchmark、lookback、relative strength value、version trace。

完成後停止。不要執行 Phase 1D、Phase 1E 或 Phase 2。

完成後請回報：relative strength 公式與 lookback、version 命名、修改檔案、跑了哪些測試/QA、任何資料不足情境、下一個建議 phase。
```

## Phase 1D - Backtest Calibration Workflow

```text
COPY-PASTE PROMPT: Phase 1D - Backtest Calibration Workflow

請只執行 Daily Radar v2 Optimization 的 Phase 1D: Backtest Calibration Workflow。

前提：Phase 1A 與 Phase 1C 已完成；Phase 1B 建議已完成。

本階段目標：建立可重跑的 backtest calibration 流程，用歷史 run 或可回放資料校準 bucket 門檻、風險扣分、relative strength 權重、過熱扣分與 rank cutoff，並產出 calibration report。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- Phase 0 baseline/decision 文件
- backend/src/ai_stock_sentinel/daily_radar/service.py
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/scripts/backtest_win_rate.py
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_service.py
- backend/tests/test_daily_radar_repository.py

本階段必須遵守：
1. Calibration 不可引入 LLM 排名、bucket 判定或分數覆寫。
2. Calibration report 不可宣稱勝率保證或價格承諾。
3. 不可用未來資料污染 signal-date 的 input snapshot。
4. 不可把 calibration 直接改成 live scoring 行為，除非測試與版本 trace 同步更新。
5. 不可刪除既有 scoring tests 來配合新權重。
6. 不可新增 expensive live daily run calls。

本階段必做範圍：
1. 建立 backtest/calibration command、script 或 service seam，可用 historical runs、candidate snapshots 或可重建資料重跑。
2. Calibration report 至少包含 sample count、bucket distribution、rank cutoff impact、overheat/risk penalty impact、relative strength impact、資料不足/skip reason。
3. Scoring config 或 version manifest 必須可追蹤每次規則/權重調整原因。
4. 若調整 live scoring config，必須 bump scoring/rule version，並更新 tests/fixtures。
5. Report 輸出需是可重跑、可 diff 的 deterministic artifact 或 structured output。
6. 文件記錄如何執行 calibration 與如何解讀報告。

TDD / 測試要求：
- Calibration fixture tests：同一 fixture 重跑產出穩定 report。
- Tests 覆蓋 rank cutoff、bucket threshold、risk penalty、relative strength 權重的影響。
- Tests 確認資料不足會列 skip reason，不會靜默排除。
- 若 live scoring config 改變，existing scoring/service/API tests 全部更新並通過。

Manual QA：
- 執行一次 calibration command 或 fixture run，保存/回報 report 摘要。

完成後停止。不要執行 Phase 1E 或 Phase 2。

完成後請回報：新增 command/文件、calibration report 摘要、是否調整 live scoring version、跑了哪些測試/QA、下一個建議 phase。
```

## Phase 1E - Phase 1 Release Gate And Spec Sync

```text
COPY-PASTE PROMPT: Phase 1E - Phase 1 Release Gate And Spec Sync

請只執行 Daily Radar v2 Optimization 的 Phase 1E: Phase 1 Release Gate And Spec Sync。

前提：Phase 1A、Phase 1B、Phase 1C、Phase 1D 已完成或已明確標示 deferred。

本階段目標：做 Phase 1 release gate，確認每日訊號準確度相關功能完整、request budget 合格、trace 可回放，並只把已穩定的事實同步到正式 spec。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- Phase 0 baseline/decision 文件
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/README.md
- docs/plans/2026-06-02-daily-radar-rollout-checklist.md
- README.md 的 Daily Radar 區段
- backend/src/ai_stock_sentinel/daily_radar/
- frontend/src/lib/dailyRadarTypes.ts
- frontend/src/pages/DailyRadarPage.tsx
- backend/tests/test_daily_radar_api.py
- backend/tests/test_daily_radar_api_contract.py
- backend/tests/test_daily_radar_service.py
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_universe.py
- backend/tests/test_daily_radar_raw_data.py

本階段必須遵守：
1. 只驗證與同步已完成且測試穩定的 Phase 1 事實。
2. 不可新增 Phase 2 chip context cache、weekly major holders、lending 或 full margin details。
3. 不可把 planned/deferred 功能寫成正式 spec 已完成。
4. 不可引入 LLM ranking 或交易指令語言。
5. 不可修改測試來掩蓋 release gate failure。
6. 不可忽略 request budget regression。

本階段必做範圍：
1. 檢查 Phase 1 success criteria：multi-track universe、market regime、relative strength、score/version trace、replayable signal evidence、calibration report。
2. 執行 Daily Radar backend 相關測試與 frontend typecheck/build（若前端 contract/UI 有變更）。
3. 做一次 manual daily run QA，確認 run 成功、market_context 非空、candidate trace 完整、request pattern 合格。
4. 更新 `docs/specs/daily-stock-radar-spec.md` 中已完成且穩定的 API/schema/request budget/trace 事實。
5. 保留 `docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md` 作為需求脈絡，不要刪除 discussion。
6. 若仍有 deferred/open questions，留在 plans 或 release note，不寫成正式 contract。
7. 檢查 Phase 1 trace 欄位是否符合 shared evidence/context vocabulary，且能支援 Phase 2C/2D consumers，但本階段沒有提前接入 `/analyze`、portfolio 或 lifecycle review。

驗收要求：
- 任一 candidate 都能看到 market regime、relative strength、matched rules、score breakdown、data dates、scoring version/rule version、replayable evidence 或明確缺資料原因。
- Daily run 無 LLM key 時仍可產生 deterministic ranking。
- Daily run 沒有新增全市場 per-symbol expensive calls。
- Spec 僅包含已穩定事實。

完成後停止。不要執行 Phase 2A、Phase 2B、Phase 2C、Phase 2D 或 Phase 2E。

完成後請回報：release gate 結果、spec sync 內容、跑了哪些測試/QA、仍 deferred 的項目、下一個建議 phase。
```

## Phase 2A - Shared Background Context Cache Foundation

```text
COPY-PASTE PROMPT: Phase 2A - Shared Background Context Cache Foundation

請只執行 Daily Radar v2 Optimization 的 Phase 2A: Shared Background Context Cache Foundation。

前提：Phase 1 release gate 已完成或使用者明確同意先做 Phase 2 foundation。

本階段目標：建立 daily run 之外的 shared background context cache、資料表、repository、backend internal updater endpoint 與 GitHub Actions 排程，讓 weekly major holders、lending、full margin context 由背景流程寫入 consumer-neutral cache，不在 daily run 或其他分析流程即時逐檔呼叫。Daily Radar 是本階段第一個讀取 consumer；`/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 將在 Phase 2C/2D 接入。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/daily_radar/router.py
- backend/src/ai_stock_sentinel/daily_radar/service.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/alembic/versions/
- .github/workflows/daily-radar.yml
- backend/tests/test_daily_radar_repository.py
- backend/tests/test_db_models.py
- backend/tests/test_daily_radar_raw_data.py

本階段必須遵守：
1. 不可把 weekly major holders、lending 或 full margin context 加入 Phase 1 ranking driver。
2. Daily run 只能讀 cache，不可在主流程做全市場 per-symbol expensive calls。
3. 背景更新失敗不可阻塞 existing daily run。
4. Missing/stale context 必須明確標示，不可用 minimal margin `data_date` 暗示 full margin 已取得。
5. 不可修改候選 ranking 或 observation score。
6. 不可做 frontend labels；本階段只建立 cache foundation。
7. 不可把背景更新路徑設計成多選方案；正式路徑固定為 GitHub Actions 排程呼叫 backend internal endpoint。
8. 不可把 cache schema 綁死 Daily Radar UI 或 ranking；不可改其他 consumer 行為。

本階段必做範圍：
1. 新增 shared background context table/model/migration，保存 symbol、context_type、applicable_consumers 或 equivalent consumer scope、source、as_of_date、freshness、payload、missing_reason、replay_key 或 equivalent 欄位。
2. 新增 repository/read interface，支援 daily run 或 API 依 selected symbols 批次讀取 cache。
3. 新增 backend internal endpoint：`POST /internal/daily-radar/chip-context/update`，使用既有 Daily Radar internal auth 模式或等效 Bearer token 驗證。
4. 新增 background updater service，由 internal endpoint 觸發，負責更新 weekly major holders、lending、full margin context；實際 provider 可先以 interface/fixture/stub 落地，但 endpoint、updater contract 與 cache write path 必須清楚。
5. 新增 GitHub Actions workflow，例如 `.github/workflows/daily-radar-chip-context.yml`，定時呼叫 `POST /internal/daily-radar/chip-context/update`；workflow 使用既有 backend URL/internal token secret pattern，不得寫入 secrets。
6. 若需要 script/command，只能作為本機測試或手動除錯輔助，不可作為正式背景更新路徑。
7. Tests 鎖定 daily run 不會因 cache missing/stale 失敗。
8. Tests 鎖定 daily run 不會呼叫 forbidden per-symbol expensive provider。
9. 文件說明正式更新路徑、cache 更新頻率、freshness、失敗處理與 request budget。
10. 文件說明 Daily Radar 是第一個 foundation consumer，`/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 在 Phase 2C/2D 以 read/reference 方式接入。

TDD / 測試要求：
- Migration/model tests：table columns、indexes、constraints。
- Repository tests：upsert/read by selected symbols、fresh/stale/missing cases。
- Shared contract tests：cache records 不依賴 Daily Radar UI/ranking 欄位，且可表示 applicable consumers。
- Internal endpoint/updater tests：`POST /internal/daily-radar/chip-context/update` 需要 internal auth，能觸發 updater，失敗可記錄但不阻塞 daily run。
- GitHub Actions workflow review/test：確認 workflow 定時呼叫 chip-context endpoint，使用 secret/variable，不硬編 secrets。
- Daily run integration/mock tests：主流程只讀 cache，不做 forbidden provider calls。

Manual QA：
- 執行一次 repository/updater fixture flow，確認 cache 可寫入讀回。

完成後停止。不要執行 Phase 2B、Phase 2C、Phase 2D 或 Phase 2E。

完成後請回報：新增 table/interface、internal endpoint、GitHub Actions workflow、migration 名稱、request budget guard、跑了哪些測試/QA、下一個建議 phase。
```

## Phase 2B - Shared Background Context Labels In Daily Radar API And Detail Trace

```text
COPY-PASTE PROMPT: Phase 2B - Shared Background Context Labels In Daily Radar API And Detail Trace

請只執行 Daily Radar v2 Optimization 的 Phase 2B: Shared Background Context Labels In Daily Radar API And Detail Trace。

前提：Phase 2A 已完成，background chip context cache 可讀。

本階段目標：把 weekly major holders、lending、full margin context 以 shared background labels/detail context 方式接到 Daily Radar API trace。這些 labels 必須使用 consumer-neutral contract，但本階段只在 Daily Radar API/detail surface 顯示；不得改變 ranking 或 score，也不得提前改 Phase 2C/2D consumers。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- Phase 2A cache foundation 文件或實作
- backend/src/ai_stock_sentinel/daily_radar/router.py
- backend/src/ai_stock_sentinel/daily_radar/service.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/daily_radar/schemas.py
- frontend/src/lib/dailyRadarTypes.ts
- frontend/src/pages/DailyRadarPage.tsx
- backend/tests/test_daily_radar_api.py
- backend/tests/test_daily_radar_api_contract.py
- backend/tests/test_daily_radar_repository.py

本階段必須遵守：
1. Weekly major holders 只能顯示為 background label，不可進 score driver。
2. Lending 與 full margin context 只能作為背景風險/context，不可在 daily run 即時逐檔抓取。
3. Ranking 在移除 background context 後必須不變。
4. API/UI 語言必須清楚區分 daily trigger signal 與 background context。
5. Missing/stale context 必須顯示 missing reason/freshness，不可靜默忽略。
6. 不可產生交易指令、價格承諾或保證性語句。
7. 不可同步修改個股分析、持股診斷或 lifecycle review 顯示；這些 consumer 只能在 Phase 2C/2D 接入。

本階段必做範圍：
1. API response 或 candidate detail trace 新增 background context labels，包含 context_type、label、source、as_of_date、freshness、missing_reason、replay_key 或 equivalent 欄位。
2. Weekly major holders label 顯示為背景持股集中/大戶脈絡，不參與 observation score。
3. Lending label 顯示空方壓力或背景風險，不參與 daily trigger ranking。
4. Full margin context 與 existing minimal margin `data_date` 清楚分離命名。
5. Tests 證明有無 background context 時 score/ranking 不變。
6. 更新 frontend shared types；若 detail UI 已有合適位置，可顯示 labels，但不得改 ranking 視覺語意。
7. 文件或 API contract 說明 labels 是 shared background context 的 Daily Radar surface，不是交易 action 或 portfolio recommendation。

TDD / 測試要求：
- API/schema tests：response 可表示 background labels、freshness、missing reason。
- Shared contract tests：labels 可用 consumer-neutral 欄位表示，且不要求其他 workflows 在本階段接入。
- Scoring tests：background context 不影響 observation score 或 bucket 判定。
- Repository/API integration tests：daily run 只讀 cache。
- Frontend typecheck/build，若有前端變更。

Manual QA：
- 用 fixture cache 跑 latest/detail response，確認 labels 可讀且 score/ranking 不因 labels 改變。

完成後停止。不要執行 Phase 2C、Phase 2D 或 Phase 2E。

完成後請回報：新增 labels、API/type 變更、score unchanged 驗證、跑了哪些測試/QA、下一個建議 phase。
```

## Phase 2C - Shared Context Read Contract For Analyze And Position Analysis

```text
COPY-PASTE PROMPT: Phase 2C - Shared Context Read Contract For Analyze And Position Analysis

請只執行 Daily Radar v2 Optimization 的 Phase 2C: Shared Context Read Contract For Analyze And Position Analysis。

前提：Phase 2A 與 Phase 2B 已完成，shared background context cache 與 Daily Radar surface 已穩定。

本階段目標：讓 `/analyze` 與 `/analyze/position` 以 read/reference 方式引用 shared evidence/context layer。個股分析可顯示 setup evidence、風險背景與資料完整度 caveats；持股分析可顯示 context caveats，但不得覆寫 `recommended_action`、`trailing_stop`、`exit_reason` 或任何既有 rule-based field。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/api.py
- backend/src/ai_stock_sentinel/models.py
- backend/src/ai_stock_sentinel/graph/nodes.py
- backend/src/ai_stock_sentinel/analysis/context_generator.py
- backend/src/ai_stock_sentinel/analysis/strategy_generator.py
- backend/src/ai_stock_sentinel/analysis/position_scorer.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- frontend/src/pages/AnalyzePage.tsx
- frontend/src/lib/dailyRadarTypes.ts
- backend/tests/test_api.py
- backend/tests/test_position_scorer.py
- backend/tests/test_daily_radar_api_contract.py

本階段必須遵守：
1. `/analyze` 與 `/analyze/position` 只能讀 shared cache/repository，不可觸發全市場或逐檔昂貴即時查詢。
2. LLM 不可自行計算 shared context 數值，不可用 shared context 覆寫 rule-based fields。
3. `/analyze/position` 不可因 shared context 改寫 `recommended_action`、`trailing_stop`、`exit_reason`。
4. Missing/stale context 必須非阻塞，並以資料 caveat 或 missing reason 表示。
5. UI/API copy 只能描述 evidence、context、risk、data quality，不可產生交易指令、價格承諾或勝率語言。

本階段必做範圍：
1. 新增 shared context read seam，讓 `/analyze` 可依 symbol/date 讀取 shared evidence/context。
2. 在 `/analyze` response 或 analysis detail 中加入 shared context caveats/evidence，保持向後相容。
3. 讓 `/analyze/position` 可讀取 shared context caveats，但只作為輔助證據與資料品質說明。
4. 更新 backend schema/frontend shared types；若前端呈現，使用觀察語言與資料 caveat，不改主要 action UX。
5. 確認 missing/stale shared context 不阻塞分析流程。

TDD / 測試要求：
- API/schema tests：`/analyze` response 可表示 shared evidence/context 或 missing reason。
- Analyzer boundary tests：LLM 不計算 shared context 數值，也不覆寫 deterministic fields。
- Position analysis tests：shared context 不改變 `recommended_action`、`trailing_stop`、`exit_reason`。
- Missing/stale tests：context missing 時 response 有 caveat 且流程成功。
- Frontend typecheck/build，若有前端 type/UI 變更。

Manual QA：
- 用 fixture symbol 跑 `/analyze` 與 `/analyze/position`，確認 shared context 可讀、缺資料可顯示、既有 action fields 不被覆寫。

完成後停止。不要執行 Phase 2D 或 Phase 2E。

完成後請回報：新增 read seam、API/type 變更、哪些欄位保持不變、跑了哪些測試/QA、下一個建議 phase。
```

## Phase 2D - Portfolio Diagnosis And Lifecycle Review Shared Context References

```text
COPY-PASTE PROMPT: Phase 2D - Portfolio Diagnosis And Lifecycle Review Shared Context References

請只執行 Daily Radar v2 Optimization 的 Phase 2D: Portfolio Diagnosis And Lifecycle Review Shared Context References。

前提：Phase 2A、Phase 2B 與 Phase 2C 已完成。

本階段目標：讓 portfolio diagnosis 與 lifecycle review 以 read/reference 方式引用 shared evidence/context layer。Portfolio diagnosis 只能把 shared context 作為 evidence/caveat，不可產生 portfolio action；lifecycle review 必須使用 point-in-time context 回放當時 evidence，不可用 future data 批評 entry/exit-time decision。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/analysis/position_lifecycle.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/pages/ClosedPortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py
- backend/tests/test_position_lifecycle_analysis.py

本階段必須遵守：
1. Portfolio diagnosis 不可把 shared context 轉成 portfolio action、加減碼指令或交易建議。
2. Lifecycle review 不可使用 signal/entry/exit date 之後的 future data 作為當時 decision 的批評依據。
3. Missing/stale context 必須非阻塞，並顯示 missing reason 或 data quality note。
4. 不可改變 Single Trade Review row-scoped 語意，除非需求文件另有明確要求。
5. 不可讓 LLM 推論未記錄 intent 或覆寫 deterministic lifecycle classifications。

本階段必做範圍：
1. Portfolio diagnosis read path：依 symbol/date 讀取 shared context，加入 evidence/caveat/data quality notes。
2. Lifecycle review read path：依事件日期或 review 的 relevant date 讀取 point-in-time shared context。
3. Evidence payload 必須保存 shared context 的 source、as_of_date、freshness、missing_reason、replay_key。
4. 若 context 不存在，保留 insufficient/missing data 語意，不阻塞 diagnosis/review。
5. 更新 backend schema/frontend shared types；UI copy 只能描述資料證據、風險背景與 caveat。

TDD / 測試要求：
- Portfolio diagnosis tests：shared context 可讀取並出現在 evidence/caveat，但不產生 portfolio action。
- Lifecycle replay tests：review 使用 point-in-time context，不讀 future context。
- Missing/stale tests：context missing 時保留 insufficient/missing note，流程成功。
- LLM boundary tests：不推論未記錄 intent，不覆寫 deterministic classification。
- Frontend typecheck/build，若有前端 type/UI 變更。

Manual QA：
- 用 active position 與 closed lifecycle fixture 檢查 shared context caveat 顯示，確認沒有交易指令或 future-data leakage。

完成後停止。不要執行 Phase 2E。

完成後請回報：portfolio/lifecycle read path、point-in-time handling、欄位不變項、跑了哪些測試/QA、下一個建議 phase。
```

## Phase 2E - Phase 2 Release Gate And Observation-Language Review

```text
COPY-PASTE PROMPT: Phase 2E - Phase 2 Release Gate And Observation-Language Review

請只執行 Daily Radar v2 Optimization 的 Phase 2E: Phase 2 Release Gate And Observation-Language Review。

前提：Phase 2A、Phase 2B、Phase 2C 與 Phase 2D 已完成。

本階段目標：驗證 shared background context 與 daily trigger signals 已清楚分離，request budget 合格，所有 API/UI/explanation copy 都維持觀察語言，並只把已穩定的 Phase 2 事實同步到 spec。

請先閱讀：
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/README.md
- README.md 的 Daily Radar 區段
- backend/src/ai_stock_sentinel/daily_radar/
- frontend/src/lib/dailyRadarTypes.ts
- frontend/src/pages/DailyRadarPage.tsx
- frontend/src/pages/AnalyzePage.tsx
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/pages/ClosedPortfolioPage.tsx
- backend/tests/test_daily_radar_api.py
- backend/tests/test_daily_radar_api_contract.py
- backend/tests/test_daily_radar_repository.py
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_raw_data.py
- backend/tests/test_portfolio_router.py
- backend/tests/test_position_lifecycle_analysis.py

本階段必須遵守：
1. 不可新增功能；只做 release gate、copy review、spec sync 與必要 bug fix。
2. 不可把 background context 改成 ranking driver。
3. 不可忽略 request budget 或 provider call regression。
4. 不可把 `observation_score` 包裝成勝率或保證結果。
5. 不可刪除 failing tests 來通過 release gate。
6. Spec 只能記錄已穩定實作。
7. 不可讓 shared context 變成 trading instruction、recommended action、portfolio action 或 lifecycle verdict。

本階段必做範圍：
1. 驗證 Phase 2 success criteria：獨立 cache/table/schedule/freshness trace、weekly major holders labels、lending/full margin missing handling、daily run request pattern 可控。
2. Snapshot/API/UI copy review：不得出現交易指令、價格承諾、勝率保證或保證性結果語言。
3. Tests 確認 ranking 不依賴 weekly major holders 或 other background context。
4. Tests 或 mocks 確認 daily run 不對全市場逐檔呼叫昂貴資料源。
5. 更新正式 spec 中已穩定的 Phase 2 API/schema/cache/request budget 事實。
6. 若有未穩定 provider 或排程事項，留在 plans/open questions，不寫成正式 contract。
7. 驗證 shared evidence/context contract 是 consumer-neutral，且 Daily Radar、`/analyze`、`/analyze/position`、portfolio diagnosis、lifecycle review 都以 read/reference 方式使用。
8. 驗證 `/analyze/position`、portfolio diagnosis 與 lifecycle review 的 deterministic action/verdict/classification 沒有被 shared context 直接覆寫。

驗收要求：
- Background context 與 daily trigger signal 在資料、命名、trace、API/UI 語言上清楚分離。
- Daily run 缺少 background context 時仍可成功產生候選。
- 移除 weekly major holders cache 後 ranking 不變。
- Request budget tests 或 mock assertions 通過。
- `/analyze`、`/analyze/position`、portfolio diagnosis、lifecycle review 缺少 shared context 時仍可成功回應。
- Shared context 不直接改寫 recommended action、portfolio action 或 lifecycle verdict。

完成後停止。

完成後請回報：Phase 2 release gate 結果、spec sync 內容、copy review 發現、跑了哪些測試/QA、仍 deferred 的項目。
```
