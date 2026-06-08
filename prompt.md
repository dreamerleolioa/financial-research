# Position Lifecycle Entry/Exit Analysis Implementation Prompt

你現在要在 `financial-research` 專案中實作 Position Lifecycle Entry/Exit Analysis，也就是針對同一個 `position_group_id` 底下的分批進場、分批出場、持倉管理與整體交易生命週期做可追溯、可測試、rule-based 的檢討系統。

請先完整閱讀並以此為主要需求來源：

- `docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md`
- `docs/plans/2026-06-04-single-trade-review-analysis.md`
- `docs/specs/backend-api-technical-spec.md`
- `backend/src/ai_stock_sentinel/db/models.py`
- `backend/src/ai_stock_sentinel/portfolio/router.py`
- `backend/src/ai_stock_sentinel/analysis/trade_review.py`
- `frontend/src/pages/PortfolioPage.tsx`
- `frontend/src/pages/ClosedPortfolioPage.tsx`
- `frontend/src/lib/portfolioTypes.ts`
- `backend/tests/test_portfolio_router.py`
- `backend/tests/test_trade_review.py`

## Core Intent

目前系統的 Single Trade Review 是 row-centric：一筆已結案 `UserPortfolio` row 代表一個 exit batch，`trade_review` 也只檢討該 closed row。

本需求要新增 event-centric lifecycle review：同一個 `position_group_id` 底下的所有 entry、add-entry、partial-exit、full-exit 事件要能形成一條 chronological event ledger，並在不破壞現有 Single Trade Review MVP 的前提下，新增 group-level lifecycle timeline 與 lifecycle review。

請使用 additive design：新增資料表、API、型別、UI 與分析 engine；不要把現有 `trade_review` 或 `/portfolio/{portfolio_id}/review` 改造成 lifecycle review。

## Non-Negotiable Compatibility Rules

請嚴格遵守以下限制：

1. 不可破壞現有 `/portfolio` active portfolio 行為。
2. 不可破壞現有 `/portfolio/closed` closed row 與 group display 行為。
3. 不可破壞現有 `/portfolio/{portfolio_id}/close` partial/full close 行為與 response shape。
4. 不可破壞現有 `/portfolio/{portfolio_id}/review` Single Trade Review 行為。
5. 不可改變 `TradeReview` 的 row-scoped 語意；它仍然 unique by `portfolio_id`。
6. 不可把多個 exit batch 合併進現有 Single Trade Review。
7. 不可讓 lifecycle review 共用 `/portfolio/{portfolio_id}/review` endpoint。
8. 不可要求既有 close form 立即必填新的 reason fields；第一版必須 backward-compatible。
9. 不可把 `PUT /portfolio/{id}` 的成本、日期、數量修改直接視為 add-entry；若要記錄，只能明確標成 `manual_adjustment` 或 `manual_record_correction`。
10. 不可從價格走勢推論使用者意圖；沒有記錄的 intent 一律是 `not_recorded` 或 `insufficient`。
11. 不可讓 LLM 計算 PnL、R-multiple、MAE、MFE、weighted cost、technical indicators 或 classification。
12. 不可在 evidence payload 中儲存完整 OHLCV / K-line arrays。
13. 不可要求使用者手動輸入證券交易稅；交易稅預設必須由系統依 market/product/broker 設定計算，且仍可相容既有 close flow。
14. 不可把台灣股票稅費規則寫成永久硬編碼常數；台股預設值只能是可設定的 market/product/broker defaults。
15. 手續費與證券交易稅必須清楚分離：broker handling fee 依券商費率、折扣、最低收費與實際手續費 override 處理；sell-side securities transaction tax 依商品別規則計算。

## Required Architecture Direction

新增一個平行 lifecycle subsystem。

### Data Model

新增 `position_event` table，用來記錄事件帳本。至少需要支援：

```text
id
user_id
position_group_id
symbol
event_type
event_date
price
quantity
fees
taxes
source_portfolio_id
reason_category
reason_code
plan_adherence
confidence_level
note
source
data_quality_note
created_at
updated_at
```

`event_type` 至少支援：

- `initial_entry`
- `add_entry`
- `partial_exit`
- `full_exit`
- `manual_adjustment`

`reason_category` 至少支援：

- `technical`
- `institutional_flow`
- `fundamental`
- `news`
- `risk_control`
- `plan_execution`
- `emotional`
- `record_correction`
- `not_recorded`

`plan_adherence` 至少支援：

- `yes`
- `partial`
- `no`
- `not_recorded`

`confidence_level` 至少支援：

- `high`
- `medium`
- `low`
- `not_recorded`

`source` 至少支援：

- `synthetic_from_portfolio_row`
- `user_backfilled`
- `user_recorded_at_event_time`
- `manual_record_correction`
- `not_recorded`

`fees` 與 `taxes` 必須保留在 `position_event` ledger 中，但第一版預設由系統計算，不可變成使用者必填手動欄位。`fees` 代表 broker handling fee，需支援 broker fee rate、fee discount、minimum fee 與 actual-fee override。`taxes` 代表 securities transaction tax，主要在 sell-side events 依 market/product/broker rule 計算；例如台灣普通股賣出證交稅約 0.3%，但這只能作為可設定預設值，不可寫成不可調整的永久 Taiwan-only 常數。

新增 `position_lifecycle_review` table，用來保存 group-level review。至少需要支援：

```text
id
user_id
position_group_id
symbol
review_version
review_result JSON/JSONB
evidence_payload JSON/JSONB
llm_summary nullable text
created_at
updated_at
```

如果資料庫目前使用 SQLite 測試與 Postgres production，migration 與 column types 要同時相容。

### Lifecycle Plan / Decision Context

新增 lifecycle-level plan 欄位或資料結構，用來保存使用者交易計畫。至少包含：

```text
thesis
setup_type
planned_holding_period
planned_invalidation
planned_stop_price
planned_target_or_scale_out_rule
planned_risk_amount
planned_risk_pct
position_sizing_rationale
source
created_after_entry
```

不要自動補 `planned_invalidation`、`planned_stop_price`、`planned_risk_pct`、`reason_code`、`plan_adherence`、`confidence_level` 這類 intent-sensitive 欄位。

如果既有部位沒有計畫資料，lifecycle review 必須顯示：

```text
decision_context: insufficient
```

## Implementation Phases

請依序實作。每個 phase 完成後都要跑相關測試。不要跳到下一個 phase，除非前一個 phase 已完成並驗證。

### Phase A: Event Ledger Foundation

目標：新增 event ledger，不改現有使用者體驗。

必做：

1. 新增 `PositionEvent` model。
2. 新增 Alembic migration。
3. 新增必要 indexes，至少包含 `user_id`、`position_group_id`、`symbol`、`event_date`。
4. 新增 conservative backfill：
   - active row -> synthetic `initial_entry`
   - full-close row -> synthetic `initial_entry` + `full_exit`
   - partial-close row -> synthetic `partial_exit`，依 `position_group_id` 串接
5. backfilled events 必須標記 `source = synthetic_from_portfolio_row`。
6. backfilled intent fields 必須是 `not_recorded` 或 `decision_context: insufficient`，不可假裝知道使用者原始意圖。
7. 建立新持股時寫入 `initial_entry` event。
8. partial close 時寫入 `partial_exit` event。
9. full close 時寫入 `full_exit` event。
10. 保持現有 create/close API response shape 不變。
11. create/close dual-write event 時，系統預設計算並記錄 `fees` 與 `taxes`；不得把證券交易稅改成 required manual input。
12. 手續費計算要與交易稅計算分開：broker handling fee 支援費率、折扣、最低收費與 actual-fee override；sell-side transaction tax 依商品別與市場/券商設定套用。
13. 台灣股票相關預設值必須放在可設定的 market/product/broker defaults 中，保留日後調整或支援其他市場的空間。

測試要求：

- model/migration test 或等效 regression test 覆蓋 `position_event` 欄位。
- create portfolio 會寫入 `initial_entry`。
- partial close 會保留現有 active/closed row 行為，並寫入 `partial_exit`。
- full close 會保留現有 closed row 行為，並寫入 `full_exit`。
- backfill 不補假的 reason/plan。
- fees/taxes 仍寫入 `position_event`，且證券交易稅可由系統預設計算，不需要使用者手動輸入。
- broker handling fee discount/minimum/actual-fee override 與 sell-side product-dependent transaction tax 分別有 regression coverage。
- 現有 `test_portfolio_router.py` 與 `test_trade_review.py` 仍通過。

### Phase A2: Decision Context Foundation

目標：讓事件能保存固定原因與計畫脈絡，但不強迫既有流程填寫。

必做：

1. 新增 event-level reason fields：`reason_category`、`reason_code`、`plan_adherence`、`confidence_level`、`note`。
2. 新增 lifecycle-level plan persistence，或在合適 table 中保存 plan fields。
3. 既有 active positions 若沒有 plan，保持可用，但回傳 missing/insufficient decision context。
4. 新增後端 serializer/schema/types，讓前端可讀取 missing operation plan 狀態。
5. 不要要求 LLM。

測試要求：

- reason fields 可保存與讀取。
- lifecycle plan fields 可保存與讀取。
- 未填寫 plan 時不會阻止 position diagnosis、close、Single Trade Review。
- intent-sensitive fields 不會被自動 default 成具體值。

### Phase B: Event Timeline API And UI

目標：先只提供 read-only timeline，不做 lifecycle judgment。

必做後端：

1. 新增 `GET /portfolio/groups/{position_group_id}/events`。
2. endpoint 必須只允許目前登入使用者讀取自己的 group events。
3. endpoint 回傳 chronological events。
4. endpoint 回傳 event provenance，例如 synthetic/user_backfilled/user_recorded_at_event_time。
5. 路由順序要避免被 `/{portfolio_id}/review` 這類 dynamic route 吃掉；static/group route 必須放在 dynamic route 前。

必做前端：

1. 在 `/portfolio/closed` group header 增加 timeline 入口。
2. timeline 顯示 entries、add entries、partial exits、full exit。
3. timeline 必須明確標示 synthetic / not recorded / insufficient decision context。
4. 保留每筆 closed row 原本的 `檢討分析` button。
5. 不要把 timeline 命名或呈現成 Single Trade Review。

測試要求：

- API contract test：只回傳本人 group events。
- events 按 event_date 與 created_at 穩定排序。
- closed page 仍保留 per-batch review action。
- manual QA：partial close 後 group timeline 能看到 exit event，原本 Single Trade Review 仍可使用。

### Phase C: Lifecycle Metrics Engine

目標：從 events 與 market data 產生 deterministic metrics 與 evidence payload。

必做：

1. 新增獨立 lifecycle analysis module，不要塞進 `trade_review.py`。
2. metrics 必須 deterministic、formula-based、可測試。
3. 每個 event 的 technical indicators 必須 point-in-time：
   - entry event 只能用 entry date 以前資料
   - exit event 只能用 exit date 以前資料
   - full-period path metrics 只能作為 outcome facts，不可用來批評早期決策
4. 至少計算：
   - total_realized_pnl
   - total_return_pct_on_weighted_cost
   - weighted_average_entry_price
   - total_holding_days_from_first_entry
   - active_exposure_days
   - max_unrealized_profit_pct
   - max_unrealized_drawdown_pct
   - profit_giveback_pct
   - realized_r_multiple when planned risk exists
   - mae_pct / mfe_pct
   - mfe_capture_rate
   - plan_adherence_score when decision context exists
5. evidence payload 只能保存 summarized metrics、event list、indicator snapshots、data quality notes，不可保存完整 K 線。
6. 資料不足時回傳 explicit data_quality notes，不要硬判斷。

測試要求：

- weighted average cost 正確。
- realized PnL / return 正確。
- MAE / MFE / MFE capture deterministic。
- point-in-time indicator 不偷用 future data。
- evidence payload 不含完整 OHLCV arrays。
- insufficient data 會產生 data_quality notes。

### Phase D: Deterministic Classification And Template Review

目標：新增 rule-based lifecycle review，不接 LLM。

必做：

1. 新增 entry sequence classifications，例如：
   - `disciplined_scaling_in`
   - `chasing_scale_in`
   - `averaging_down_into_weakness`
   - `early_probe_then_confirm`
   - `oversized_initial_entry`
   - `insufficient_data`
2. 新增 exit sequence classifications，例如：
   - `disciplined_scale_out`
   - `premature_scale_out`
   - `late_scale_out`
   - `risk_reduction_exit`
   - `incoherent_exit_sequence`
   - `insufficient_data`
3. 新增 lifecycle classifications，例如：
   - `coherent_position_management`
   - `good_entry_poor_exit`
   - `weak_entry_saved_by_exit`
   - `overtraded_position`
   - `held_winner_well`
   - `gave_back_winner`
   - `averaged_down_failed`
   - `insufficient_data`
4. 每個 classification 必須包含：
   - `classification`
   - `confidence`
   - `supporting_signals`
   - `conflicting_signals`
   - `caveats`
   - `source_events`
5. 新增 fixed template output，至少包含：
   - overall conclusion
   - what worked
   - what needs review
   - event-level evidence
   - next-operation rules
   - data quality notes
6. 每一句 template 都必須可追溯到 event、metric、classification 或 recorded reason。
7. 不可輸出空泛建議，例如「下次小心一點」。建議必須具體到下次交易可執行。

測試要求：

- averaging down below MA20 with weak regime -> `averaging_down_into_weakness`。
- positive risk-reducing trim with plan adherence -> `disciplined_scale_out` 或 `risk_reduction_exit`。
- high percentage trim during strong uptrend without invalidation -> `premature_scale_out`。
- saved fixture 對同一 event sequence 產出穩定 classification。
- template sections 完整且引用 source events/metrics。

### Phase E: Lifecycle Review API And UI

目標：提供 group-level lifecycle review，明確與 Single Trade Review 分離。

必做後端：

1. 新增 `GET /portfolio/groups/{position_group_id}/lifecycle-review`。
2. 新增 `POST /portfolio/groups/{position_group_id}/lifecycle-review`。
3. `GET` 有 saved review 就回傳；沒有就回傳 not found 或 equivalent empty state。
4. `POST` 第一次建立 saved lifecycle review。
5. `POST` 若已存在 saved review，直接回傳既有 review，不要 silently recompute。
6. `review_result` 與 `evidence_payload` 必須同一 transaction 寫入。
7. refresh/recompute 若要支援，必須是 explicit/version-aware；第一版可不做。
8. 不可與 `/portfolio/{portfolio_id}/review` 混淆。

必做前端：

1. 在 `/portfolio/closed` group header 增加 `整體部位檢討` action。
2. 每個 exit batch row 的 `檢討分析` button 保留不變。
3. lifecycle modal/page 顯示：
   - chronological timeline
   - 整體結果
   - 分批進場檢討
   - 持倉管理檢討
   - 分批出場檢討
   - 下次操作規則
   - 資料品質
4. UI 必須清楚區分：
   - 單筆出場檢討：one sell decision
   - 整體部位檢討：whole multi-entry/multi-exit lifecycle
5. 顯示 review provenance：real events / synthetic events / mixed provenance。
6. 提供 copyable evidence payload。

測試與 QA：

- API tests 覆蓋 saved review persistence、不重算、權限、transaction atomicity。
- frontend manual QA 覆蓋：
  - closed group header 可開 lifecycle review
  - exit batch row 仍可開 Single Trade Review
  - timeline 正確顯示 events
  - evidence payload 可複製
  - insufficient decision context 有清楚提示

### Phase F: Optional Narrative Layer

第一版不要實作。只有在 Phase C/D/E deterministic review 已穩定後，才可新增 optional `llm_summary`。

若未來實作 LLM summary，限制如下：

- LLM 只能改寫 structured review result。
- LLM 只能根據 evidence payload 做自然語言摘要。
- LLM 不可計算任何數值。
- LLM 不可 assign classification。
- LLM 不可推論未記錄的使用者意圖。
- LLM 不可取代 deterministic template 作為 source of truth。

## Required Test Strategy

請 test-first 或至少每個 phase 補齊 regression tests。

最低驗收測試：

1. 現有 partial close 行為仍通過。
2. 現有 Single Trade Review 行為仍通過。
3. 現有 `/portfolio/closed` grouping 行為仍通過。
4. 現有 `/analyze/position` 行為不受影響。
5. `position_event` migration 與 backfill 正確。
6. create/close dual-write event 正確。
7. group events endpoint 權限與排序正確。
8. lifecycle metrics deterministic。
9. lifecycle classifications deterministic。
10. lifecycle review persistence 不 silently recompute。
11. frontend 能明確區分 Single Trade Review 與 Lifecycle Review。
12. fees/taxes 記錄於 `position_event`，且預設由系統計算。
13. 證券交易稅不是 required manual input；broker handling fee 與 sell-side product-dependent transaction tax 的規則分開測試。
14. 台灣市場/商品/券商預設值可設定，測試不可依賴不可調整的永久 hardcoded Taiwan constants。

建議每個 backend phase 至少執行：

```bash
cd backend
make test
```

建議每個 frontend phase 至少執行 frontend typecheck/build 或專案既有 equivalent command。請先查看 `frontend/package.json` 後再決定實際命令。

## Implementation Notes

- 使用既有 code style 與 test style。
- 不要新增無關重構。
- 不要刪除或弱化既有測試。
- 不要用 `as any`、`@ts-ignore`、`@ts-expect-error` 來壓錯。
- 不要把 migration/backfill 寫成會偽造使用者決策脈絡。
- 不要把證券交易稅做成使用者必填欄位；系統必須能依設定預設計算並寫入 `position_event.taxes`。
- 不要把 broker handling fee 與 securities transaction tax 混成同一種成本；`position_event.fees` 記錄券商手續費，`position_event.taxes` 記錄交易稅。
- broker handling fee 要支援券商折扣、最低手續費與實際手續費 override；transaction tax 要支援 sell-side、商品別、market/product/broker 設定。
- 台灣普通股賣出證交稅約 0.3% 只能作為可設定預設範例，不可寫死成永久 Taiwan-only 行為。
- 如果發現現有資料不足，請明確回傳 `insufficient_data` / `decision_context: insufficient`，不要硬湊結論。
- 若 API response 需要新增欄位，優先做 backward-compatible optional fields。
- 若 route 有 `/portfolio/groups/...`，請放在 dynamic `/{portfolio_id}/...` routes 前面。

## Definition Of Done

完成後必須達成：

1. Position lifecycle event ledger 可用。
2. 現有 portfolio create/close flow dual-write events，但外部行為不變。
3. 既有資料有 conservative synthetic backfill。
4. group-level event timeline API/UI 可用。
5. lifecycle metrics/classification/template review 是 deterministic。
6. lifecycle review 保存於獨立 `position_lifecycle_review`，不污染 `trade_review`。
7. UI 清楚區分 exit-batch review 與 whole-lifecycle review。
8. `position_event` 保留並記錄 `fees` 與 `taxes`，預設由系統計算；證券交易稅不是 required manual input。
9. broker handling fee 的折扣、最低收費與 actual-fee override，以及 sell-side product-dependent transaction tax，都由可設定的 market/product/broker rules 支援。
10. 台灣股票預設稅費規則是 configurable defaults，不是永久 hardcoded constants，且不破壞既有 close-flow compatibility 與 additive lifecycle design。
11. 所有相關 tests/build/checks 通過，或明確列出 pre-existing unrelated failures。
