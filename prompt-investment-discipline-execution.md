# Investment Discipline Execution Master Prompt

---

## Master Prompt

```text
你現在要執行 AI Stock Sentinel 的 Investment Discipline Execution Plan。

請先閱讀以下文件，並以它們作為本任務的唯一計劃來源：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/plans/2026-06-11-investment-discipline-execution-commands.md
- docs/specs/daily-stock-radar-spec.md
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- docs/plans/2026-06-11-daily-radar-v2-phase-1d-backtest-calibration.md

產品定位：
AI Stock Sentinel 不是 AI 薦股工具、AI 投顧、買賣建議引擎或保證勝率掃描器。
它應被執行成「台股研究紀律系統」：每日 setup radar、建倉策略脈絡紀錄、持股風險監控、可回放交易復盤。

總目標：
讓系統未來能把每筆操作結果分類為：
1. 策略品質問題：setup/rule 沒有足夠 forward behavior。
2. 執行紀律問題：使用者違反原始紀錄的建倉/加碼/防守計劃。
3. 風險管理問題：倉位大小、防守參考或 portfolio exposure 不合理。
4. 資料品質問題：資料 missing、stale 或 insufficient。
5. 技術分析外事件：結果由當時 available technical/chip/context evidence 無法捕捉的外部事件主導。

共同硬性規則：
1. 一次只執行一個 phase。完成後停止，回報結果，不要自行進入下一 phase。
2. 不使用 LLM 產生、修改或覆寫 ranking、bucket 判定、score、risk state、portfolio action、validation metric 或 rule governance 結論。
3. 不把 validation report 包裝成勝率承諾、目標價、投資承諾或公開績效宣傳。
4. 不使用 future data 回填 signal date、entry date 或 exit date 當時不可知的證據。
5. 不把 missing data 當成 neutral evidence；必須保留 missing/stale reason。
6. 不新增交易命令語言；使用 observation、risk state、discipline trigger、data caveat、risk-control reference。
7. 保持 backward compatibility；不得刪除既有相容欄位，除非該 phase 明確要求 migration。
8. 若修改正式 spec，只能記錄已實作且測試穩定的事實；不可把 planned behavior 寫成既定 contract。
9. 若發現計劃文件與目前程式碼不一致，先回報差異與最小安全調整，不要自行擴大 scope。

階段選擇規則：
- 如果使用者沒有指定 phase，先執行 Phase 0: Baseline Readiness Check。
- 如果 Phase 0 已完成且回報 go，下一個預設 phase 是 Phase 1: Forward Outcome Validation。
- 若使用者明確指定 phase，僅執行該 phase。

完成回報格式：
Phase completed:
- Phase:
- Scope:
- Changed files:
- Behavioral changes:
- Backward compatibility:
- Verification:
- Manual QA:
- Data or sample limitations:
- Residual risks:
- Next recommended phase:
```

---

## Phase 0 Prompt: Baseline Readiness Check

貼這段給新 session 時，agent 只會檢查狀態，不會改程式。

```text
請執行 Investment Discipline Execution Plan 的 Phase 0: Baseline Readiness Check。

請閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/specs/daily-stock-radar-spec.md
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/plans/2026-06-11-daily-radar-v2-phase-1d-backtest-calibration.md
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/calibration.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/shared_context.py

請只回報：
1. Daily Radar candidate trace 是否足夠支援 forward validation。
2. shared background context 是否已和 daily trigger signal 分離。
3. 是否有未完成 worktree 變更會干擾 Phase 1。
4. Phase 1 的 go/no-go 結論。
5. 若 no-go，列出最小 blocker fix。

限制：
- 不要實作 Phase 1。
- 不要修改任何檔案，除非使用者明確要求建立 readiness note。
- 不要跑需要外部網路或 live market data 的流程。
```

---

## Phase 1 Prompt: Forward Outcome Validation

這是第一個建議實作 phase。它的目的不是提高分數，而是建立「候選 setup 後來到底表現如何」的可回放驗證。

```text
請執行 Investment Discipline Execution Plan 的 Phase 1: Forward Outcome Validation。

本 phase 目標：
建立 deterministic offline validation workflow，衡量 Daily Radar candidates 在 5/10/20 個 trading days 後的 forward behavior。這是 rule-quality / calibration 診斷，不是績效宣傳，不改 live scoring。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/plans/2026-06-11-investment-discipline-execution-commands.md
- docs/plans/2026-06-11-daily-radar-v2-phase-1d-backtest-calibration.md
- docs/specs/daily-stock-radar-spec.md
- backend/src/ai_stock_sentinel/daily_radar/calibration.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/relative_strength.py
- backend/src/ai_stock_sentinel/daily_radar/schemas.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/scripts/daily_radar_calibration.py
- backend/tests/test_daily_radar_calibration.py
- backend/tests/test_daily_radar_repository.py
- backend/tests/test_daily_radar_scoring.py

必須遵守：
1. 不可改 live scoring、ranking、bucket 判定、risk penalty、scoring version 或 rule version。
2. 不可把 validation output 命名或呈現成 user-facing win rate。
3. 不可使用 signal date 之後的資料重建當日入選原因。
4. 不可靜默丟掉 missing future price、missing benchmark、stale price；必須列 skip reason。
5. 不可引入 LLM。
6. 不可刪除或弱化現有 calibration tests。

必做範圍：
1. 新增 forward validation module/script，例如：
   - backend/src/ai_stock_sentinel/daily_radar/forward_validation.py
   - backend/scripts/daily_radar_forward_validation.py
2. 支援 fixture mode 與 db mode。若 db mode 缺資料，必須回報 skip reasons。
3. 支援 windows：5、10、20 trading days。
4. 至少計算：
   - forward_return_pct
   - excess_return_vs_benchmark_pct
   - max_favorable_excursion_pct
   - max_adverse_excursion_pct
   - close_below_defense_reference
   - hit_rate_above_threshold（diagnostic only）
   - profit_factor_like_ratio
   - sample_count
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
   - metadata
   - sample_summary
   - bucket_outcomes
   - rule_outcomes
   - risk_label_outcomes
   - market_regime_outcomes
   - score_decile_outcomes
   - ablation_candidates
   - skip_reasons
   - version_manifest
8. 新增文件說明此 report 是 rule-quality / calibration 診斷，不是績效宣傳。

建議 command：
cd backend
uv run python scripts/daily_radar_forward_validation.py \
  --market TW \
  --start-date 2026-06-01 \
  --end-date 2026-06-30 \
  --windows 5,10,20 \
  --output /tmp/daily-radar-forward-validation.json

測試要求：
- Unit tests for forward return、MFE、MAE、benchmark excess return。
- Fixture tests for missing candidate price、missing benchmark、stale/future data gap。
- Grouping tests：bucket/rule/risk label aggregation 穩定。
- Snapshot test：fixture report deterministic、sorted keys、無 wall-clock timestamp。
- 確認 Phase 1 不改 live scoring version 或 live ranking behavior。

完成後停止。不要執行 Phase 2、3、4、5、6。

完成後回報：
- 新增 command/module。
- report shape。
- fixture report 摘要。
- db mode 是否可用與限制。
- 跑了哪些測試/QA。
- 是否確認 live scoring 未變。
- 下一個建議 phase。
```

---

## Phase 2 Prompt: Entry Strategy Context Audit And Gap Closure

這個 phase 不是從零建立 entry context。現有 backend/frontend/lifecycle review 已有不少實作；本 phase 的任務是先 audit，再只補缺口。

```text
請執行 Investment Discipline Execution Plan 的 Phase 2: Entry Strategy Context Audit And Gap Closure。

本 phase 目標：
核對既有 entry strategy context 實作是否已端到端滿足需求，並只補仍缺的 seam。不要重複實作已存在的欄位、migration、endpoint 或 UI 流程。

已知目前可能已存在的實作：
- EntryRecordContext 定義 entry_reason、planned_holding_period、default_stop_rule、add_entry_condition、note。
- POST /portfolio 接 entry_record，建立 PositionEvent，並在有 lifecycle-plan 欄位時建立 PositionLifecyclePlan。
- PositionEvent / PositionLifecyclePlan 有 source/provenance 與 created_after_entry。
- Frontend Analyze / Portfolio / Closed Portfolio 已有 entry plan、backfill、add-entry、decision_context 顯示。

本 phase 要回答：
1. 所有入口是否真的把 fixed-option strategy context 正確送到後端。
2. optional note 是否只作 secondary evidence，而不是主要判斷來源。
3. user_recorded_at_event_time / user_backfilled / created_after_entry 是否在新建倉、加碼、回填、lifecycle review 中語意一致。
4. lifecycle review 是否能用既有 context 分辨 strategy failure、execution deviation、data issue、outside-technical-analysis event。
5. 若上述已完整，請不要改程式，只更新/補充驗證或回報 no-op 結論。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/portfolio/entry_record_contract.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/analysis/position_lifecycle.py
- backend/src/ai_stock_sentinel/db/models.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_entry_record_contract.py
- backend/tests/test_portfolio_router.py
- backend/tests/test_position_lifecycle_analysis.py

必做範圍：
1. Audit 既有新建倉是否捕捉：
   - entry_reason
   - planned_holding_period
   - default_stop_rule
   - add_entry_condition
2. Audit add-entry、backfill、lifecycle review 是否正確使用上述 context。
3. 確認 optional note 只能作 secondary evidence，不可取代固定欄位。
4. 確認 provenance 語意一致：
   - user_recorded_at_event_time
   - user_backfilled
   - synthetic_from_portfolio_row
   - manual_record_correction
   - not_recorded
5. 確認 lifecycle review 使用固定欄位判斷 plan adherence；缺資料時保留 decision_context: insufficient。
6. 若 existing position backfill 已支援，確認標記 created_after_entry = true 或等價欄位，不可假裝是 entry-time plan。
7. 只有 audit 發現缺口時才修改程式；若沒有缺口，回報 no-op 並列出驗證證據。

限制：
1. 不可用 PnL、後續走勢或 LLM 推論原始意圖。
2. 不可因缺少 fixed fields 阻塞 existing lifecycle review。
3. 不可把 backfilled plan 當作 entry-time plan。
4. 不可新增交易命令語言。

測試要求：
- Entry record contract tests。
- Portfolio create/update API tests。
- Lifecycle review decision_context tests。
- Backfilled provenance tests。
- Add-entry plan-adherence tests if add-entry gap is found.
- Frontend build/typecheck，若有 frontend 變更。

完成後停止。不要執行 Phase 3、4、5、6。

完成後回報：
- changed files。
- audit 結論：哪些已存在、哪些補了缺口、哪些 no-op。
- schema/API/UI 變更。
- provenance 策略。
- backward compatibility。
- 驗證命令。
- deferred add-entry/backfill 項目。
- 下一個建議 phase。
```

---

## Phase 3 Prompt: Rule Pruning And Score Governance

```text
請執行 Investment Discipline Execution Plan 的 Phase 3: Rule Pruning And Score Governance。

前提：
Phase 1 已完成，且至少有 deterministic forward validation report 可用。

本 phase 目標：
建立 rule registry、rule tier、validation status 與 ablation workflow，讓 scoring signals 可以被升級、降級、移到 context only 或 deprecated。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- Phase 1 forward validation implementation/report
- docs/specs/daily-stock-radar-spec.md
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/explanations.py
- backend/src/ai_stock_sentinel/daily_radar/types.py
- backend/src/ai_stock_sentinel/daily_radar/schemas.py
- backend/src/ai_stock_sentinel/daily_radar/calibration.py
- backend/tests/test_daily_radar_scoring.py
- backend/tests/test_daily_radar_explanations.py
- backend/tests/test_daily_radar_calibration.py

必須遵守：
1. 不可主觀宣稱 signal 有效；必須引用 validation/ablation evidence 或標記 insufficient sample。
2. 不可把 low sample signal 升為 driver。
3. 不可新增未驗證 scoring driver；新 signal 預設只能是 context_only 或 confirming_evidence。
4. 若 live scoring 改變，必須 bump scoring/rule version 並更新 tests/fixtures/spec notes。
5. 不可用 LLM 決定 rule tier。

必做範圍：
1. 新增 rule registry，例如 backend/src/ai_stock_sentinel/daily_radar/rule_registry.py。
2. Registry 至少包含 rule code、description、tier、owner module、validation status、first version、last reviewed version。
3. 支援 tiers：driver、confirming_evidence、risk_modifier、context_only、deprecated。
4. 新增 rule validation status seam。
5. 新增 ablation workflow/script。
6. Ablation 最少覆蓋：news sentiment、fundamental valuation、MFI、OBV、KD、Donchian、institutional flow、margin-related risk labels、relative strength、market regime penalty。
7. 將 scoring/explanations 中的 rules 與 registry 對齊。

驗收目標：
1. 每個 scoring driver 都有 registry entry。
2. 每個 rule 都有 tier 與 validation status。
3. deprecated/context_only rule 不影響 score。
4. ablation report deterministic，且列出 sample count、delta、insufficient-sample cases。
5. 若 live scoring 有變，必須有 scoring/rule version bump、changelog、fixture/test 更新。
6. API/candidate trace 足以解釋 historical rule version 與 rule code。

測試要求：
- Rule registry coverage tests。
- Governance tests：deprecated/context_only 不影響 score。
- Ablation report fixture/snapshot tests。
- Existing Daily Radar scoring/explanation/calibration tests。

完成後停止，不要執行 Phase 4、5、6。

完成後回報：
- 新增 registry/ablation 檔案。
- 是否改 live scoring。
- version bump / changelog。
- ablation 摘要。
- 跑了哪些測試/QA。
- 下一個建議 phase。
```

---

## Phase 4 Prompt: Risk Language Alignment

```text
請執行 Investment Discipline Execution Plan 的 Phase 4: Risk Language Alignment。

本 phase 目標：
統一使用者可見語言，讓 Daily Radar、單股分析、持股診斷、portfolio diagnosis、single trade review、lifecycle review 都使用研究/紀律語言，而不是買賣指令語言。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/specs/daily-stock-radar-spec.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/ai-stock-sentinel-position-diagnosis-spec.md
- backend/src/ai_stock_sentinel/api.py
- backend/src/ai_stock_sentinel/analysis/position_scorer.py
- backend/src/ai_stock_sentinel/analysis/trade_review.py
- backend/src/ai_stock_sentinel/analysis/position_lifecycle.py
- frontend/src/pages/AnalyzePage.tsx
- frontend/src/pages/DailyRadarPage.tsx
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/pages/ClosedPortfolioPage.tsx

必須遵守：
1. 不可改 Daily Radar ranking、bucket、score 或 candidate sorting。
2. 不可改 position diagnosis deterministic 行為。
3. 不可刪除 recommended_action、trailing_stop、exit_reason 等既有相容欄位。
4. 不可把 Hold、Trim、Exit 當作 primary user-facing copy。

必做範圍：
1. 新增 additive risk-language 欄位，例如 risk_state、discipline_triggers、observation_conditions、risk_control_reference。
2. primary UI copy 改為風險狀態、紀律觸發、觀察條件、資料 caveat。
3. legacy/internal compatibility fields 保留並標示為 secondary。
4. 建立 copy guard 或 allowlisted string scan。

驗收目標：
1. Daily Radar、Analyze、Portfolio、Closed Portfolio 主要使用者表面不以買/賣/加碼/減碼/出場作為 primary copy。
2. legacy fields 仍存在，不破壞既有 API/client。
3. 新 risk-language 欄位可被 frontend 或 API consumer 使用。
4. copy guard 能防止 command-language 回到 primary user-facing surfaces。

測試要求：
- Backend API tests 覆蓋新增欄位與 backward compatibility。
- Frontend build/typecheck，若有 frontend 變更。
- Copy guard 或 allowlisted string scan。
- Existing Daily Radar / portfolio / review tests 不因 copy migration 失敗。

完成後停止，不要執行 Phase 5、6。

完成後回報：
- changed files。
- 哪些 command language 被替換。
- 相容欄位策略。
- 跑了哪些測試/QA。
- 仍需後續 migration 的欄位。
- 下一個建議 phase。
```

---

## Phase 5 Prompt: Portfolio Risk Layer

```text
請執行 Investment Discipline Execution Plan 的 Phase 5: Portfolio Risk Layer。

本 phase 目標：
新增 read-only portfolio risk summary，讓產品從單檔分析升級為 portfolio-level risk discipline。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/portfolio/fees.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/services/history_loader.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py

必須遵守：
1. Read-only only：不可建立、修改、刪除持股或交易事件。
2. 不可輸出 buy/sell/add/trim/exit 指令。
3. 不可把 portfolio risk diagnostics 轉成 recommended action。
4. 不可使用 LLM。
5. Missing price、missing defense reference、zero quantity、stale data 必須有 data quality caveat。
6. 若 sector/theme data 不可靠，先不要做 sector concentration，不可硬編分類。

必做範圍：
1. 新增 deterministic portfolio risk module。
2. 新增 additive endpoint：GET /portfolio/risk-summary。
3. Response 至少包含 portfolio_value、total_unrealized_pnl、total_at_risk、position_risks、concentration、shared_exposures、risk_budget_status、data_quality。
4. 每筆 position risk 至少包含 symbol、quantity、current_price、entry_price、market_value、defense_reference、estimated_risk_amount、estimated_risk_pct_of_portfolio、risk_state、discipline_triggers、data_quality。

驗收目標：
1. Risk summary deterministic，且可由 stored portfolio rows plus market data 解釋。
2. endpoint 只讀取當前 user active positions，user scoping 有測試。
3. missing price、zero quantity、missing defense reference、stale data 產生 data-quality caveat，不捏造風險。
4. API/frontend 不產生 portfolio action 或交易命令。
5. 若 sector/theme data 不可靠，僅做 symbol/setup-bucket/shared-risk concentration，不硬編產業分類。

測試要求：
- Unit tests：position risk amount、portfolio percentage、total at risk、symbol concentration。
- Data gap tests：missing price、zero quantity、missing defense reference、stale data。
- Auth/user scoping tests。
- API contract tests。
- Frontend build/typecheck，若有 frontend 變更。

完成後停止，不要執行 Phase 6。

完成後回報：
- 新增 endpoint/module。
- portfolio value 計算方式。
- risk state 規則。
- 資料不足處理。
- 跑了哪些測試/QA。
- 下一個建議 phase。
```

---

## Phase 6 Prompt: Release Gate And Monitoring

```text
請執行 Investment Discipline Execution Plan 的 Phase 6: Release Gate And Monitoring。

前提：
Phase 1、3、5 已完成，或使用者明確要求先建立 release gate skeleton。

本 phase 目標：
建立 release gate，防止未來重新引入未驗證 scoring driver、signal zoo complexity、命令式投顧語言或 portfolio action 誤用。

必做範圍：
1. 建立 release gate checklist。
2. 新增或整理 automated checks：
   - rule registry coverage
   - deprecated/context_only 不影響 score
   - copy guard with allowlist
   - validation report deterministic snapshot
   - portfolio risk data-gap behavior
3. 文檔化 verifier commands。
4. 更新 specs/docs，只同步完成狀態與 release gate 要求。

限制：
1. 不可新增 scoring/ranking/portfolio features。
2. 不可把 validation metrics 改成 public win-rate claims。
3. 不可把 background context 或 portfolio risk 轉成 trading command。

驗收目標：
1. Release gate checklist 明確列出 scoring signal、rule tier、validation evidence、copy guard、version bump、portfolio data gaps。
2. Automated checks 能防止沒有 registry entry 的 scoring driver。
3. Copy guard 以 allowlist 區分 primary copy 和 deprecated compatibility field docs。
4. Validation report snapshot 仍 deterministic。
5. Portfolio risk data-gap behavior 被測試覆蓋。
6. Specs/docs 只同步已完成且測試穩定的 facts。

測試要求：
- Release gate tests 通過。
- Rule governance tests 通過。
- Copy guard 通過。
- Portfolio risk tests 覆蓋 missing/stale data。
- Frontend build/typecheck，若 frontend copy guard 或 UI 有變更。

完成後停止。

完成後回報：
- release gate 規則。
- automation/checks。
- spec sync 內容。
- 跑了哪些測試/QA。
- deferred items。
- 下一輪建議。
```
