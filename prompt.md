# COPY-PASTE PROMPT: Phase A - Event Ledger Foundation

```text
請只執行 Position Lifecycle Entry/Exit Analysis 的 Phase A: Event Ledger Foundation。

本階段目標：新增 position_event event ledger foundation，讓 portfolio create/close flow 可以開始記錄事件，但不改現有使用者體驗、不改現有 API response shape、不實作 timeline、metrics、classification 或 lifecycle review UI。

請先閱讀這些檔案以確認現有架構與測試：
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- docs/plans/2026-06-04-single-trade-review-analysis.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/analysis/trade_review.py
- backend/tests/test_portfolio_router.py
- backend/tests/test_trade_review.py

本階段必須遵守的相容性限制：
1. 不可破壞現有 /portfolio active portfolio 行為。
2. 不可破壞現有 /portfolio/closed closed row 與 group display 行為。
3. 不可破壞現有 /portfolio/{portfolio_id}/close partial/full close 行為與 response shape。
4. 不可破壞現有 /portfolio/{portfolio_id}/review Single Trade Review 行為。
5. 不可改變 TradeReview 的 row-scoped 語意；它仍然 unique by portfolio_id。
6. 不可把多個 exit batch 合併進現有 Single Trade Review。
7. 不可讓 lifecycle review 共用 /portfolio/{portfolio_id}/review endpoint。
8. 不可把 PUT /portfolio/{id} 的成本、日期、數量修改直接視為 add_entry；若要記錄只能明確標成 manual_adjustment 或 manual_record_correction。
9. 不可從價格走勢推論使用者意圖；沒有記錄的 intent 一律是 not_recorded 或 insufficient。
10. 不可接 LLM，不可做 metrics/classification/template review。
11. 不可在 evidence payload 中儲存完整 OHLCV / K-line arrays。
12. 不可要求使用者手動輸入證券交易稅；交易稅預設必須由系統依 market/product/broker 設定計算，且仍相容既有 close flow。
13. 不可把台灣股票稅費規則寫成永久硬編碼常數；台股預設值只能是 configurable market/product/broker defaults。
14. broker handling fee 與 securities transaction tax 必須清楚分離：position_event.fees 記錄券商手續費，position_event.taxes 記錄交易稅。

本階段必做範圍：
1. 新增 PositionEvent model。
2. 新增 Alembic migration。
3. 新增必要 indexes，至少包含 user_id、position_group_id、symbol、event_date。
4. position_event 至少支援 id、user_id、position_group_id、symbol、event_type、event_date、price、quantity、fees、taxes、source_portfolio_id、reason_category、reason_code、plan_adherence、confidence_level、note、source、data_quality_note、created_at、updated_at。
5. event_type 至少支援 initial_entry、add_entry、partial_exit、full_exit、manual_adjustment。
6. source 至少支援 synthetic_from_portfolio_row、user_backfilled、user_recorded_at_event_time、manual_record_correction、not_recorded。
7. 新增 conservative backfill：active row -> synthetic initial_entry；full-close row -> synthetic initial_entry + full_exit；partial-close row -> synthetic partial_exit，依 position_group_id 串接。
8. backfilled events 必須標記 source = synthetic_from_portfolio_row。
9. backfilled intent fields 必須是 not_recorded 或 decision_context: insufficient，不可假裝知道使用者原始意圖。
10. 建立新持股時寫入 initial_entry event。
11. partial close 時寫入 partial_exit event。
12. full close 時寫入 full_exit event。
13. 保持現有 create/close API response shape 不變。
14. create/close dual-write event 時，系統預設計算並記錄 fees 與 taxes；不得把證券交易稅改成 required manual input。
15. broker handling fee 計算要支援 broker fee rate、fee discount、minimum fee、actual-fee override。
16. sell-side transaction tax 要依 product/market/broker rules 計算；台灣普通股賣出證交稅約 0.3% 只能作為 configurable default example，不可寫死成永久 Taiwan-only 行為。

測試要求：
- model/migration test 或等效 regression test 覆蓋 position_event 欄位。
- create portfolio 會寫入 initial_entry。
- partial close 會保留現有 active/closed row 行為，並寫入 partial_exit。
- full close 會保留現有 closed row 行為，並寫入 full_exit。
- backfill 不補假的 reason/plan。
- fees/taxes 仍寫入 position_event，且證券交易稅可由系統預設計算，不需要使用者手動輸入。
- broker handling fee discount/minimum/actual-fee override 與 sell-side product-dependent transaction tax 分別有 regression coverage。
- 現有 test_portfolio_router.py 與 test_trade_review.py 仍通過。

完成後停止。不要實作 Phase A2、Phase B、Phase C、Phase D、Phase E、Phase F、timeline UI、metrics、classification、lifecycle review UI 或 LLM summary。

完成後請回報：修改了哪些檔案、跑了哪些測試、是否有 pre-existing unrelated failures、下一個建議 phase。
```

# COPY-PASTE PROMPT: Phase A2 - Decision Context Foundation

```text
請只執行 Position Lifecycle Entry/Exit Analysis 的 Phase A2: Decision Context Foundation。

前提：Phase A 已完成。

本階段目標：讓 position events 能保存固定操作原因與 lifecycle-level 交易計畫脈絡，但不強迫既有流程填寫、不做 timeline UI、不做 metrics、不做 classification、不做 lifecycle review API/UI。

請先閱讀這些檔案以確認現有架構與 Phase A 結果：
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- docs/plans/2026-06-04-single-trade-review-analysis.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/tests/test_portfolio_router.py
- backend/tests/test_trade_review.py

本階段必須遵守的相容性限制：
1. 不可破壞現有 /portfolio、/portfolio/closed、/portfolio/{portfolio_id}/close、/portfolio/{portfolio_id}/review 行為。
2. 不可改變 TradeReview 的 row-scoped 語意。
3. 不可要求 existing active positions 立即補填 plan 才能繼續診斷、close 或 Single Trade Review。
4. 不可自動補 planned_invalidation、planned_stop_price、planned_risk_pct、reason_code、plan_adherence、confidence_level 這類 intent-sensitive 欄位。
5. 不可從價格走勢推論使用者意圖。
6. 不可接 LLM。
7. 不可做 timeline UI、metrics、classification、lifecycle review API/UI。
8. fees/taxes 設計需延續 Phase A：ledger 記錄最終金額，但交易稅不可是使用者必填欄位，費率必須可設定。

本階段必做範圍：
1. 新增或完善 event-level reason fields：reason_category、reason_code、plan_adherence、confidence_level、note。
2. reason_category 至少支援 technical、institutional_flow、fundamental、news、risk_control、plan_execution、emotional、record_correction、not_recorded。
3. entry/add reason_code 至少支援 breakout_confirmation、pullback_held_support、pullback_held_ma20、institutional_flow_strengthened、fundamental_thesis_improved、planned_scale_in、averaging_down、chasing_momentum、manual_record_correction。
4. exit/trim reason_code 至少支援 target_reached、trailing_stop_hit、support_broken、ma20_lost、institutional_flow_weakened、fundamental_thesis_broken、news_risk_increased、risk_reduction、profit_protection、planned_scale_out、stop_loss、emotional_exit、manual_record_correction。
5. plan_adherence 至少支援 yes、partial、no、not_recorded。
6. confidence_level 至少支援 high、medium、low、not_recorded。
7. 新增 lifecycle-level plan persistence，或在合適 table 中保存 plan fields。
8. lifecycle-level plan 至少支援 thesis、setup_type、planned_holding_period、planned_invalidation、planned_stop_price、planned_target_or_scale_out_rule、planned_risk_amount、planned_risk_pct、position_sizing_rationale、source、created_after_entry。
9. setup_type 至少支援 breakout、pullback、mean_reversion、value_revaluation、earnings_or_event、momentum_continuation、long_term_accumulation、defensive_rebalance、other。
10. planned_holding_period 至少支援 short_term、swing、medium_term、long_term。
11. planned_invalidation 是核心欄位；未填時不得假設使用者原本有明確失效條件。
12. 既有 active positions 若沒有 plan，保持可用，但回傳 missing/insufficient decision context。
13. 若使用者事後補填 plan，必須標記 source = user_backfilled 與 created_after_entry = true。
14. 新增後端 serializer/schema/types，讓前端可讀取 missing operation plan 狀態。

測試要求：
- reason fields 可保存與讀取。
- lifecycle plan fields 可保存與讀取。
- setup_type、planned_holding_period、reason_category、reason_code、plan_adherence、confidence_level 的 allowed values 有測試覆蓋。
- 未填寫 plan 時不會阻止 position diagnosis、close、Single Trade Review。
- intent-sensitive fields 不會被自動 default 成具體值。
- user_backfilled plan 會標記 created_after_entry。
- 現有 test_portfolio_router.py 與 test_trade_review.py 仍通過。

完成後停止。不要實作 Phase B、Phase C、Phase D、Phase E、Phase F、timeline UI、metrics、classification、lifecycle review API 或 LLM summary。

完成後請回報：修改了哪些檔案、跑了哪些測試、是否有 pre-existing unrelated failures、下一個建議 phase。
```

# COPY-PASTE PROMPT: Phase B - Event Timeline API And UI

```text
請只執行 Position Lifecycle Entry/Exit Analysis 的 Phase B: Event Timeline API And UI。

前提：Phase A 與 Phase A2 已完成。

本階段目標：提供 read-only event timeline API/UI，讓使用者能看到同一 position_group_id 下的 entries/adds/exits 時間線。本階段不做 lifecycle judgment、不做 metrics engine、不做 classification、不保存 lifecycle review。

請先閱讀這些檔案以確認現有架構與前端頁面：
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- frontend/src/pages/ClosedPortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py
- backend/tests/test_trade_review.py

本階段必須遵守的相容性限制：
1. 不可破壞現有 /portfolio/closed grouping 與期間篩選行為。
2. 不可移除或改變每筆 exit batch row 原本的 檢討分析 button。
3. 不可把 timeline 命名或呈現成 Single Trade Review。
4. 不可把 timeline 當成 lifecycle review 或輸出 judgment。
5. 不可改變 /portfolio/{portfolio_id}/review。
6. 不可接 LLM。
7. 不可做 metrics、classification、lifecycle review persistence。
8. route 有 /portfolio/groups/... 時必須放在 dynamic /{portfolio_id}/... routes 前面，避免被 dynamic route 吃掉。
9. UI 顯示 fees/taxes 時要延續前面設計：可顯示系統計算值，但不可要求使用者手動輸入交易稅。

後端必做範圍：
1. 新增 GET /portfolio/groups/{position_group_id}/events。
2. endpoint 必須只允許目前登入使用者讀取自己的 group events。
3. endpoint 回傳 chronological events。
4. endpoint 回傳 event provenance，例如 synthetic_from_portfolio_row、user_backfilled、user_recorded_at_event_time、manual_record_correction、not_recorded。
5. events 按 event_date 與 created_at 穩定排序。
6. 若 group 不存在或不屬於目前使用者，回傳合適錯誤，不可洩漏資料。

前端必做範圍：
1. 在 /portfolio/closed group header 增加 timeline 入口。
2. timeline 顯示 entries、add entries、partial exits、full exit、manual adjustments。
3. timeline 必須明確標示 synthetic / not_recorded / insufficient decision context。
4. 保留每筆 closed row 原本的 檢討分析 button。
5. timeline 文案必須與 Single Trade Review 清楚分離。

測試與 QA 要求：
- API contract test：只回傳本人 group events。
- events 按 event_date 與 created_at 穩定排序。
- closed page 仍保留 per-batch review action。
- frontend build/typecheck 使用專案既有命令執行；請先查看 frontend/package.json。
- manual QA：partial close 後 group timeline 能看到 exit event，原本 Single Trade Review 仍可使用。

完成後停止。不要實作 Phase C、Phase D、Phase E、Phase F、lifecycle metrics、classification、group-level lifecycle review persistence 或 LLM summary。

完成後請回報：修改了哪些檔案、跑了哪些測試/QA、是否有 pre-existing unrelated failures、下一個建議 phase。
```

# COPY-PASTE PROMPT: Phase C - Lifecycle Metrics Engine

```text
請只執行 Position Lifecycle Entry/Exit Analysis 的 Phase C: Lifecycle Metrics Engine。

前提：Phase A、Phase A2、Phase B 已完成。

本階段目標：從 position events 與 market data 產生 deterministic lifecycle metrics 與 evidence payload。本階段不做 classification/template review、不做 lifecycle review UI、不接 LLM。

請先閱讀這些檔案以確認現有分析模式與資料來源：
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/analysis/trade_review.py
- backend/src/ai_stock_sentinel/analysis/position_scorer.py
- backend/tests/test_trade_review.py
- backend/tests/test_portfolio_router.py

本階段必須遵守的相容性限制：
1. 不可修改 trade_review.py 的 Single Trade Review 語意；如果需要共用 helper，必須保持 row-scoped review 行為不變。
2. lifecycle analysis 必須放在獨立 module，不要把 lifecycle review 塞進 trade_review.py。
3. 不可改變 /portfolio/{portfolio_id}/review。
4. 不可接 LLM。
5. 不可 assign lifecycle classifications。
6. 不可在 evidence payload 中保存完整 OHLCV / K-line arrays。
7. 不可用未來資料批評早期 entry/exit decision。
8. 不可宣稱精準高點或低點就是唯一正確操作點；高低點只能作為 outcome facts。
9. 若沒有 decision context，必須輸出 decision_context: insufficient，不可推論使用者意圖。
10. fees/taxes 使用 ledger 金額；若缺漏，可依 Phase A 的可設定規則補 calculated default，但必須標明 data_quality。
11. score-like metrics 可計算與保存為 internal/advanced trace，但不可把未校準的 0-100 分數設計成預設使用者決策主訊號。

本階段必做範圍：
1. 新增獨立 lifecycle analysis module。
2. metrics 必須 deterministic、formula-based、可測試。
3. 每個 event 的 technical indicators 必須 point-in-time：entry event 只能用 entry date 以前資料；exit event 只能用 exit date 以前資料。
4. full-period path metrics 只能作為 outcome facts，不可用來批評早期決策。
5. Lifecycle metrics 至少計算 total_realized_pnl、total_return_pct_on_weighted_cost、max_position_size、max_capital_at_risk、average_entry_price_over_time、weighted_average_entry_price、final_exit_date、total_holding_days_from_first_entry、active_exposure_days、max_unrealized_profit_pct、max_unrealized_drawdown_pct、profit_giveback_pct。
6. Entry sequence metrics 至少計算 entry_count、add_entry_count、initial_entry_vs_ma20_pct、each_add_entry_vs_ma20_pct、average_up_count、average_down_count、add_after_breakdown_count、add_after_confirmation_count、time_between_entries、price_distance_between_entries。
7. Exit sequence metrics 至少計算 exit_count、partial_exit_count、first_exit_return_pct、final_exit_return_pct、percentage_sold_before_peak、percentage_sold_after_breakdown、profit_protected_by_partial_exits、residual_position_giveback_pct。
8. Additional professional metrics 至少計算 planned_1r_amount、realized_r_multiple、mae_pct、mae_r_multiple、mfe_pct、mfe_r_multiple、mfe_capture_rate、plan_adherence_score、decision_quality_score、capital_at_risk_by_event、exposure_curve；若 benchmark/sector data 可用，計算 benchmark_relative_return_pct 與 sector_relative_return_pct，否則輸出 data_quality notes。
9. evidence payload 只能保存 summarized metrics、event list、indicator snapshots、capped detected event list、market regime snapshots at each entry/exit event、data quality notes。
10. evidence payload 禁止保存 full OHLCV arrays、raw K-line series、raw LLM prompts、未記錄的 inferred user intent。
11. 資料不足時回傳 explicit data_quality notes，不要硬判斷。
12. plan_adherence_score、decision_quality_score 與其他 lifecycle score 必須被標記為 internal/advanced trace；若要對使用者呈現，預設轉成 high/medium/low、label、reason、caveat，而不是裸露精確分數。

測試要求：
- weighted average cost 正確。
- realized PnL / return 正確。
- fees/taxes 會納入 realized result，且不要求手動交易稅。
- max_position_size、max_capital_at_risk、average_entry_price_over_time、final_exit_date、entry sequence metrics、exit sequence metrics 有 deterministic test 或 fixture coverage。
- planned_1r_amount、mae_r_multiple、mfe_r_multiple、decision_quality_score、capital_at_risk_by_event、exposure_curve 在資料足夠時可重現；資料不足時有 data_quality notes。
- MAE / MFE / MFE capture deterministic。
- point-in-time indicator 不偷用 future data。
- evidence payload 不含完整 OHLCV arrays。
- evidence payload 包含 capped detected events、market regime snapshots、data_quality notes。
- insufficient data 會產生 data_quality notes。
- score-like metrics 不會被預設 template/UI 當成 headline 0-100 分數；raw score breakdown 只進 advanced trace 或 evidence payload。
- 現有 test_trade_review.py 與 test_portfolio_router.py 仍通過。

完成後停止。不要實作 Phase D、Phase E、Phase F、classification/template review、lifecycle review UI 或 LLM summary。

完成後請回報：修改了哪些檔案、跑了哪些測試、是否有 pre-existing unrelated failures、下一個建議 phase。
```

# COPY-PASTE PROMPT: Phase D - Deterministic Classification And Template Review

```text
請只執行 Position Lifecycle Entry/Exit Analysis 的 Phase D: Deterministic Classification And Template Review。

前提：Phase C 已完成。

本階段目標：新增 rule-based lifecycle classification 與 fixed template review。本階段不做 lifecycle review API/UI persistence、不接 LLM。

請先閱讀這些檔案以確認現有 lifecycle metrics 輸出與測試：
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/analysis/trade_review.py
- Phase C 新增的 lifecycle analysis module
- backend/tests/test_trade_review.py
- backend/tests/test_portfolio_router.py

本階段必須遵守的相容性限制：
1. 不可改變 Single Trade Review row-scoped 行為。
2. 不可改變 /portfolio/{portfolio_id}/review。
3. 不可接 LLM。
4. LLM 不可計算 metrics、不可 assign classification、不可推論 intent。本階段完全不要加入 LLM。
5. 每個 classification 必須 rule-based、deterministic、可測。
6. 每一句 template 都必須追溯到 event、metric、classification 或 recorded reason。
7. 若資料不足或缺少 decision context，輸出 insufficient_data / decision_context: insufficient，不可硬判斷。
8. 不可輸出空泛建議，例如「下次小心一點」。建議必須具體到下次交易可執行。
9. fees/taxes 只能作為已記錄或系統計算的事實成本，不可用它推論使用者意圖。
10. 不可宣稱精準高點或低點就是唯一正確操作點；只能以 metrics/evidence 描述是否有 giveback、risk reduction 或 premature scale-out 訊號。
11. Template 不可用精確 0-100 分數作為 headline conclusion；必須優先使用 tiers、labels、reasons、caveats、source events。

本階段必做範圍：
1. 新增 entry sequence classifications，例如 disciplined_scaling_in、chasing_scale_in、averaging_down_into_weakness、early_probe_then_confirm、oversized_initial_entry、insufficient_data。
2. 新增 exit sequence classifications，例如 disciplined_scale_out、premature_scale_out、late_scale_out、risk_reduction_exit、incoherent_exit_sequence、insufficient_data。
3. 新增 lifecycle classifications，例如 coherent_position_management、good_entry_poor_exit、weak_entry_saved_by_exit、overtraded_position、held_winner_well、gave_back_winner、averaged_down_failed、insufficient_data。
4. 每個 classification 必須包含 classification、confidence、supporting_signals、conflicting_signals、caveats、source_events。
5. 新增 fixed template output，至少包含 overall conclusion、what worked、what needs review、event-level evidence、next-operation rules、data quality notes。
6. 每一句 template 都必須可追溯到 event、metric、classification 或 recorded reason。
7. Classification rule shape 必須覆蓋需求文件中的代表規則：averaging_down_into_weakness、disciplined_scale_out、premature_scale_out。
8. 若 template 需要呈現信心或品質，使用 high/medium/low、insufficient、needs_review 等 label；raw score 只能放在 advanced trace 或 evidence payload。

測試要求：
- averaging down below MA20 with weak regime -> averaging_down_into_weakness。
- positive risk-reducing trim with plan adherence -> disciplined_scale_out 或 risk_reduction_exit。
- high percentage trim during strong uptrend without invalidation -> premature_scale_out。
- late exits after major giveback or breakdown -> late_scale_out。
- coherent event sequence with plan adherence and controlled sizing -> coherent_position_management。
- insufficient decision context 不會被硬判斷成使用者犯錯。
- saved fixture 對同一 event sequence 產出穩定 classification。
- template sections 完整且引用 source events/metrics。
- template headline 不顯示未校準 raw 0-100 分數；若有 raw score，僅出現在 trace/evidence 區塊。
- 現有 test_trade_review.py 與 test_portfolio_router.py 仍通過。

完成後停止。不要實作 Phase E、Phase F、lifecycle review API/UI 或 LLM summary。

完成後請回報：修改了哪些檔案、跑了哪些測試、是否有 pre-existing unrelated failures、下一個建議 phase。
```

# COPY-PASTE PROMPT: Phase E - Lifecycle Review API And UI

```text
請只執行 Position Lifecycle Entry/Exit Analysis 的 Phase E: Lifecycle Review API And UI。

前提：Phase D 已完成。

本階段目標：提供 group-level lifecycle review API/UI，將 Phase C/D 的 deterministic metrics/classification/template 保存並呈現，且明確與 Single Trade Review 分離。本階段不做 LLM summary。

請先閱讀這些檔案以確認現有後端 API、前端頁面與 Phase C/D 輸出：
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- docs/specs/backend-api-technical-spec.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/analysis/trade_review.py
- Phase C/D 新增的 lifecycle analysis/classification modules
- frontend/src/pages/ClosedPortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py
- backend/tests/test_trade_review.py

本階段必須遵守的相容性限制：
1. 不可改變 /portfolio/{portfolio_id}/review Single Trade Review 行為。
2. 不可把 lifecycle review 共用 /portfolio/{portfolio_id}/review endpoint。
3. 不可移除每個 exit batch row 的 檢討分析 button。
4. group-level lifecycle review 必須使用 /portfolio/groups/{position_group_id}/lifecycle-review 這類 group endpoint。
5. POST 若已存在 saved lifecycle review，直接回傳既有 review，不要 silently recompute。
6. refresh/recompute 若要支援，必須 explicit/version-aware；第一版可不做。
7. review_result 與 evidence_payload 必須同一 transaction 寫入。
8. 不可接 LLM，不可新增 llm_summary。
9. UI 必須清楚區分：單筆出場檢討 = one sell decision；整體部位檢討 = whole multi-entry/multi-exit lifecycle。
10. fees/taxes 顯示為 event ledger 中的成本事實，不要求使用者手動輸入交易稅。
11. UI 預設不可把 raw 0-100 score 當成主視覺；應優先呈現 tiers、labels、reasons、source events、data-quality warnings。

後端必做範圍：
1. 新增 GET /portfolio/groups/{position_group_id}/lifecycle-review。
2. 新增 POST /portfolio/groups/{position_group_id}/lifecycle-review。
3. GET 有 saved review 就回傳；沒有就回傳 not found 或 equivalent empty state。
4. POST 第一次建立 saved lifecycle review。
5. POST 若已存在 saved review，直接回傳既有 review，不要 silently recompute。
6. review_result 與 evidence_payload 必須同一 transaction 寫入。
7. lifecycle review 必須保存到獨立 position_lifecycle_review，不污染 trade_review。
8. endpoint 必須只允許目前登入使用者操作自己的 group。

前端必做範圍：
1. 在 /portfolio/closed group header 增加 整體部位檢討 action。
2. 每個 exit batch row 的 檢討分析 button 保留不變。
3. lifecycle modal/page 顯示 chronological timeline、整體結果、分批進場檢討、持倉管理檢討、分批出場檢討、下次操作規則、資料品質。
4. timeline 中每個 event 可展開顯示 point-in-time indicators 與 market regime snapshot。
5. UI 必須清楚區分單筆出場檢討與整體部位檢討。
6. 顯示 review provenance：real events / synthetic events / mixed provenance。
7. 提供 copyable evidence payload。
8. insufficient decision context 必須有清楚提示。
9. 若本階段穩定了 lifecycle API request/response shape、review versioning 或 UI contract，請同步更新 docs/specs/backend-api-technical-spec.md 或在回報中明確標示尚未 promotion。
10. 若顯示 score breakdown，必須放在 collapsed advanced trace 或 evidence payload，不可放在預設 summary headline。

測試與 QA 要求：
- API tests 覆蓋 saved review persistence、不重算、權限、transaction atomicity。
- frontend build/typecheck 使用專案既有命令執行；請先查看 frontend/package.json。
- frontend manual QA 覆蓋 closed group header 可開 lifecycle review、exit batch row 仍可開 Single Trade Review、timeline 正確顯示 events、event 可展開 point-in-time indicators、evidence payload 可複製、insufficient decision context 有清楚提示。
- frontend manual QA 覆蓋 lifecycle review 預設畫面以 labels/reasons/caveats 為主，不以 raw 0-100 score 作為主視覺。
- 現有 test_trade_review.py 與 test_portfolio_router.py 仍通過。

完成後停止。不要實作 Phase F 或任何 LLM summary。

完成後請回報：修改了哪些檔案、跑了哪些測試/QA、是否有 pre-existing unrelated failures、下一個建議 phase。
```

# COPY-PASTE PROMPT: Phase F - Optional Narrative Layer

```text
請只執行 Position Lifecycle Entry/Exit Analysis 的 Phase F: Optional Narrative Layer。

前提：Phase E 已完成、deterministic review 已穩定，且使用者明確要求 Phase F。

如果使用者沒有明確要求 Phase F，請不要執行本階段。

本階段目標：新增 optional llm_summary narrative layer，只把 deterministic lifecycle review 的 structured result 改寫成自然語言摘要。本階段不可新增 refresh/recompute 或其他非 LLM narrative 功能。

請先閱讀這些檔案以確認 Phase E 的 lifecycle review API/UI 與 deterministic output：
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/db/models.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- Phase C/D/E 新增的 lifecycle modules/API/UI
- frontend/src/pages/ClosedPortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_portfolio_router.py

本階段必須遵守的限制：
1. LLM 只能改寫 structured review result。
2. LLM 只能根據 evidence payload 做自然語言摘要。
3. LLM 不可計算任何數值。
4. LLM 不可 assign classification。
5. LLM 不可推論未記錄的使用者意圖。
6. LLM 不可取代 deterministic template 作為 source of truth。
7. LLM 不可使用完整 OHLCV / K-line arrays。
8. 若 evidence payload 顯示 decision_context: insufficient，LLM 摘要必須保留此限制，不可補故事。
9. 不可改變 Single Trade Review row-scoped 行為。
10. 不可改變 lifecycle review saved-review 不重算語意。
11. LLM 不可把 raw score 改寫成勝率、alpha、推薦強度或比 deterministic label 更強的投資判斷。

本階段必做範圍：
1. 新增 optional llm_summary 欄位的生成/顯示流程。
2. llm_summary 必須只根據 deterministic review_result 與 evidence_payload。
3. 若 deterministic result 不存在，不可直接用 LLM 生成 lifecycle review。
4. UI 必須清楚標示 LLM summary 是 narrative helper，不是 source of truth。
5. llm_summary 應摘要 labels、reasons、caveats、source events；不要主打未校準 raw 0-100 score。

測試與 QA 要求：
- LLM prompt/input 不包含完整 OHLCV arrays。
- LLM summary 不會改變 deterministic classifications。
- decision_context insufficient 時 summary 不會推論未記錄 intent。
- raw score 不會被 LLM summary 轉成勝率、推薦強度或更高信心敘事。
- saved review 行為仍不 silently recompute。
- 現有 backend/frontend 相關測試仍通過。

完成後停止。不要新增 refresh/recompute 或其他非 LLM narrative 功能，除非使用者另行要求。

完成後請回報：修改了哪些檔案、跑了哪些測試/QA、是否有 pre-existing unrelated failures。
```
