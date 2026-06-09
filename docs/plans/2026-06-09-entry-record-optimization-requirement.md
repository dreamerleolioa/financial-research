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

When creating a new position, the user should provide four additional fields:

1. Entry reason
2. Planned holding period
3. Default stop rule
4. Add-entry condition

These fields should use fixed options. Optional notes may exist, but they are secondary and must not replace fixed options.

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

Backfilled plans must not be treated as original pre-entry plans.

## Review Semantics

With this requirement, future lifecycle review can distinguish:

- planned scale-in vs averaging down
- rule-based stop violation vs strategy still valid
- short-term trade overstaying vs medium-term planned hold
- strategy failure vs execution deviation
- insufficient context vs recorded plan followed

If fixed fields are missing, lifecycle review should continue to work but must show:

```text
decision_context: insufficient
```

## Phased Delivery

### Phase 0: Requirement And Decision Log

Goal:

- Finalize this requirement document.
- Confirm fixed options and non-goals.
- Do not touch backend or frontend implementation.

Expected outcome:

- This document becomes the source of truth for later implementation prompts.

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

## Open Questions

1. Should `entry_reason` be single-select only in v1?
2. Should `add_entry_condition` allow multiple selections?
3. Should `no_averaging_down` be a standalone toggle rather than an option?
4. Should system-derived stop rules store the computed reference price at entry time?
5. Should `/analyze` action_plan be optionally imported into the entry form as prefilled values?
