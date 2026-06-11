# Daily Radar v2 優化需求文件

| Metadata | Value |
| -------- | ----- |
| Status | planned |
| Date | 2026-06-10 |
| Spec reference | docs/specs/daily-stock-radar-spec.md |

本文件是 Daily Radar v2 優化的需求文件，不是實作程式碼，也不取代既有長期規格。若後續落地改變 API、資料表、排程或公開行為，需再同步更新 `docs/specs/daily-stock-radar-spec.md` 或對應技術規格。

## 1. 目標與可行性結論

Daily Radar v2 的目標是讓每日雷達更準確、更可回放、更容易校準，同時把週頻或昂貴籌碼資料移到背景脈絡，不讓每日執行流程承擔高成本查詢。

本需求同時建立一個 reusable evidence/context layer 的第一版語意：可回放訊號證據與風險背景標籤應設計成 consumer-neutral 的資料與 trace contract。Daily Radar v2 是第一個 foundation consumer；同一輪 Phase 2 也必須讓個股分析 `/analyze`、持股分析 `/analyze/position`、portfolio diagnosis 與 lifecycle review 能引用同一套 shared evidence/context layer。這些 consumers 只能把 shared layer 作為 evidence、context、caveat 與資料完整度來源，不可把背景 context 直接轉換成交易指令、recommended action 或 portfolio action。

可行性結論：

| Phase | 結論 | 依據 |
| ----- | ---- | ---- |
| Phase 1 | 可用現有 seams 實作 | 目前已有 OHLCV、technical indicators、`DailyRadarCandidate`、`StockRawData`、yfinance backfill、persistence seams。只需補上 market index wiring，就能完成多軌 universe、市況 regime v1、相對大盤強弱、回測校準與 scoring/version traceability，並產生 reusable replayable signal evidence 的第一批欄位。 |
| Phase 2 | 需要新背景 cache、資料表、排程與跨 consumer 讀取介面 | 週頻大戶、借券、完整融資融券與 chip context 不應在 daily run 或其他分析流程即時逐檔呼叫。需先建立 consumer-neutral 背景快取、資料表、更新排程與讀取介面，再依序接到 Daily Radar、`/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review。 |

## 2. 目前系統事實

以下事實來自現有文件與目前 Daily Radar live 說明，用於界定本需求的起點：

1. `POST /internal/daily-radar/run` 是自包含的 live run 入口，由後端自行選出 universe、補齊 selected symbols 的 OHLCV、執行 Stage 1/2 rule-based scoring，並持久化 run log 與 candidates。
2. Live universe 目前不是全市場逐檔掃描，而是雙軌 institutional universe：same-day institutional leaders top 50 加 recent accumulation/concentration leaders top 50，合併後去重。
3. 既有 spec 描述的是 FinMind all-market 法人資料 request budget；但目前 live Daily Radar universe code path 使用 `TwseRwdInstitutionalUniverseProvider`，由 TWSE RWD 外資與投信報表 `TWT38U` / `TWT44U` 建立法人雙軌 universe，不是 FinMind all-market path。
4. yfinance OHLCV 只對 selected universe 中缺少 final `StockRawData` 的 symbol 做 batch download，已有 final raw rows 會重用。
5. Candidate ranking 由 deterministic rule-based buckets 與 scoring 完成，不使用 LLM 選股、排名或 bucket 判定。
6. 現有 bucket 包含 institutional accumulation、price volume strengthening、bottoming reversal、support retest，輸出需要 matched rules、score breakdown、input snapshot、data dates 與 prefilter reasons 等 trace evidence。
7. 已知目前 gap：live `market_context` 以空物件傳入，因此完整大盤濾網尚未接上。Live full margin 也仍不完整，目前只有 minimal margin `data_date`，不能視為完整融資融券內容。
8. 已知目前 gap：法人資料來源命名存在 docs-vs-code mismatch。Phase 1 實作前需決定以 TWSE RWD、FinMind all-market 或明確分層命名作為正式規格，並同步更新 spec 與程式命名，避免 trace 與 request budget 誤導。

## 3. 範圍切分

### 3.1 Phase 1, 每日訊號準確度

Phase 1 使用日頻資料與現有儲存結構改善排名品質，重點是把每日雷達從單一籌碼偏向，升級成多軌、可校準、可解釋的觀察清單。

必須包含：

1. Multi-track universe：保留現有 institutional tracks，新增由 OHLCV 與 technical data 可支撐的 price-volume、reversal、support-retake 或 equivalent tracks。
2. Market regime v1：接上 market index OHLCV，產生 basic regime，例如 constructive、neutral、risk-off，用於降級或風險標籤。
3. Relative strength vs market：每檔候選需可計算相對大盤強弱，用作排序調整與 trace evidence。
4. Backtest calibration：用歷史 run 或可回放資料校準分數權重、過熱扣分、bucket 門檻與 rank cutoff。
5. Scoring/version traceability：每次 run 必須保存 scoring version、rule version、input data dates 與 score breakdown，讓回測結果能對應當時規則。

### 3.2 Phase 2, 背景籌碼脈絡

Phase 2 的目標不是把週頻或昂貴資料塞進 daily ranking，而是建立 shared background context cache，讓 Daily Radar detail view 先顯示「籌碼背景標籤」，再讓個股分析、持股分析、portfolio diagnosis 與 lifecycle review 引用同一套背景 context 與 replayable evidence。

必須包含：

1. Background chip context cache：建立 daily run 之外的快取來源與讀取介面。
2. Weekly major holders：週頻大戶資料先作為背景 label 顯示，不作為 Phase 2 初期的 score driver。
3. Lending：借券資料作為空方壓力或背景風險 context，不能要求 daily run 即時全市場逐檔呼叫。
4. Full margin：補齊完整融資融券資料，但仍需與 daily trigger signal 分開管理。
5. Context separation：daily trigger signals 與 background context 必須在資料、命名、trace 與 UI 語言上清楚分離。
6. No expensive calls during daily run：每日執行期間不得對全市場做 per-symbol expensive API calls。Daily run 只能讀既有 cache 或對 selected universe 做已允許的低成本補齊。

### 3.3 Reusable evidence/context layer

可回放訊號證據與風險背景標籤應以 compact、provenance-rich records 表示，讓不同 workflow 能檢查同一套資料來源、計算版本、資料日期與缺資料原因。

Daily Radar v2 是第一個 foundation consumer。Shared layer 的命名、schema 與 repository seam 不應綁死 Daily Radar UI 或 ranking 語意；本輪 Phase 2 必須接上以下 consumers 的 read/reference path：

1. `/analyze`：可引用 shared evidence/context 作為單檔 setup、風險背景與資料完整度說明，但 LLM 不可自行計算數值或覆寫 rule-based fields。
2. `/analyze/position`：可引用 shared evidence/context 作為持股風險 caveat，但不可直接改寫 `recommended_action`、`trailing_stop` 或 `exit_reason`。
3. Portfolio diagnosis：可引用 shared context 作為資料證據與風險說明，但不可直接產生 portfolio action。
4. Lifecycle review：可引用 point-in-time shared context 回放當時 evidence，且不可使用 signal date 之後的 future data 批評當時 decision。

## 4. 資料分類

| 分類 | 資料 | 更新頻率 | 用途 | 是否可進入 daily v1/v2 排名 | 備註 |
| ---- | ---- | -------- | ---- | -------------------------- | ---- |
| Daily trigger signal | OHLCV | 日頻 | 趨勢、量價、波動、支撐壓力 | 可以 | yfinance selected-symbol backfill 與 `StockRawData` 重用。 |
| Daily trigger signal | Technical indicators | 日頻 | bucket 命中、過熱扣分、反轉確認 | 可以 | 需保存計算版本與輸入日期。 |
| Daily trigger signal | Institutional flow | 日頻 | 法人方向、連續性、集中度 | 可以 | 使用 all-market 查詢結果，不做逐檔昂貴呼叫。 |
| Daily trigger signal | Market index OHLCV | 日頻 | market regime、relative strength benchmark | 可以 | Phase 1 必須接上，目前 live `market_context` 為空。 |
| Daily trigger signal | Margin summary | 日頻 | 融資風險標籤與扣分 | 有完整資料後才可以 | 目前 live full margin 不完整，只能標示資料不足或 minimal context。 |
| Background context | Weekly major holders | 週頻 | 大戶持股背景標籤 | 不可以作為 Phase 1 ranking driver | Phase 2 可先顯示為背景 label，不進 score。 |
| Background context | Lending | 日頻或依資料源 | 借券壓力背景 | 不可在 daily run 即時逐檔抓取 | 需背景 cache。 |
| Background context | Full margin details | 日頻 | 籌碼背景與風險說明 | 需等背景 cache 穩定後再評估 | 需避免與 minimal margin `data_date` 混淆。 |

Reusable evidence/context records 至少應能表示 `applicable_consumers` 或 equivalent consumer scope、`evidence_type` / `context_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key` 或 equivalent trace key。Daily Radar v2 可以先使用其中與候選排序、detail trace、background labels 直接相關的欄位；`/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 必須在 Phase 2 以 read/reference 方式接入。

## 5. 功能需求

| ID | Description | Validation method | Data sufficiency | Acceptance criteria |
| -- | ----------- | ----------------- | ---------------- | ------------------- |
| DRV2-P1-01 | 建立 multi-track universe，除現有 institutional tracks 外，納入日頻 OHLCV/technical 可支撐的量價、反轉與支撐類軌道。 | Unit tests for universe provider and integration test for run candidate diversity. | 需要 selected symbols 有足夠近 60 到 120 個交易日 OHLCV。 | 同一 run 的候選來源可追溯到 track，且不是只由法人雙軌構成。 |
| DRV2-P1-02 | 接上 market index wiring，產生 market regime v1 並填入 live `market_context`。 | Unit tests for regime calculation and live run response/persistence assertion. | 需要 market index OHLCV 最新日期不落後容忍值。 | `market_context` 不再是空物件，candidate trace 可看到 regime 與資料日期。 |
| DRV2-P1-03 | 為每檔候選計算 relative strength vs market，並保存於 trace。 | Unit tests for relative strength math and regression fixtures. | 需要候選與 index 有可對齊的交易日資料。 | 每筆 candidate 有 relative strength value、lookback window 與 benchmark symbol。 |
| DRV2-P1-04 | 建立 backtest calibration 流程，校準 bucket 門檻、風險扣分與 rank cutoff。 | Backtest command or test fixture comparing historical ranks and outcomes. | 需要歷史 OHLCV、candidate input snapshots 或可重建資料。 | 可產出 calibration report，並能說明每次 scoring version 的變更原因。 |
| DRV2-P1-05 | 保存 scoring/version traceability，包括 scoring version、rule version、matched rules、score breakdown、input data dates。 | Persistence tests and API schema tests. | 每個核心資料源需有 data date 或明確缺資料原因。 | 任一 candidate 都能回放當時規則與輸入摘要，不只保存最後分數。 |
| DRV2-P1-06 | 保持 LLM 不參與候選排名、bucket 判定或分數覆寫。 | Tests for service boundaries and code review checklist. | 不依賴 LLM response。 | Daily Radar 在沒有 LLM key 時仍可產生同樣 ranking result。 |
| DRV2-SHARED-01 | 建立 reusable evidence/context contract，讓 replayable signal evidence 與 risk background labels 可被 Daily Radar、個股分析、持股診斷與 lifecycle review 共用。 | Schema/repository seam tests and documentation review. | 需要 evidence/context records 保存 source、as_of_date、freshness、missing reason、replay key 與 applicable consumers。 | Daily Radar v2 是第一個 foundation consumer；同一輪 Phase 2 需提供 `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 的 read/reference path。 |
| DRV2-P2-01 | 建立 background chip context cache，用於週頻大戶、借券與完整融資融券資料。 | Migration tests, repository tests, scheduled job tests. | 需要資料表保存 source、as_of_date、freshness 與 symbol。 | Daily run 只讀 cache，不在主流程做全市場逐檔昂貴查詢。 |
| DRV2-P2-02 | 週頻大戶資料先顯示為 background label，不作為 score driver。 | API/UI tests for context labels and scoring tests proving score unchanged. | 需要週頻資料有 as_of_date 與 freshness label。 | Candidate detail 可顯示大戶背景，但 ranking 在移除該資料後不變。 |
| DRV2-P2-03 | 補齊 lending 與 full margin context，並與 daily trigger signals 分開命名與追蹤。 | Repository tests and response schema tests. | 需要資料完整度標籤與缺資料原因。 | 前端與 API 可區分 trigger signal、background context、missing context。 |
| DRV2-P2-04 | 禁止 daily run 做全市場 per-symbol expensive API calls。 | Integration tests with mocked providers and request count assertions. | Daily run 只能用 all-market query、batch OHLCV 或 background cache。 | Full run 的 external call pattern 符合 budget，且超額時測試失敗。 |
| DRV2-P2-05 | 所有輸出維持觀察語言，只描述 setup、風險、背景與資料證據。 | Snapshot tests for generated explanations and UI copy review. | 不需要交易指令欄位。 | 不出現交易指令、價格承諾或保證性結果語言。 |
| DRV2-SHARED-02 | `/analyze` 與 `/analyze/position` 引用 shared evidence/context。 | API/schema tests, analyzer boundary tests, response snapshot tests. | 需要 selected symbol 可讀取 shared context；missing/stale context 必須非阻塞。 | 個股分析可顯示 shared context caveats；持股分析可顯示 shared context caveats，但不覆寫 rule-based fields 或 LLM output contract。 |
| DRV2-SHARED-03 | Portfolio diagnosis 與 lifecycle review 引用 shared evidence/context。 | Portfolio diagnosis tests, lifecycle replay tests, future-data leakage tests. | 需要 point-in-time context 或明確 missing reason。 | Portfolio diagnosis 只把 shared context 當 evidence/caveat；lifecycle review 可回放當時 context，且不得用未來資料污染 entry/exit-time review。 |

## 6. 護欄

1. 不使用 LLM 做 candidate ranking、bucket 判定、分數計算或規則覆寫。
2. 週頻大戶資料不得進入 daily v1 ranking。Phase 2 初期只能作為背景 label 或 detail context。
3. Daily run 不得對全市場逐檔做 expensive API calls，包括週頻大戶、借券與完整融資融券逐檔即時查詢。
4. 輸出只能使用觀察語言，描述 setup、資料證據、風險標籤與追蹤重點，不產生交易指令、價格承諾或保證性語句。
5. `observation_score` 是內部排序、校準與 trace 用途，不可包裝成勝率或保證結果。
6. Missing data 必須明確標示，不可以用 minimal margin `data_date` 暗示完整融資融券已取得。
7. Shared evidence/context contract 不可包含 Daily Radar 專用 UI 假設、portfolio action 假設或交易指令語言；risk background labels 只能描述 context、caveat 與資料證據。
8. Shared evidence/context 可以被 `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 引用，但只能作為 evidence/context/caveat；不可單獨變成 recommended action、portfolio action、lifecycle verdict 或 LLM 覆寫依據。
9. Consumers 必須讀 shared cache/repository，不可在各自流程中觸發全市場或逐檔昂貴即時查詢。

## 7. 驗證計劃與建議測試檔案

Phase 1 驗證重點是 deterministic、可回放與資料足夠時的排序改善：

1. Universe tests：確認 multi-track universe 來源、去重、排序與 track trace。
2. Market context tests：確認 market index wiring、regime v1、空資料降級與 data date trace。
3. Relative strength tests：確認候選與大盤基準在不同 lookback window 下計算正確。
4. Scoring trace tests：確認 scoring version、rule version、matched rules、score breakdown 與 input snapshot 被保存。
5. Backtest calibration tests：確認校準報表能重跑，且改版可追蹤。
6. Shared evidence/context tests：確認 reusable schema/repository seam 可表示 source、as_of_date、freshness、missing reason、replay key 與 applicable consumers。
7. Consumer read tests：確認 `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 可讀取 shared context，且 missing/stale context 非阻塞。

建議測試檔案：

| Area | Suggested test file |
| ---- | ------------------- |
| Universe tracks | `backend/tests/daily_radar/test_universe.py` |
| Market context | `backend/tests/daily_radar/test_market_context.py` |
| Relative strength | `backend/tests/daily_radar/test_relative_strength.py` |
| Scoring trace | `backend/tests/daily_radar/test_scoring_trace.py` |
| Service integration | `backend/tests/daily_radar/test_service.py` |
| Backtest calibration | `backend/tests/daily_radar/test_backtest_calibration.py` |
| Background chip context | `backend/tests/daily_radar/test_chip_context_cache.py` |
| API response contract | `backend/tests/api/test_daily_radar.py` |
| Individual analysis shared context | `backend/tests/test_api.py` |
| Position analysis shared context | `backend/tests/test_position_scorer.py` |
| Portfolio shared context | `backend/tests/test_portfolio_router.py` |
| Lifecycle shared context | `backend/tests/test_position_lifecycle_analysis.py` |

Phase 2 驗證重點是 cache separation 與 request budget：

1. 背景排程可單獨更新 chip context，失敗時不阻塞 daily run。
2. Daily run 只讀背景 cache，不對全市場逐檔呼叫昂貴資料源。
3. 週頻大戶 label 顯示後不改變 Phase 1 ranking。
4. API response 可清楚表示 context type、as_of_date、freshness 與 missing reason。
5. 背景 context label 移除後不改變 ranking，且 shared contract 不直接產生 portfolio action、recommended action 或交易建議。
6. `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 的 shared context usage 均有測試覆蓋，不觸發昂貴即時抓取。

## 8. 分階段推出順序

| Order | Step | Scope | Success criteria |
| ----- | ---- | ----- | ---------------- |
| 1 | Instrument current baseline | 保存現有 ranking、candidate trace、request counts、existing evidence/context seams 與 known gaps。 | 可用同一日期重跑並比較 v1 baseline。 |
| 2 | Shared evidence/context contract | 定義 replayable signal evidence 與 risk background labels 的 consumer-neutral 命名、trace key、applicable consumers 與資料 seam。 | Daily Radar 可先使用，schema 可支援其他 consumers。 |
| 3 | Market index wiring | 補上 `market_context` 與 market regime v1。 | Live run trace 不再出現空 market context，market weakness 可產生風險標籤。 |
| 4 | Multi-track universe | 新增日頻技術與量價軌道。 | 候選來源更多元，且每筆 candidate 可追溯 track。 |
| 5 | Relative strength | 加入相對大盤強弱。 | 排名與 trace 可反映候選是否強於大盤。 |
| 6 | Backtest calibration | 用歷史資料校準權重與 cutoff。 | 有 calibration report，並能固定 scoring version。 |
| 7 | Phase 1 release gate | 完成 service integration、API contract 與 manual daily run 驗證。 | Daily run 成功、request budget 合格、候選 trace 完整。 |
| 8 | Background chip cache | 建立 Phase 2 shared cache、table、schedule 與 read interface。 | 背景更新與 daily run / analysis flows 解耦。 |
| 9 | Daily Radar background labels | 接上週頻大戶、借券與 full margin context label。 | Detail view 顯示背景資料，但 ranking 不依賴週頻大戶資料。 |
| 10 | Individual analysis consumers | `/analyze` 與 `/analyze/position` 引用 shared context。 | 個股分析與持股分析可顯示 evidence/caveat，不覆寫 rule-based fields。 |
| 11 | Portfolio and lifecycle consumers | Portfolio diagnosis 與 lifecycle review 引用 shared context。 | Portfolio diagnosis 不產生 action；lifecycle review 不使用 future data。 |

## 9. 成功標準

Phase 1 成功標準：

1. Daily Radar 能產生比現有雙軌法人 universe 更完整的候選來源分布。
2. 每筆候選都有 market regime、relative strength、matched rules、score breakdown、data dates 與 scoring version。
3. Backtest calibration 能指出規則調整對排名品質、過熱排除與 bucket 分布的影響。
4. Live run 不需要 LLM，也不增加全市場逐檔昂貴呼叫。
5. Replayable signal evidence 的命名與 trace 欄位不綁死 Daily Radar UI，並可在本輪 Phase 2 被其他分析 workflow 以 read/reference 方式讀取。

Phase 2 成功標準：

1. 背景 chip context 有獨立 cache、table、schedule 與 freshness trace。
2. 週頻大戶資料可先作為背景 label 顯示，不驅動 daily score。
3. Lending 與 full margin context 可在缺資料時明確標示，不污染 daily trigger signals。
4. Daily run 的外部 request pattern 維持可控，昂貴資料更新由背景流程處理。
5. Risk background labels 使用 consumer-neutral contract，Daily Radar 是第一個 foundation consumer，且 `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 都能以 read/reference 方式引用同一套 shared context。
6. 其他 consumers 引用 shared context 時不改變 deterministic scoring、recommended action、portfolio action 或 lifecycle verdict，只增加 evidence/caveat 與資料完整度 trace。

## 10. 非目標

1. 本需求不要求改寫既有 Daily Radar 長期 spec。
2. 本需求不宣稱 Daily Radar v2 已完成實作。
3. 本需求不新增交易指令、價格承諾或保證性績效語言。
4. 本需求不要求 Daily run 直接掃完整市場並逐檔呼叫昂貴 API。
5. 本需求不把週頻大戶資料混入 Phase 1 daily ranking。
6. 本需求不要求 redesign `/analyze`、`/analyze/position`、持股診斷或 lifecycle review UI；只要求在既有 API/response 或 detail payload 中可引用 shared evidence/context。
7. 本需求不把 shared risk background labels 轉換成 portfolio action、recommended action、lifecycle verdict 或交易建議。
