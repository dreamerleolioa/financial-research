# Entry Record Optimization Copy-Paste Prompts

## Phase 0 - Requirement And Decision Log

```text
COPY-PASTE PROMPT: Phase 0 - Requirement And Decision Log

請只執行 Entry Record Optimization 的 Phase 0: Requirement And Decision Log。

本階段目標：建立並確認固定選項進場紀錄需求文件，不做任何後端或前端實作。

請先閱讀：
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- docs/specs/README.md
- prompt.md

本階段必須遵守：
1. 不可修改 backend 或 frontend 程式碼。
2. 不可新增 migration。
3. 不可修改 API behavior。
4. 不可把本需求直接 promotion 到 docs/specs，除非使用者明確要求。
5. 不可把固定選項改回自由文字主導。
6. 不可加入 LLM intent inference。
7. 不可從價格走勢、PnL 或後續結果推論使用者原始意圖。

本階段必做範圍：
1. 確認需求文件包含 Why This Exists、Product Principle、Minimal User Input、Fixed Option Taxonomy、Relationship To Existing System、Non-Goals、Data Semantics、Review Semantics、Phased Delivery、Decision Log、Open Questions。
2. 確認最小欄位為：進場理由、持有週期、預設停損、加碼條件。
3. 確認四個欄位皆以固定選項為主。
4. 確認 optional note 只作為補充，不作為主要資料來源。
5. 確認 backfilled plan 不會被視為原始 entry-time intent。
6. 確認需求文件仍放在 docs/plans。

驗收要求：
- 文件語意清楚區分「交易後行為檢討」與「原始策略正確性檢討」。
- 文件明確說明 `decision_context: insufficient` 何時保留。
- 文件沒有要求本階段實作任何程式碼。

完成後停止。不要執行 Phase A、Phase B、Phase C、Phase D、Phase E 或 Phase F。

完成後請回報：修改了哪些文件、是否有 open questions、下一個建議 phase。
```

## Phase A - Fixed Option Taxonomy And Validation Contract

```text
COPY-PASTE PROMPT: Phase A - Fixed Option Taxonomy And Validation Contract
請只執行 Entry Record Optimization 的 Phase A: Fixed Option Taxonomy And Validation Contract。

前提：Phase 0 已完成。

本階段目標：建立固定選項 taxonomy 與 validation contract，讓後續 entry/create/add-entry 流程可以穩定保存結構化決策脈絡。本階段以 TDD 方式先補 contract tests，再實作最小 schema/validation/types。不要做 UI 表單，不要改使用者流程。

請先閱讀：
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_decision_context_persistence.py
- backend/tests/test_portfolio_router.py

本階段必須遵守：
1. 不可破壞現有 /portfolio create/list/update/close 行為。
2. 不可要求既有 active positions 立即補填欄位。
3. 不可自動補 intent-sensitive 欄位。
4. 不可從價格走勢或分析結果推論使用者 intent。
5. 不可加入 LLM。
6. 不可做 entry form UI。
7. 不可做 add-entry flow。
8. 不可改變 Single Trade Review 或 Lifecycle Review 的既有 endpoint 語意。
9. 固定選項必須可被後續 review deterministic 使用。
10. Free-text note 只能是 optional secondary context。

本階段必做範圍：
1. 定義 entry reason allowed values。
2. 定義 planned holding period allowed values。
3. 定義 default stop rule allowed values。
4. 定義 add-entry condition allowed values。
5. 決定這些 values 如何映射到現有 PositionEvent / PositionLifecyclePlan，或是否需要新增欄位。
6. 若新增欄位，需有 migration 與 model tests。
7. 若只使用既有欄位，需有 serializer/schema/type tests 確保 contract 穩定。
8. 更新 frontend shared types，但不做 UI。
9. 保留 `not_recorded` / missing state，用於 existing positions。
10. 不把 optional note 當作主要欄位。

TDD / 測試要求：
- 先新增 failing tests，覆蓋 allowed values。
- invalid option 必須被拒絕。
- missing option 對 existing/backfilled data 不應破壞既有流程。
- `not_recorded` 或 null 不可被自動轉成具體意圖。
- frontend type contract 能表示四個固定欄位與 missing state。
- 現有 test_portfolio_router.py、test_decision_context_persistence.py 仍通過。

完成後停止。不要執行 Phase B、Phase C、Phase D、Phase E 或 Phase F。

完成後請回報：修改了哪些檔案、跑了哪些測試、是否有 pre-existing unrelated failures、下一個建議 phase。
```

## Phase B - Entry Context Capture

```text
COPY-PASTE PROMPT: Phase B - Entry Context Capture
請只執行 Entry Record Optimization 的 Phase B: Entry Context Capture。

前提：Phase A 已完成。

本階段目標：在新增持股 / 首次進場時，讓使用者用固定選項提供四個最小決策欄位：進場理由、持有週期、預設停損、加碼條件。資料必須以 entry-time provenance 保存，供未來 lifecycle review 使用。

請先閱讀：
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/pages/AnalyzePage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py
- backend/tests/test_decision_context_persistence.py
- frontend/package.json

本階段必須遵守：
1. 不可破壞現有新增持股流程。
2. 不可要求 existing active positions 立即補填。
3. 不可把 optional note 當作固定欄位替代品。
4. 不可加入 LLM。
5. 不可從 /analyze 自動建立使用者 intent，除非使用者明確確認。
6. 不可做 add-entry flow。
7. 不可改變 close flow。
8. 不可改變 Single Trade Review endpoint。
9. 若 UI 預填 /analyze action_plan，只能視為 draft，必須由使用者確認後才保存。
10. 新 entry context 必須標記為 user_recorded_at_event_time。

本階段必做範圍：
1. 擴充 portfolio create request，使其可接收四個固定選項。
2. 新增持股時保存 entry event-level reason。
3. 新增或更新 PositionLifecyclePlan，保存 planned_holding_period、default stop rule、add-entry condition 相關資訊。
4. 若 default stop rule 為 system-derived rule，保存 rule value；如有 computed reference price，需明確標示 as-of date。
5. 前端新增持股表單顯示四個固定選項。
6. 表單選項不得是自由文字主導。
7. optional note 可保留，但只作為補充。
8. existing list response shape 若需相容，避免破壞既有 consumers。
9. decision-context-status 應能反映 entry context present/missing。

TDD / 測試要求：
- create portfolio 帶固定選項時，PositionEvent / PositionLifecyclePlan 正確保存。
- create portfolio 不帶固定選項時，既有流程仍可用，且 decision_context 顯示 insufficient 或 missing。
- invalid fixed option 被拒絕。
- optional note 不會覆蓋 fixed option。
- /analyze 預填若有實作，未確認不得保存成 user intent。
- frontend build/typecheck 使用專案既有命令執行。
- manual QA：新增持股可選四個欄位，建立後狀態可讀回。

完成後停止。不要執行 Phase C、Phase D、Phase E 或 Phase F。

完成後請回報：修改了哪些檔案、跑了哪些測試/QA、是否有 pre-existing unrelated failures、下一個建議 phase。
```

## Phase C - Add-Entry Context Capture

```text
COPY-PASTE PROMPT: Phase C - Add-Entry Context Capture
請只執行 Entry Record Optimization 的 Phase C: Add-Entry Context Capture。

前提：Phase B 已完成。

本階段目標：新增明確的 add-entry flow，讓使用者加碼時用固定選項記錄加碼原因與是否符合原本加碼條件。不得從編輯持股資料推論 add_entry。

請先閱讀：
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py
- backend/tests/test_position_lifecycle_analysis.py
- frontend/package.json

本階段必須遵守：
1. 不可把 PUT /portfolio/{id} 的成本、日期、數量修改推論成 add_entry。
2. add_entry 必須由明確 add-entry action 建立。
3. 不可破壞現有 active portfolio list/update/close 行為。
4. 不可加入 LLM。
5. 不可從價格下跌自動判斷使用者是在攤平，必須依 reason_code / fixed option / plan adherence。
6. 不可改變 Single Trade Review endpoint。
7. 不可把 add-entry flow 混成新的 initial_entry。
8. 加碼必須保留 event-time provenance。

本階段必做範圍：
1. 新增明確 add-entry API 或等效 action。
2. Add-entry request 至少包含 date、price、quantity、fees/taxes handling、reason fixed option、plan adherence、confidence level 或 equivalent fixed option。
3. add-entry event 寫入 PositionEvent，event_type = add_entry。
4. UI 提供加碼入口，與一般 edit 持股清楚分離。
5. UI 顯示原始 add-entry condition，讓使用者選擇本次是否符合。
6. 若本次違反原本加碼條件，必須能保存 plan_adherence = no 或 equivalent。
7. Existing update flow 仍不得建立 add_entry event。
8. Timeline / lifecycle review 可讀到 add_entry event。

TDD / 測試要求：
- add-entry API 會建立 add_entry event。
- PUT update 不會建立 add_entry event。
- add-entry reason / plan_adherence 可保存。
- invalid add-entry option 被拒絕。
- lifecycle entry_sequence 可以辨識 add_entry_count。
- frontend build/typecheck 使用專案既有命令執行。
- manual QA：加碼 action 與編輯持股 action 清楚分離。

完成後停止。不要執行 Phase D、Phase E 或 Phase F。

完成後請回報：修改了哪些檔案、跑了哪些測試/QA、是否有 pre-existing unrelated failures、下一個建議 phase。
```

## Phase D - Existing Position Backfill And Provenance

```text
COPY-PASTE PROMPT: Phase D - Existing Position Backfill And Provenance
請只執行 Entry Record Optimization 的 Phase D: Existing Position Backfill And Provenance。

前提：Phase B 已完成；Phase C 可已完成但不是必要前提。

本階段目標：讓使用者可以為既有 active positions 補填缺失的進場計畫，但必須清楚標記為事後補填，不可假裝它是原始 entry-time intent。

請先閱讀：
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/pages/ClosedPortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py
- backend/tests/test_decision_context_persistence.py
- frontend/package.json

本階段必須遵守：
1. 不可把 backfilled plan 視為原始 entry-time plan。
2. Backfilled plan 必須 source = user_backfilled。
3. Backfilled plan 必須 created_after_entry = true。
4. 不可從價格走勢、PnL、review result 推論使用者原本想法。
5. 不可要求使用者補填後才能 close 或 review。
6. 不可加入 LLM。
7. 不可破壞 existing position diagnosis / close / review flow。
8. 若使用者不補填，仍保留 decision_context: insufficient。

本階段必做範圍：
1. 新增 missing operation plan 提示。
2. 新增 backfill plan form，使用 Phase A/B 的固定選項。
3. 保存 backfilled plan provenance。
4. UI 明確提示：這是事後補填，會改善未來檢討，但不視為原始進場計畫。
5. decision-context-status 可區分 present / missing / backfilled。
6. Closed lifecycle review 顯示 provenance caveat。

TDD / 測試要求：
- backfilled plan 保存 source = user_backfilled。
- backfilled plan 保存 created_after_entry = true。
- 未補填時不阻止 close/review。
- lifecycle review 對 backfilled plan 顯示 caveat 或 provenance。
- invalid fixed option 被拒絕。
- frontend build/typecheck 使用專案既有命令執行。
- manual QA：existing active position 可以看到補填提示，補填後狀態更新。

完成後停止。不要執行 Phase E 或 Phase F。

完成後請回報：修改了哪些檔案、跑了哪些測試/QA、是否有 pre-existing unrelated failures、下一個建議 phase。

```

## Phase E - Lifecycle Review Integration

```text
COPY-PASTE PROMPT: Phase E - Lifecycle Review Integration
請只執行 Entry Record Optimization 的 Phase E: Lifecycle Review Integration。

前提：Phase B 已完成；若要完整處理加碼檢討，Phase C 也應完成。

本階段目標：讓 lifecycle review 使用固定選項進場紀錄與加碼紀錄，提升 plan adherence、scale-in review、stop-rule review 的判讀，但仍不得推論未記錄 intent。

請先閱讀：

- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/analysis/position_lifecycle.py
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- frontend/src/pages/ClosedPortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_position_lifecycle_analysis.py
- backend/tests/test_portfolio_router.py
- frontend/package.json

本階段必須遵守：

1. 不可推論未記錄 intent。
2. decision_context missing 時必須保留 insufficient。
3. Backfilled plan 必須顯示 provenance caveat。
4. 不可加入 LLM。
5. 不可把 raw 0-100 score 當成預設主視覺。
6. 不可改變 Single Trade Review row-scoped 語意。
7. 不可用未來資料批評 entry-time decision。
8. 不可宣稱精準高低點是唯一正確操作點。

本階段必做範圍：

1. Lifecycle review 使用 entry reason 判斷 entry 是否符合計畫。
2. Lifecycle review 使用 planned holding period 判斷是否過早/過晚，但需避免硬判斷。
3. Lifecycle review 使用 default stop rule 判斷是否忽視預設防守條件。
4. Lifecycle review 使用 add-entry condition 判斷 planned scale-in vs averaging down。
5. UI 顯示固定選項與 review 結論的關聯。
6. 缺資料時輸出 data quality notes。
7. Backfilled plan 顯示 caveat。
8. Review 文案必須以 labels、reasons、caveats、source events 為主。

TDD / 測試要求：

- no_averaging_down plan + lower-price add_entry below MA20 -> 標示違反加碼計畫或 needs_review。
- pullback_holds_ma20 entry reason + entry above/near MA20 -> 支持訊號可追溯。
- stop rule violated but not acted on -> review 顯示 needs_review，前提是資料足夠。
- missing decision context -> 不硬判斷。
- backfilled plan -> 顯示 provenance caveat。
- frontend build/typecheck 使用專案既有命令執行。
- manual QA：closed lifecycle review 顯示進場理由、持有週期、停損規則、加碼條件與檢討關聯。

完成後停止。不要執行 Phase F。

完成後請回報：修改了哪些檔案、跑了哪些測試/QA、是否有 pre-existing unrelated failures、下一個建議 phase。
```

## Phase F - Spec Promotion Review

```text
COPY-PASTE PROMPT: Phase F - Spec Promotion Review
請只執行 Entry Record Optimization 的 Phase F: Spec Promotion Review。

前提：Phase A-E 已完成且行為穩定。

本階段目標：審查哪些已實作並穩定的 API/schema/UI contract 應 promotion 到正式 specs。不要新增功能。

請先閱讀：

- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/specs/README.md
- docs/specs/backend-api-technical-spec.md
- docs/specs/ai-stock-sentinel-architecture-spec.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- frontend/src/lib/portfolioTypes.ts

本階段必須遵守：

1. 只記錄已穩定實作的事實。
2. 不可把未完成 phase 寫成正式 spec。
3. 不可新增 backend/frontend 功能。
4. 不可修改測試來配合文件。
5. 不可移除 docs/plans 中的討論脈絡。
6. 不可把 optional future ideas 寫成已承諾 contract。

本階段必做範圍：

1. Review implementation status of Phase A-E。
2. 決定哪些 API request/response fields 進 backend-api-technical-spec。
3. 決定哪些 long-term analysis semantics 進 architecture spec。
4. 更新 specs 時保留固定選項、provenance、decision_context insufficient 語意。
5. 若有未穩定事項，留在 docs/plans open questions。

驗收要求：

- specs 只包含已穩定事實。
- docs/plans requirement 保留完整討論脈絡。
- prompt.md 不需要再新增下一階段，除非使用者要求。
- git diff 僅包含文件變更。

完成後停止。

完成後請回報：修改了哪些文件、哪些內容被 promotion、哪些仍留在 open questions。
```
