# Entry Record Optimization Requirement

> Status: Future planning note  
> Date: 2026-06-09  
> Scope: Fixed-option entry decision context capture for new positions and add-entry events. This is intentionally not an implementation diff.

## Why This Exists

Current lifecycle review can already evaluate trading behavior after the fact: entries, add entries, exits, realized results, market-regime snapshots, and whether the sequence looked disciplined.

However, it still cannot reliably answer whether the original strategy was correct, because the system may not know what the user intended at entry time.

Without original entry context, the review can say:

- The user averaged down into weakness.
- The user exited after a large giveback.
- The position had insufficient decision context.

But it cannot confidently say:

- The user followed the original plan.
- The add entry was allowed by the original scale-in rule.
- The stop was violated but ignored.
- The strategy thesis failed.

This requirement closes that gap by making entry records capture a small set of fixed decision fields.

## Product Principle

Do not ask the user to write a long journal.

Ask the user to choose structured options that are easy to fill, easy to analyze, and stable enough for future lifecycle review.

Preferred shape:

```text
fixed options first -> optional note second
```

Avoid:

```text
free-text journal -> later LLM inference
```

The system must not infer user intent from price movement, PnL, or later outcomes.

## Minimal User Input

When creating a new position, the user should provide four fixed-option primary fields:

1. Entry reason
2. Planned holding period
3. Default stop rule
4. Add-entry condition

These four fields are the minimum required entry context. Optional notes may exist, but they are secondary only. A note cannot satisfy, replace, or override the fixed options, and it must not become the primary data source for review.

## Fixed Option Taxonomy

### Entry Reason

Suggested values:

| Value | User Label | Meaning |
| --- | --- | --- |
| `breakout_confirmation` | 突破確認 | Price broke through a key resistance or prior high. |
| `pullback_held_support` | 拉回守住支撐 | Pullback held a known support area. |
| `pullback_held_ma20` | 拉回守住 MA20 | Pullback held MA20 or similar medium-term trend support. |
| `institutional_flow_strengthened` | 法人籌碼轉強 | Institutional flow strengthened. |
| `fundamental_thesis_improved` | 基本面改善 | Fundamental thesis improved. |
| `event_or_news_catalyst` | 事件題材發酵 | Event/news catalyst improved setup. |
| `long_term_accumulation` | 長期佈局 | Long-term accumulation thesis. |
| `value_revaluation` | 低估修復 | Valuation re-rating or undervaluation recovery. |
| `other` | 其他 | User chooses other; optional note can explain. |

Default recommendation: single-select for v1.

### Planned Holding Period

Suggested values:

| Value | User Label |
| --- | --- |
| `short_term` | 短線：1-2 週 |
| `swing` | 波段：2-8 週 |
| `medium_term` | 中線：1-3 個月 |
| `long_term` | 長期：3 個月以上 |

### Default Stop Rule

Suggested values:

| Value | User Label |
| --- | --- |
| `break_20d_low` | 跌破近 20 日低點 |
| `break_ma20` | 跌破 MA20 |
| `break_ma60` | 跌破 MA60 |
| `cost_minus_pct` | 跌破成本價 - N% |
| `fixed_price` | 固定價格 |
| `no_stop_recorded` | 不設定 / 尚未設定 |

For system-derived rules, the UI may display the current reference value at entry time, such as:

```text
停損規則：跌破 MA20
當下 MA20：142.5
```

### Add-Entry Condition

Suggested values:

| Value | User Label |
| --- | --- |
| `no_add_entry` | 不加碼，只做單筆 |
| `breakout_above_prior_high` | 突破前高再加碼 |
| `pullback_holds_ma20` | 拉回 MA20 不破再加碼 |
| `pullback_holds_support` | 拉回支撐不破再加碼 |
| `institutional_flow_continues` | 法人持續買超再加碼 |
| `profit_threshold_reached` | 獲利達 N% 後再加碼 |
| `data_quality_complete_only` | 只在資料品質完整時加碼 |
| `no_averaging_down` | 不向下攤平 |
| `custom_plan_required` | 需要自訂計畫，暫不自動判讀 |

V1 should include `no_averaging_down` because future lifecycle review must distinguish planned scale-in from averaging down after weakness.

## Relationship To Existing System

This requirement builds on existing lifecycle work:

- PositionEvent already supports entry/add/exit event facts.
- PositionEvent already has reason fields such as `reason_category`, `reason_code`, `plan_adherence`, and `confidence_level`.
- PositionLifecyclePlan already has plan-level fields such as `thesis`, `setup_type`, `planned_holding_period`, `planned_invalidation`, `planned_stop_price`, `planned_target_or_scale_out_rule`, `planned_risk_pct`, and `position_sizing_rationale`.
- Lifecycle review already supports `decision_context: insufficient`.

This requirement focuses on the missing capture surface: asking the user for structured entry context at the moment of entry or add-entry.

## Non-Goals

This requirement does not implement:

- Backtesting or confidence calibration.
- LLM-based intent inference.
- Free-text trading journal analysis.
- Full advisory-mode compliance.
- Portfolio-level risk budgeting.
- New lifecycle scoring models.
- Automatic strategy correctness judgment without recorded user intent.

## Data Semantics

The system should preserve provenance:

| Source | Meaning |
| --- | --- |
| `user_recorded_at_event_time` | User filled the context when creating the entry/add event. |
| `user_backfilled` | User filled the plan after the entry already existed. |
| `synthetic_from_portfolio_row` | System reconstructed event facts from old portfolio rows. |
| `manual_record_correction` | User corrected a record manually. |
| `not_recorded` | Context was not recorded. |

Backfilled plans can improve future review quality, but they must not be treated as original entry-time intent.

## Review Semantics

Post-trade behavior review and original strategy correctness review are different questions. Existing lifecycle review can evaluate what happened after entry. Original strategy correctness requires fixed-option context recorded at event time and must not be inferred from price movement, PnL, or later outcomes.

With this requirement, future lifecycle review can distinguish:

- planned scale-in vs averaging down
- rule-based stop violation vs strategy still valid
- short-term trade overstaying vs medium-term planned hold
- strategy failure vs execution deviation
- insufficient context vs recorded plan followed

If fixed event-time fields are missing, lifecycle review should continue to work but must show:

```text
decision_context: insufficient
```

This remains true when only optional notes exist, provenance is `synthetic_from_portfolio_row` or `not_recorded`, or the plan was backfilled after entry.

## Phased Delivery

### Phase 0: Requirement And Decision Log

Goal:

- Finalize this requirement document.
- Confirm fixed options and non-goals.
- Do not touch backend or frontend implementation.
- Do not add migrations, change API behavior, promote this to docs/specs, or execute Phase A, B, C, D, E, or F.

Expected outcome:

- This documentation-only phase makes the requirement precise enough for later implementation prompts.

### Phase A: Fixed Option Taxonomy And Validation Contract

Goal:

- Define backend/frontend shared option values.
- Ensure allowed values map cleanly to existing PositionEvent and PositionLifecyclePlan fields.
- Add validation tests before UI work.

Expected outcome:

- Fixed-option values are stable and tested.

### Phase B: Entry Context Capture

Goal:

- Extend new position creation to capture:
  - entry reason
  - planned holding period
  - default stop rule
  - add-entry condition
- Persist the context with event-time provenance.

Expected outcome:

- New entries have enough context for future lifecycle review.

### Phase C: Add-Entry Context Capture

Goal:

- Add an explicit add-entry flow.
- Do not infer add-entry from editing an existing portfolio row.
- Capture add-entry reason and whether it follows the original add-entry condition.

Expected outcome:

- The system can distinguish planned scale-in from averaging down.

### Phase D: Existing Position Backfill And Provenance

Goal:

- Let users fill missing plans for existing active positions.
- Mark those plans as `user_backfilled` and `created_after_entry = true`.

Expected outcome:

- Old positions can improve future review quality without pretending the plan existed at entry time.

### Phase E: Lifecycle Review Integration

Goal:

- Use the fixed options in lifecycle review.
- Improve review labels, caveats, and next-operation rules.
- Preserve `decision_context: insufficient` when data is missing.

Expected outcome:

- Lifecycle review can evaluate plan adherence without inferring unrecorded intent.

### Phase F: Spec Promotion Review

Goal:

- Decide which contracts are stable enough to promote into core specs.
- Update backend API spec only after implementation behavior is stable.

Expected outcome:

- Long-term API/schema facts are documented in durable spec files.

## Decision Log

| Decision | Recommendation | Impact |
| --- | --- | --- |
| Use fixed options instead of free text | Yes | Enables deterministic review and avoids LLM intent inference. |
| Keep optional notes | Yes, secondary only | Allows nuance without weakening structured analysis. |
| Require only four fields for v1 | Yes | Keeps entry flow lightweight. |
| Persist backfilled plans as original intent | No | Prevents false historical accuracy. |
| Infer add-entry from portfolio edit | No | Avoids accidental intent creation. |
| Promote immediately to specs | No | Keep in docs/plans until behavior stabilizes. |
| Keep `entry_reason` single-select | Yes | Matches the implemented scalar enum contract and keeps entry capture lightweight. |
| Keep `add_entry_condition` single-select | Yes | Matches the implemented scalar enum contract and avoids ambiguous plan-adherence interpretation. |
| Split `no_averaging_down` into a standalone toggle | No | Keep it as one `add_entry_condition` option for v1; revisit only if product needs independent scale-in policy controls. |
| Store system-derived stop reference price at entry time | No for v1 | Store the fixed stop rule enum only; reference prices can be recomputed from point-in-time market data and should not be promoted until explicitly implemented. |
| Import `/analyze` action plan into entry form | No for v1 | Avoid converting system suggestions into user intent; any future prefill must require explicit user confirmation before saving. |

## Resolved Phase F Follow-Up Questions

### Phase F Promotion Review Result

Completed on 2026-06-10 after Phase A-E behavior stabilized. The following implemented contracts were promoted to durable specs:

- `docs/specs/backend-api-technical-spec.md` now records the stable API/schema contracts for `entry_record` on `POST /portfolio`, `GET /portfolio/decision-context-status`, `GET /portfolio/{portfolio_id}/lifecycle-plan`, `PUT /portfolio/{portfolio_id}/lifecycle-plan/backfill`, `POST /portfolio/{portfolio_id}/add-entry`, `GET /portfolio/groups/{position_group_id}/events`, and lifecycle review `decision_context` fixed-option fields.
- `docs/specs/ai-stock-sentinel-architecture-spec.md` now records the stable architecture semantics for fixed-options-first entry context, optional notes as secondary data, provenance, backfilled plan caveats, `decision_context: insufficient`, deterministic lifecycle labels, raw-score visibility, and the no-LLM intent inference boundary.

The discussion context in this plan remains intact. The product/design questions below were resolved after Phase F and should not be treated as new implementation scope.

1. `entry_reason` remains single-select in v1.
2. `add_entry_condition` remains single-select in v1.
3. `no_averaging_down` remains an `add_entry_condition` option, not a standalone toggle.
4. System-derived stop rules do not store computed entry-time reference prices in v1.
5. `/analyze` action plan is not imported into the entry form in v1; future prefill, if added, must require explicit user confirmation before saving user intent.
