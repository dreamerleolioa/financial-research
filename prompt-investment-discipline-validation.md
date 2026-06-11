# Investment Discipline Validation And Risk Copy-Paste Prompts

這份文件提供 `docs/plans/2026-06-11-investment-discipline-validation-roadmap.md` 的分階段實作指令。每次只貼一個 phase 給實作代理，完成後停止並回報驗證結果，不要一次執行多個 phase。

前提：本 prompt series 應在目前 Daily Radar v2 optimization 到達穩定 checkpoint 後再開始。若 current worktree 仍有 Daily Radar v2 未完成變更，先完成、提交或明確暫停該工作，再執行本系列。

共通定位：AI Stock Sentinel 應定位為台股研究紀律系統：每日 setup radar、持股風險監控與可回放交易復盤。不得把產品改造成 AI 薦股、AI 投顧、買賣建議引擎、保證勝率掃描器。

共通原則：

1. 不使用 LLM 產生、修改或覆寫 ranking、bucket 判定、score、risk state、portfolio action、validation metric 或 rule governance 結論。
2. 不把 validation report 包裝成保證勝率、目標價、投資承諾或公開績效宣傳。
3. 不用 future data 回填 signal date、entry date 或 exit date 當時不可知的證據。
4. 不把 missing data 當成 neutral evidence；必須保留 missing/stale reason。
5. 不新增交易命令語言；使用 observation、risk state、discipline trigger、data caveat、risk-control reference。
6. 所有 phase 都要保持 backward compatibility，除非該 phase 明確要求 deprecation migration。
7. 若需要修改正式 spec，只能記錄已實作且測試穩定的事實，不可把 planned behavior 寫成既定 contract。

## Phase A - Language Boundary Alignment

```text
COPY-PASTE PROMPT: Phase A - Language Boundary Alignment

請只執行 Investment Discipline Validation And Risk Roadmap 的 Phase A: Language Boundary Alignment。

本階段目標：統一使用者可見語言，讓 Daily Radar、單股分析、持股診斷、portfolio diagnosis、single trade review、lifecycle review 都使用研究/紀律語言，而不是買賣指令語言。本階段可新增 additive API 欄位與 UI copy migration，但不可刪除現有相容欄位。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/ai-stock-sentinel-position-diagnosis-spec.md
- docs/plans/2026-06-04-single-trade-review-analysis.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/api.py
- backend/src/ai_stock_sentinel/analysis/strategy_generator.py
- backend/src/ai_stock_sentinel/analysis/position_scorer.py
- backend/src/ai_stock_sentinel/analysis/trade_review.py
- backend/src/ai_stock_sentinel/analysis/position_lifecycle.py
- frontend/src/pages/AnalyzePage.tsx
- frontend/src/pages/DailyRadarPage.tsx
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/pages/ClosedPortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- frontend/src/lib/dailyRadarTypes.ts

本階段必須遵守：
1. 不可改 Daily Radar ranking、bucket、score 或 candidate sorting。
2. 不可改 position diagnosis 的 deterministic 行為；只能新增風險語言欄位或改 primary UI copy。
3. 不可刪除 `recommended_action`、`trailing_stop`、`exit_reason` 等既有相容欄位，除非已有明確 migration plan 與測試。
4. 不可把 `Hold`、`Trim`、`Exit` 當作 primary user-facing copy。
5. 不可新增 broker execution、交易下單、個人化投資建議或 suitability 流程。
6. 不可讓 LLM 產生交易命令語言。

本階段必做範圍：
1. Audit 後端 response、前端 labels、generated explanations、docs/specs 中的 primary user-facing command language。
2. 新增或整理 additive risk-language 欄位，例如：
   - `position_analysis.risk_state`
   - `position_analysis.risk_state_label`
   - `position_analysis.discipline_triggers`
   - `position_analysis.observation_conditions`
   - `position_analysis.risk_control_reference`
   - `position_analysis.command_language_deprecated`
3. 將 primary UI copy 從「建議買/賣/加碼/減碼/出場」改為「風險狀態、紀律觸發、觀察條件、資料 caveat」。
4. 保留 legacy/internal compatibility fields，但文件標記為 compatibility 或 secondary。
5. 建立 copy guard：用 tests 或 allowlisted string scan 防止 primary user-facing copy 回到命令語言。
6. 更新必要 spec/docs，只同步本階段已完成的語言邊界與相容欄位策略。

建議詞彙方向：
- `Hold` → `risk_state: stable` 或 `position_condition: intact`
- `Trim` → `risk_state: elevated` + `discipline_trigger: profit_protection_review`
- `Exit` → `risk_state: critical` + `discipline_trigger: defense_line_breached`
- `entry_zone` → observation zone
- `stop_loss` → risk-control reference 或 defense reference
- `recommended_action` → risk state / triggered conditions / review prompts

TDD / 測試要求：
- Backend API tests 覆蓋 position analysis 新增欄位與 backward compatibility。
- Frontend typecheck/build 通過。
- Copy guard 測試或 script 能找出 primary surfaces 的 banned command terms；允許 docs/specs 中定義 deprecated compatibility fields。
- Existing Daily Radar / portfolio / review 相關 tests 不應因 copy migration 失敗。

Manual QA：
- 打開或檢查 Analyze、Daily Radar、Portfolio、Closed Portfolio 主要使用者流程，確認 primary copy 不再像交易命令。
- 抽查 API response：legacy fields 仍存在，新 risk-language fields 可用。

完成後停止。不要執行 Phase B、C、D 或 E。

完成後請回報：改了哪些檔案、哪些 command language 被替換、相容欄位策略、跑了哪些測試/QA、仍需後續 migration 的欄位、下一個建議 phase。
```

## Phase B - Forward Outcome Validation

```text
COPY-PASTE PROMPT: Phase B - Forward Outcome Validation

請只執行 Investment Discipline Validation And Risk Roadmap 的 Phase B: Forward Outcome Validation。

本階段目標：建立 deterministic offline validation workflow，衡量 Daily Radar candidates 在 5/10/20 個交易日後的 forward return、benchmark excess return、MFE、MAE、drawdown、risk-control reference breach 等結果。本階段只產出校準/研究報告，不改 live scoring。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/plans/2026-06-11-daily-radar-v2-phase-1d-backtest-calibration.md
- docs/specs/daily-stock-radar-spec.md
- backend/src/ai_stock_sentinel/daily_radar/calibration.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/relative_strength.py
- backend/src/ai_stock_sentinel/daily_radar/schemas.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/scripts/daily_radar_calibration.py
- backend/scripts/backtest_win_rate.py
- backend/tests/test_daily_radar_calibration.py
- backend/tests/test_daily_radar_repository.py
- backend/tests/test_daily_radar_scoring.py

本階段必須遵守：
1. 不可改 live scoring、ranking、bucket 判定或 risk penalty。
2. 不可把 validation output 寫成 user-facing win rate。
3. 不可使用 signal date 之後的資料重建當日入選原因。
4. 不可靜默丟掉 missing future price、missing benchmark、stale price；必須列 skip reason。
5. 不可引入 LLM。
6. 不可刪除或弱化現有 calibration tests。

本階段必做範圍：
1. 新增 forward validation module/script，例如：
   - `backend/src/ai_stock_sentinel/daily_radar/forward_validation.py`
   - `backend/scripts/daily_radar_forward_validation.py`
2. 支援 fixture mode 與 db mode。若 db mode 缺資料，必須回報 skip reasons。
3. 支援 windows：5、10、20 trading days。
4. 至少計算：
   - `forward_return_pct`
   - `excess_return_vs_benchmark_pct`
   - `max_favorable_excursion_pct`
   - `max_adverse_excursion_pct`
   - `close_below_defense_reference`
   - `hit_rate_above_threshold`（diagnostic only）
   - `profit_factor_like_ratio`
   - `sample_count`
5. 至少按以下 dimensions 分組：
   - primary bucket
   - secondary bucket
   - matched rule code
   - risk label
   - market regime
   - relative strength bucket
   - repeat status
   - score decile
   - data freshness status
6. 輸出 stable JSON，sorted keys，無 wall-clock timestamp。
7. Report shape 至少包含：
   - `metadata`
   - `sample_summary`
   - `bucket_outcomes`
   - `rule_outcomes`
   - `risk_label_outcomes`
   - `market_regime_outcomes`
   - `score_decile_outcomes`
   - `ablation_candidates`
   - `skip_reasons`
   - `version_manifest`
8. 新增文件說明此 report 是 rule-quality/calibration 診斷，不是績效宣傳。

建議 command：
```bash
cd backend
uv run python scripts/daily_radar_forward_validation.py \
  --market TW \
  --start-date 2026-06-01 \
  --end-date 2026-06-30 \
  --windows 5,10,20 \
  --output /tmp/daily-radar-forward-validation.json
```

TDD / 測試要求：
- Unit tests for forward return、MFE、MAE、benchmark excess return。
- Fixture tests for missing candidate price、missing benchmark、stale/future data gap。
- Grouping tests：bucket/rule/risk label aggregation 穩定。
- Snapshot test：fixture report deterministic、sorted keys、無 wall-clock timestamp。
- 確認 Phase B 不改 live scoring version 或 live ranking behavior。

Manual QA：
- 執行 fixture forward validation command，回報 report 摘要與 sample/skip reason。
- 若 db mode 可用，執行小日期區間 dry run；若資料不足，回報不足原因。

完成後停止。不要執行 Phase C、D 或 E。

完成後請回報：新增 command/module、report shape、fixture report 摘要、是否有 db mode 限制、跑了哪些測試/QA、下一個建議 phase。
```

## Phase C - Rule Pruning And Score Governance

```text
COPY-PASTE PROMPT: Phase C - Rule Pruning And Score Governance

請只執行 Investment Discipline Validation And Risk Roadmap 的 Phase C: Rule Pruning And Score Governance。

前提：Phase B 已完成，至少有 deterministic forward validation report 可用。

本階段目標：建立 rule registry、rule tier、validation status 與 ablation workflow，讓 scoring signals 可以被升級、降級、移到 context only 或 deprecated。本階段可以新增 governance infrastructure；只有在 validation evidence 足夠、測試完整、version bump 明確時，才可以調整 live scoring。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- Phase B forward validation implementation/report
- docs/specs/daily-stock-radar-spec.md
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/explanations.py
- backend/src/ai_stock_sentinel/daily_radar/types.py
- backend/src/ai_stock_sentinel/daily_radar/schemas.py
- backend/src/ai_stock_sentinel/daily_radar/calibration.py
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_explanations.py
- backend/tests/test_daily_radar_calibration.py

本階段必須遵守：
1. 不可主觀宣稱某個 signal 有效；必須引用 validation/ablation evidence 或標記為 insufficient sample。
2. 不可把 low sample signal 升為 driver。
3. 不可新增未驗證 scoring driver；新 signal 預設只能是 `context_only` 或 `confirming_evidence`。
4. 若 live scoring 改變，必須 bump scoring/rule version 並更新 tests/fixtures/spec notes。
5. 不可用 LLM 決定 rule tier。
6. 不可刪除 failing tests 來配合 rule change。

本階段必做範圍：
1. 新增 rule registry，例如 `backend/src/ai_stock_sentinel/daily_radar/rule_registry.py`。
2. Registry 至少包含：rule code、description、tier、owner module、validation status、first version、last reviewed version。
3. 支援 rule tiers：
   - `driver`
   - `confirming_evidence`
   - `risk_modifier`
   - `context_only`
   - `deprecated`
4. 新增 rule validation status seam，例如 `backend/src/ai_stock_sentinel/daily_radar/rule_validation.py`。
5. 新增 ablation workflow/script，例如 `backend/scripts/daily_radar_rule_ablation.py`。
6. Ablation 最少覆蓋：
   - remove news sentiment
   - remove fundamental valuation
   - remove MFI
   - remove OBV
   - remove KD
   - remove Donchian
   - remove institutional flow
   - remove margin-related risk labels
   - remove relative strength
   - remove market regime penalty
7. 將 scoring/explanations 中的 rules 與 registry 對齊，避免無 rule code 的 driver signal。
8. 如果選擇調整 live scoring，產出清楚 changelog：哪些 rule 被 demote/promote/deprecated，原因是什麼。

TDD / 測試要求：
- Rule registry tests：所有 scoring driver 都有 registry entry。
- Governance tests：deprecated/context_only rule 不影響 score。
- Ablation report fixture tests：輸出 deterministic，且列出 sample count、delta、insufficient-sample cases。
- Scoring tests：若 live scoring 有變，更新 expected score、score breakdown、version trace。
- API/repository tests：candidate trace 可以解釋 historical rule version。

Manual QA：
- 執行 fixture ablation command，回報哪些 signals 建議 demote 或 sample insufficient。
- 抽查一筆 candidate trace：rule code、rule tier、score impact 可讀。

完成後停止。不要執行 Phase D 或 E。

完成後請回報：新增 registry/ablation 檔案、是否改 live scoring、version bump、ablation 摘要、跑了哪些測試/QA、下一個建議 phase。
```

## Phase D - Portfolio Risk Layer

```text
COPY-PASTE PROMPT: Phase D - Portfolio Risk Layer

請只執行 Investment Discipline Validation And Risk Roadmap 的 Phase D: Portfolio Risk Layer。

本階段目標：新增 read-only portfolio risk summary，讓產品從單檔分析升級為 portfolio-level risk discipline。此 phase 不建立交易指令、不改 portfolio rows、不新增 broker execution。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/ai-stock-sentinel-position-diagnosis-spec.md
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/portfolio/fees.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/db/session.py
- backend/src/ai_stock_sentinel/services/history_loader.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py
- backend/tests/test_db_models.py
- backend/tests/test_history_loader.py

本階段必須遵守：
1. Read-only only：不可建立、修改、刪除持股或交易事件。
2. 不可輸出 buy/sell/add/trim/exit 指令。
3. 不可把 portfolio risk diagnostics 轉成 recommended action。
4. 不可使用 LLM。
5. Missing price、missing defense reference、zero quantity、stale data 必須有 data quality caveat。
6. 若 sector/theme data 不可靠，先不要做 sector concentration，不可硬編分類。
7. 必須保留 user scoping，只計算當前使用者 portfolio。

本階段必做範圍：
1. 新增 deterministic portfolio risk module，例如 `backend/src/ai_stock_sentinel/portfolio/risk.py`。
2. 新增 additive endpoint：
   - `GET /portfolio/risk-summary`
3. Response 至少包含：
   - `portfolio_value`
   - `total_unrealized_pnl`
   - `total_at_risk`
   - `total_at_risk_pct`
   - `position_risks`
   - `concentration`
   - `shared_exposures`
   - `risk_budget_status`
   - `data_quality`
4. 每筆 position risk 至少包含：
   - `symbol`
   - `quantity`
   - `current_price`
   - `entry_price`
   - `market_value`
   - `defense_reference`
   - `estimated_risk_amount`
   - `estimated_risk_pct_of_portfolio`
   - `risk_state`
   - `discipline_triggers`
   - `data_quality`
5. 若沒有 reliable defense reference，使用 data caveat，不可捏造。
6. 若沒有 cash/portfolio total value，明確定義 portfolio value 的 MVP 計算方式，例如 active market value sum。
7. Frontend 若新增呈現，使用 risk dashboard/caveat 語言，不做交易 CTA。
8. 更新 specs/docs，只同步 read-only risk summary contract。

TDD / 測試要求：
- Unit tests：position risk amount、portfolio percentage、total at risk、symbol concentration。
- Data gap tests：missing price、zero quantity、missing defense reference、stale data。
- Auth/user scoping tests：只包含當前 user active positions。
- API contract tests：`GET /portfolio/risk-summary` response shape。
- Frontend typecheck/build，若有 frontend 變更。
- Copy guard：portfolio risk UI/API copy 不出現 command-like primary language。

Manual QA：
- 用 fixture portfolio 呼叫 `/portfolio/risk-summary`，確認 output 可解釋且沒有交易命令。
- 若 frontend 有頁面呈現，確認 risk summary 不改任何持股資料。

完成後停止。不要執行 Phase E。

完成後請回報：新增 endpoint/module、portfolio value 計算方式、risk state 規則、資料不足處理、跑了哪些測試/QA、下一個建議 phase。
```

## Phase E - Release Gate And Ongoing Monitoring

```text
COPY-PASTE PROMPT: Phase E - Release Gate And Ongoing Monitoring

請只執行 Investment Discipline Validation And Risk Roadmap 的 Phase E: Release Gate And Ongoing Monitoring。

前提：Phase A、B、C、D 已完成或已明確標示 deferred。

本階段目標：建立 release gate，防止未來重新引入未驗證 scoring driver、signal zoo complexity、命令式投顧語言或 portfolio action 誤用。本階段以測試、文件、CI/check script、spec sync 為主，不新增產品功能。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- Phase A/B/C/D 實作與報告
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/ai-stock-sentinel-position-diagnosis-spec.md
- backend/src/ai_stock_sentinel/daily_radar/
- backend/src/ai_stock_sentinel/portfolio/
- frontend/src/
- backend/tests/
- frontend/package.json
- backend/Makefile

本階段必須遵守：
1. 不可新增 scoring/ranking/portfolio features。
2. 不可把 validation metrics 改成 public win-rate claims。
3. 不可把 background context 或 portfolio risk 轉成 trading command。
4. 不可讓 failing validation/copy/rule governance checks 被 allowlist 掩蓋，除非有明確 deprecated compatibility reason。
5. Spec 只能同步已完成且測試穩定的 facts。

本階段必做範圍：
1. 建立或整理 release gate checklist，覆蓋：
   - New scoring signals require rule-tier classification.
   - New driver signals require validation report reference or experimental flag.
   - User-facing copy cannot introduce buy/sell command language.
   - Scoring/rule changes require version bump and changelog.
   - Portfolio risk changes must handle missing/stale data.
2. 新增或整理 automated checks：
   - rule registry coverage
   - deprecated/context_only 不影響 score
   - copy guard with allowlist
   - validation report deterministic snapshot
   - portfolio risk data-gap behavior
3. 若專案有合適入口，新增 stable verifier command 或文檔化現有 commands。
4. 更新 specs/docs，只同步完成狀態與 release gate 要求。
5. 留下下一輪建議：哪些 signals 需要更多 sample、哪些 portfolio risk dimensions deferred。

建議 commands：
```bash
cd backend
uv run pytest -q \
  tests/test_daily_radar_rule_governance.py \
  tests/test_daily_radar_forward_validation.py \
  tests/test_portfolio_router.py
```

```bash
cd frontend
pnpm build
```

建議 copy scan：
```bash
rg -n "建議買|建議賣|買進|賣出|加碼|減碼|出場|必買|目標價|勝率" frontend/src backend/src docs/specs docs/plans
```

注意：copy scan 需要 allowlist，因為 specs 可能仍需定義 deprecated compatibility fields。Primary user-facing copy 不應命中。

TDD / 驗證要求：
- Release gate tests 通過。
- Copy guard 以 allowlist 區分 primary copy 和 deprecated field docs。
- Rule governance tests 能防止沒有 registry entry 的 scoring driver。
- Portfolio risk tests 覆蓋 missing/stale data。
- Frontend build/typecheck 通過。

Manual QA：
- 抽查 Daily Radar、Analyze、Portfolio、Closed Portfolio 主要頁面 copy。
- 抽查一份 validation report 和一筆 portfolio risk summary，確認沒有績效承諾或交易命令。

完成後停止。

完成後請回報：release gate 規則、automation/checks、spec sync 內容、跑了哪些測試/QA、deferred items、下一輪建議。
```
