# Compatibility Deprecation Audit

Date: 2026-06-12

Status: **No-go for removal**

This audit checks whether legacy compatibility fields can be removed after the investment-discipline Phase 5/6 work.

## Fields Audited

- `recommended_action`
- `trailing_stop`
- `trailing_stop_reason`
- `exit_reason`
- `command_language_deprecated`
- Analyze compatibility fields: `entry_zone`, `stop_loss`, `action_plan.action`

## Decision

Do not remove these fields yet.

The fields are no longer intended as primary user-facing copy, but they remain active compatibility and trace dependencies across API schemas, cache fidelity, portfolio history, tests, and internal analysis context.

## Dependency Findings

| Area | Current dependency | Removal readiness |
| --- | --- | --- |
| Backend API schema | `/analyze` and `/analyze/position` still expose legacy fields for compatibility. `AnalyzeResponse` cache restore validates historical `full_result` against the current schema. | Not ready |
| Position scorer | `build_position_risk_language()` translates `recommended_action`, `trailing_stop`, `trailing_stop_reason`, and `exit_reason` into additive risk-language fields and stores originals under `command_language_deprecated`. | Not ready |
| LLM analysis context | `LangChainAnalyzer` still receives `trailing_stop`, `recommended_action`, and `exit_reason` in the position context for explanatory text. This should be migrated to risk-language fields before removal. | Not ready |
| Historical cache | `stock_analysis_cache.recommended_action` and `stock_analysis_cache.full_result` preserve historical AnalyzeResponse snapshots. Old rows may contain only legacy fields or legacy-shaped position analysis. | Not ready |
| Portfolio history | v1 adds `risk_state`, `risk_state_label`, `discipline_triggers`, `risk_control_reference`, and `compatibility_source` to `/portfolio/latest-history` and `/portfolio/{portfolio_id}/history`. `PortfolioPage` now prefers `risk_state_label`; `recommended_action` remains legacy fallback for old rows. | Partially ready |
| Daily analysis log | `daily_analysis_log.recommended_action` is still written from analysis results and read by portfolio history endpoints. | Not ready |
| Frontend Analyze | `AnalyzePage` uses `command_language_deprecated.action_plan_action` only inside a collapsed `相容欄位（secondary）` block. This is safe as secondary display, but still a dependency. | Not ready |
| Frontend Portfolio | `PortfolioPage` uses `trailing_stop` as fallback for risk-control reference, and shows `recommended_action` / `exit_reason` only in secondary details or historical risk-label mapping. | Not ready |
| Specs / external clients | API specs still document these fields as compatibility contract. There is no repo-local evidence that external clients are free of these dependencies. | Not ready |
| Reports | Current Daily Radar rule-review and forward-validation reports do not depend on these fields. Portfolio history surfaces still do. | Partially ready |

## Required Closure Before Removal

1. Add replacement risk-language fields to history endpoints. v1 has implemented the additive fields; keep rollout verification before any removal:
   - `/portfolio/latest-history`
   - `/portfolio/{portfolio_id}/history`
   - any symbol-history consumer that needs historical position-state display
2. Backfill or version historical cache rows so `stock_analysis_cache.full_result` can be restored without requiring legacy fields.
3. Stop writing new legacy values to `daily_analysis_log.recommended_action` and `stock_analysis_cache.recommended_action`, or mark them as nullable archival fields with a migration plan.
4. Migrate `LangChainAnalyzer` position context from legacy action fields to:
   - `risk_state`
   - `discipline_triggers`
   - `observation_conditions`
   - `risk_control_reference`
5. Update frontend Portfolio historical display to read risk-language fields first and use legacy values only for old rows. v1 has moved the primary history label to `risk_state_label`; keep this as a removal gate.
6. Update API specs to mark the fields as deprecated with a removal version and minimum compatibility window.
7. Run a production data audit for historical `full_result` shape and cache usage before any DB migration.
8. Announce or document external-client migration guidance before removing fields from public responses.

## Safe Near-Term Policy

- Keep legacy fields in API responses and DB/cache rows.
- Keep primary UI copy on risk-language fields.
- Allow legacy fields only in:
  - collapsed secondary trace blocks,
  - internal compatibility docs,
  - historical fallback paths,
  - cache restore paths.
- Do not add new primary UI surfaces that depend on these fields.

## Suggested Follow-Up Phase

Continue the versioned compatibility migration plan:

1. v1 is additive: history responses expose risk-language fields and frontend history views prefer them, with legacy fallback for old rows.
2. v2 should add or keep automated guard coverage proving history primary copy does not depend on `recommended_action`.
3. v3 should run the read-only production SQL audit for legacy-only `stock_analysis_cache.full_result` rows.
4. v4 should decide whether DB columns and response fields can be hidden, soft-deprecated, or removed after one compatibility window.
