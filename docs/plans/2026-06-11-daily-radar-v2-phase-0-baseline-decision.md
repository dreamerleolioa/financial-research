# Daily Radar v2 Phase 0 Baseline And Decisions

| Metadata | Value |
| --- | --- |
| Status | Phase 0 complete |
| Date | 2026-06-11 |
| Scope | Baseline, source naming, spec alignment notes |
| Related requirements | `docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md` |

This note records the current live Daily Radar behavior before Daily Radar v2 implementation. It is a baseline and decision log only. It does not implement market regime, multi-track universe expansion, relative strength, chip context cache, consumer integrations, UI changes, or scoring changes.

## Phase 0 Guardrails

- Daily Radar ranking, bucket assignment, score calculation, candidate sorting, and risk penalties remain deterministic rule-based.
- No LLM participates in Daily Radar ranking, bucket assignment, score calculation, or rule override.
- This phase did not add expensive live external calls and did not change frontend UI.
- Unimplemented v2 behavior is recorded as planned work or an open question, not as current spec fact.
- Shared evidence/context seams are recorded as future Phase 2C/2D integration needs only.

## Live Request Pattern Baseline

The live internal run entry point is `POST /internal/daily-radar/run` in `backend/src/ai_stock_sentinel/daily_radar/router.py`.

Current request pattern:

1. Resolve `run_date` from the request body or backend today.
2. Resolve `market`, defaulting to `TW`.
3. Build the universe through `select_dual_track_universe(universe_provider, run_date=run_date, market=market, track_limit=50)`.
4. Convert selected universe entries into per-symbol institutional payloads.
5. Call `ensure_daily_radar_raw_rows` for selected symbols only, using yfinance batch OHLCV backfill when final raw rows are missing.
6. Fall back to existing final `StockRawData` rows for selected symbols if the backfill path returns no rows.
7. Call `run_daily_radar(..., market_context={}, allow_fixture_fallback=False)`.
8. Commit the run and expose `run_id`, status, universe count, prefilter count, candidate count, errors, and timestamps.

Budget conclusion: the live pattern remains bounded by the selected dual-track universe plus batch OHLCV backfill. It is not a full-market per-symbol scan. Phase 0 did not run a live external request.

## Universe And Source Naming Baseline

Live provider wiring:

- `get_daily_radar_universe_provider()` returns `TwseRwdInstitutionalUniverseProvider`.
- The provider reads TWSE RWD fund reports through `https://www.twse.com.tw/rwd/zh/fund/{report_id}`.
- Current report IDs are `TWT38U` for foreign buy top report and `TWT44U` for investment trust buy top report.
- The compatibility class `FinMindMarketInstitutionalUniverseProvider` currently subclasses the TWSE RWD provider and sets the same provider name, so it must not be used as evidence that live Daily Radar is using a FinMind all-market path.

Current universe tracks are exactly:

- `same_day_institutional`
- `recent_accumulation`

Current selection behavior:

- Same-day institutional leaders are selected first.
- Recent accumulation leaders are merged second.
- Duplicate symbols are deduplicated by first appearance.
- Track metadata is retained in each `DailyRadarUniverseEntry` and later passed into the institutional payload as `institutional_universe_tracks`, same-day rank, recent rank, and track scores.

Source naming decision for later phases: use a layered naming model instead of mixing provider names.

| Layer | Current Phase 0 name | Reason |
| --- | --- | --- |
| Consumer-neutral domain | `institutional_universe` | Describes the data role without binding trace keys to a vendor. |
| Live provider implementation | `twse_rwd_fund_reports` | Matches current code path and request source. |
| Report-level source IDs | `TWT38U`, `TWT44U` | Keeps replay/debug trace precise. |
| Legacy/spec planned source | `finmind_all_market_institutional_flow` | Keep only as a planned or historical spec term until code is intentionally wired to FinMind. |

Open spec-sync question: `docs/specs/daily-stock-radar-spec.md`, `README.md`, and `docs/plans/2026-06-02-daily-radar-rollout-checklist.md` still describe FinMind all-market institutional requests in places. The live code path uses TWSE RWD reports. Phase 1 should decide whether the formal source contract is TWSE RWD, FinMind all-market, or a layered provider-neutral contract before updating trace keys and long-term specs.

## Market Context Gap

Live `market_context` is currently passed as an empty object from the router into `run_daily_radar`.

The service can accept a non-empty `market_context` and the scoring layer already records a market component and `input_snapshot.market_context`, but live market index wiring is not connected. This remains a Phase 1 gap.

Phase 0 must not treat `market_weakness`, market regime v1, or relative strength trace as live-complete behavior.

## Candidate Trace Baseline

Existing candidate trace fields already persisted through `daily_radar_candidates` and exposed through response schemas include:

- `matched_rules`
- `score_breakdown`
- `input_snapshot`
- `data_dates`
- `bucket_scores`
- `risk_labels`
- `repeat_status`

Current gaps:

- No explicit `scoring_version` field is persisted on `daily_radar_runs` or `daily_radar_candidates`.
- No explicit `rule_version` field is persisted on `daily_radar_runs` or `daily_radar_candidates`.
- No relative strength trace is persisted because relative strength vs market is not implemented in the live scoring path.
- `input_snapshot.market_context` exists structurally, but live runs receive an empty market context.
- Full margin context is not complete; minimal margin dates must not be described as complete margin data.

## Existing Evidence And Context Boundaries

Daily Radar currently has replayable candidate evidence through persisted candidate JSON fields. It does not yet have a consumer-neutral shared evidence/context repository.

Current consumer boundaries that Phase 2C/2D must preserve:

| Consumer | Existing boundary | Fields or behavior not to overwrite |
| --- | --- | --- |
| `/analyze` | New-position strategy analysis. Rule-based strategy fields are produced by backend Python; LLM can explain but must not overwrite labels or recalculate scores. | `strategy_type`, `entry_zone`, `stop_loss`, `holding_period`, `action_plan`, `action_plan_tag`, `signal_confidence`, `data_confidence`, `cross_validation_note`, `technical_indicators`, `institutional_flow_label`, `data_sources`. |
| `/analyze/position` | Existing holding diagnosis and the source of holding-operation truth. Python rule-based fields determine position action. | `position_analysis.recommended_action`, `position_analysis.trailing_stop`, `position_analysis.trailing_stop_reason`, `position_analysis.exit_reason`, `position_analysis.profit_loss_pct`, `position_analysis.position_status`, `position_analysis.distance_to_trailing_stop_pct`, cache isolation by `analysis_type="position"` and matching `_position_request`. |
| Portfolio diagnosis/history | Portfolio history reads `daily_analysis_log` within the user's holding window and keeps active/closed portfolio ownership boundaries. | `daily_analysis_log` fields, user scoping, holding-window filters, active/closed row semantics, `position_group_id` grouping behavior. |
| Single Trade Review | Row-scoped review for one closed portfolio row and one sell decision. | `trade_review.review_result`, `trade_review.evidence_payload`, `review_version="trade-review-v1"`, compact `evidence_payload` without full OHLCV arrays or raw prompts. |
| Position Lifecycle Review | Group-scoped deterministic review across a `position_group_id`; uses event ledger, lifecycle plan, point-in-time snapshots, caveats, and data quality. | `position_lifecycle_review.review_result`, `position_lifecycle_review.evidence_payload`, `review_version="position-lifecycle-review-v1"`, `decision_context.status`, `source`, `created_after_entry`, fixed-option labels, `advanced_internal` raw scores, no missing-intent inference. |

Shared layer design implication for Phase 2C/2D: shared evidence/context should be read/reference-only for these consumers. It may add evidence, caveats, freshness, missing reasons, and source trace, but it must not overwrite deterministic scoring, recommended actions, portfolio actions, lifecycle verdicts, user intent provenance, or existing cache keys.

## Shared Evidence/Context Contract Notes For Later Phases

The reusable layer should use consumer-neutral naming and support at least:

- `applicable_consumers` or equivalent consumer scope.
- `evidence_type` or `context_type`.
- `source` split into provider-neutral domain and provider implementation where needed.
- `as_of_date` and freshness status.
- `missing_reason` for absent or stale data.
- `replay_key` or equivalent trace key.
- Point-in-time semantics for lifecycle review to avoid future-data leakage.

The first foundation consumer can be Daily Radar v2, but the contract must not encode Daily Radar UI assumptions, ranking-only semantics, portfolio action assumptions, or trading-command language.

## Spec Sync Notes

Planned or open sync items, not completed facts:

1. Align Daily Radar institutional source naming across long-term spec, README, rollout checklist, and code after the source naming decision is accepted.
2. Update request budget language to distinguish TWSE RWD all-report calls from FinMind all-market calls if TWSE RWD remains the live provider.
3. Add explicit scoring/rule version traceability only when implemented and tested.
4. Add relative strength and market regime trace only when Phase 1 market index wiring is implemented.
5. Define shared evidence/context repository/schema only in the shared-layer phase, then wire consumers in Phase 2C/2D by read/reference behavior.

## Verification Baseline

Recommended minimal existing test set for Phase 0 documentation verification:

```bash
cd backend
uv run pytest -q \
  tests/test_daily_radar_api.py \
  tests/test_daily_radar_universe.py \
  tests/test_daily_radar_service.py \
  tests/test_daily_radar_scoring.py \
  tests/test_daily_radar_raw_data.py
```

Expected diff for Phase 0 is documentation-only. Code, tests, fixtures, migrations, workflows, and frontend files should remain unchanged.

## Next Suggested Phase

Proceed to Phase 1A only after the source naming decision is accepted. Phase 1A should keep deterministic ranking and focus on the next scoped implementation item without integrating `/analyze`, `/analyze/position`, portfolio diagnosis, or lifecycle review until their explicit Phase 2C/2D scope.
