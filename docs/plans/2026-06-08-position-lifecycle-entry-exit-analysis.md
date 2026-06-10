# Position Lifecycle Entry/Exit Analysis Plan

> Status: Future planning note
> Date: 2026-06-08
> Scope: Full position lifecycle review for partial entries and partial exits. This is intentionally separate from the Single Trade Review MVP.

## Why This Exists

The Single Trade Review MVP reviews one closed `UserPortfolio` row at a time. That works for a first version because one closed row can represent one realized exit batch.

However, real trading often has both partial entries and partial exits:

- A user may build a position across several buy decisions.
- A user may reduce or close the position across several sell decisions.
- Each buy and sell can happen under different technical conditions, market regimes, conviction levels, and risk/reward context.

If the system reviews the whole lifecycle using only one `entry_date`, one `entry_price`, one `exit_date`, and one `exit_price`, the result can be misleading. A full lifecycle review needs event-level or lot-level data instead of only row-level closed portfolio data.

## Relationship To Current MVP

Current MVP remains unchanged:

- Review unit: one closed `UserPortfolio` row equals one realized exit batch.
- `position_group_id` groups related closed rows visually on `/portfolio/closed`.
- Group headers do not provide lifecycle review.
- Each exit batch has its own saved `trade_review`.
- MVP does not reconstruct partial-entry decisions.
- MVP does not merge multiple exit batches into one lifecycle judgment.

This future plan starts after the MVP is implemented and validated.

## Product Question

For one original position lifecycle, including all entries and exits, what did the overall operation do well or poorly?

The lifecycle review should answer questions such as:

- Was the initial entry technically reasonable?
- Were add-on buys justified, or did they average down into weakness?
- Did the position sizing become too aggressive relative to signal quality?
- Did partial exits protect profit, reduce risk, or cut winners too early?
- Did the final exit happen after clear deterioration, or after avoidable delay?
- Was the overall sequence coherent, or did each action contradict the prior plan?

## Core Design Shift

Single Trade Review is row-centric.

Position Lifecycle Review should be event-centric.

Instead of treating `UserPortfolio` rows as the source of truth for every decision, the next version should model position events:

```text
position_event
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
  note
  created_at
```

`fees` and `taxes` remain event-ledger fields, but they should not both be treated as required manual inputs. Broker handling fees and securities transaction taxes are distinct calculations: the system should calculate both by default from configurable market/product/broker defaults, then record the resulting amounts on the event. For Taiwan stocks, the default transaction tax is generally sell-side and product-dependent, for example common stock around 0.3%, while broker handling fees may depend on broker rate, discount, minimum fee, or an actual-fee override. These defaults must be configurable, not permanent hardcoded constants.

Suggested `event_type` values:

- `initial_entry`
- `add_entry`
- `partial_exit`
- `full_exit`
- `manual_adjustment`

This gives the review engine a chronological operation sequence instead of forcing it to infer intent from closed rows.

## Data Model Options

### Option A: Event Ledger

Add a `position_event` table and treat every buy/sell/change as an immutable event.

Pros:

- Best fit for lifecycle review.
- Can represent partial entries and partial exits naturally.
- Avoids guessing from current active/closed row state.
- Supports future audit trail and richer analytics.

Cons:

- Requires more migration and route changes.
- Current portfolio flows must write events consistently.
- Existing rows need a best-effort backfill.

### Option B: Lot Table

Add a `position_lot` table for each entry batch, then map exits to lots.

Pros:

- Good for cost basis and realized PnL attribution.
- Can answer which entry lots were closed by which exits.

Cons:

- More complex if the product does not need tax-lot precision.
- Requires choosing FIFO, average cost, or explicit lot matching.
- Lifecycle reasoning still needs event chronology.

### Recommendation

Start with an event ledger.

If tax-lot precision or exact realized attribution becomes necessary, add `position_lot` later or derive lots from events using an explicit matching policy.

## Review Units

This future feature should support three related but separate review units:

| Review Unit | Source | Purpose |
| --- | --- | --- |
| Entry event review | One `initial_entry` or `add_entry` event | Judge the technical quality of that specific buy decision. |
| Exit event review | One `partial_exit` or `full_exit` event | Judge whether that sell decision protected capital/profit well. |
| Lifecycle review | All events under one `position_group_id` | Judge the overall sequence and position management. |

The current `trade_review` table can continue to serve exit-batch review. A future lifecycle version should use a separate table or clearly separate review type, for example:

```text
position_lifecycle_review
  id
  user_id
  position_group_id
  symbol
  review_version
  review_result JSONB
  evidence_payload JSONB
  llm_summary TEXT NULL
  created_at
  updated_at
```

Do not overload `trade_review` with lifecycle-level conclusions unless a `review_type` discriminator is added deliberately.

## Analysis Semantics

The lifecycle review should be chronological and point-in-time.

For every event:

- Entry event analysis can only use data up to that entry date.
- Exit event analysis can only use data up to that exit date.
- Position-level path metrics can use the full period, but only as outcome facts.
- The review must not judge an early entry using a later selloff that was not yet knowable.
- The review must not claim the exact high or low was the correct action point.

## Suggested Metrics

### Lifecycle Metrics

- total_realized_pnl
- total_return_pct_on_weighted_cost
- max_position_size
- max_capital_at_risk
- average_entry_price_over_time
- weighted_average_entry_price
- final_exit_date
- total_holding_days_from_first_entry
- active_exposure_days
- max_unrealized_profit_pct
- max_unrealized_drawdown_pct
- profit_giveback_pct

### Entry Sequence Metrics

- entry_count
- add_entry_count
- initial_entry_vs_ma20_pct
- each_add_entry_vs_ma20_pct
- average_up_count
- average_down_count
- add_after_breakdown_count
- add_after_confirmation_count
- time_between_entries
- price_distance_between_entries

### Exit Sequence Metrics

- exit_count
- partial_exit_count
- first_exit_return_pct
- final_exit_return_pct
- percentage_sold_before_peak
- percentage_sold_after_breakdown
- profit_protected_by_partial_exits
- residual_position_giveback_pct

## Classification Ideas

### Entry Sequence Classifications

- `disciplined_scaling_in`: add entries followed confirmation and position size stayed controlled.
- `chasing_scale_in`: add entries occurred after extension without sufficient pullback or confirmation.
- `averaging_down_into_weakness`: add entries happened during breakdown or downtrend without recovery evidence.
- `early_probe_then_confirm`: small initial entry followed by stronger add after confirmation.
- `oversized_initial_entry`: first entry carried too much exposure before confirmation.
- `insufficient_data`: not enough price or event data.

### Exit Sequence Classifications

- `disciplined_scale_out`: partial exits reduced risk or protected profit near weakening signals.
- `premature_scale_out`: too much was sold before trend confirmation weakened.
- `late_scale_out`: exits happened only after major giveback or breakdown.
- `risk_reduction_exit`: partial exit improved risk profile despite imperfect timing.
- `incoherent_exit_sequence`: exits contradicted the prior entry/holding logic without clear signal change.
- `insufficient_data`: not enough price or event data.

### Lifecycle Classifications

- `coherent_position_management`
- `good_entry_poor_exit`
- `weak_entry_saved_by_exit`
- `overtraded_position`
- `held_winner_well`
- `gave_back_winner`
- `averaged_down_failed`
- `insufficient_data`

Every classification should include confidence, supporting signals, conflicting signals, and caveats.

## Evidence Payload Boundary

The lifecycle evidence payload should be copyable but not huge.

Allowed:

- event list with dates, type, price, quantity, and derived point-in-time indicators.
- summarized lifecycle metrics.
- capped detected event list.
- market regime snapshots at each entry/exit event.
- data quality notes.

Forbidden:

- full OHLCV arrays.
- raw K-line series embedded in every review.
- raw LLM prompts as evidence.
- inferred user intent that was not recorded.

Example high-level shape:

```json
{
  "position_group_id": "...",
  "symbol": "2330.TW",
  "events": [
    {
      "event_type": "initial_entry",
      "event_date": "2026-01-05",
      "price": 980.0,
      "quantity": 50,
      "indicators": {
        "ma20": 950.0,
        "rsi14": 62.0,
        "market_regime": "uptrend"
      }
    },
    {
      "event_type": "partial_exit",
      "event_date": "2026-02-14",
      "price": 1040.0,
      "quantity": 25,
      "indicators": {
        "ma20": 1055.0,
        "rsi14": 48.0,
        "market_regime": "uptrend"
      }
    }
  ],
  "lifecycle_metrics": {},
  "entry_sequence_review": {},
  "exit_sequence_review": {},
  "data_quality": {}
}
```

## Requirement Addendum: Decision-Aware Lifecycle Review

The lifecycle review should not only reconstruct what happened. It should also preserve why each action happened and whether the action followed the user's own plan.

Without decision context, the system can still produce useful price-path observations, but it cannot reliably decide whether an action was a mistake, disciplined risk control, or a valid strategy adaptation.

The next version should therefore treat the event ledger as the factual backbone and add fixed decision fields as the analysis backbone.

Minimum review principle:

```text
accurate metrics -> stable classifications -> clear templates
```

- Accurate metrics: every number is deterministic, formula-based, and reproducible from events and market data.
- Stable classifications: every label is produced by explicit rules and returns the same result for the same event sequence.
- Clear templates: every user-facing sentence traces back to a metric, event, classification, or recorded reason.

## Score Visibility Boundary

The system should keep a scoring engine, but raw scores should not become the primary user-facing decision surface.

Internal scores are still useful and should remain available for:

- ranking events, reviews, or candidates.
- deterministic guardrails and downgrade rules.
- backtesting, calibration, and future threshold review.
- debugging classification behavior.
- advanced trace and evidence inspection.

Default user-facing lifecycle review should prefer:

- tiers such as `high` / `medium` / `low`.
- labels such as `disciplined_scale_out`, `premature_scale_out`, or `decision_context: insufficient`.
- supporting signals, conflicting signals, caveats, and source events.
- concrete reasons and next-operation rules.

Exact `0-100` values such as `decision_quality_score`, `plan_adherence_score`, `signal_confidence`, or future lifecycle sub-scores should be treated as internal or advanced trace fields unless they have been calibrated and deliberately promoted into product language.

Do not present raw scores as win rate, alpha probability, recommendation strength, or investment advice. Until calibration proves predictive value, the score language should mean `signal strength` or `rule confidence`, not expected return.

## Operation Reason Taxonomy

Each entry, add, trim, and exit event should store fixed reason fields. Free-text notes are useful, but fixed values are required for reliable analysis.

Suggested event-level fields:

```text
event_type
reason_category
reason_code
plan_adherence
confidence_level
note
```

Suggested `event_type` values:

- `initial_entry`
- `add_entry`
- `partial_exit`
- `full_exit`
- `manual_adjustment`

Suggested `reason_category` values:

- `technical`
- `institutional_flow`
- `fundamental`
- `news`
- `risk_control`
- `plan_execution`
- `emotional`
- `record_correction`

Suggested entry/add `reason_code` values:

- `breakout_confirmation`
- `pullback_held_support`
- `pullback_held_ma20`
- `institutional_flow_strengthened`
- `fundamental_thesis_improved`
- `planned_scale_in`
- `averaging_down`
- `chasing_momentum`
- `manual_record_correction`

Suggested exit/trim `reason_code` values:

- `target_reached`
- `trailing_stop_hit`
- `support_broken`
- `ma20_lost`
- `institutional_flow_weakened`
- `fundamental_thesis_broken`
- `news_risk_increased`
- `risk_reduction`
- `profit_protection`
- `planned_scale_out`
- `stop_loss`
- `emotional_exit`
- `manual_record_correction`

Suggested `plan_adherence` values:

- `yes`
- `partial`
- `no`
- `not_recorded`

Suggested `confidence_level` values:

- `high`
- `medium`
- `low`

Example event decision payload:

```json
{
  "event_type": "add_entry",
  "reason_category": "technical",
  "reason_code": "pullback_held_ma20",
  "plan_adherence": "yes",
  "confidence_level": "medium",
  "note": "Volume contracted on pullback and institutional flow remained positive."
}
```

## User Plan And Adherence Fields

A lifecycle review needs a baseline plan. Otherwise the system can only compare the user against generic rules, not against the user's intended strategy.

Minimum lifecycle-level plan fields:

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
```

Suggested `setup_type` values:

- `breakout`
- `pullback`
- `mean_reversion`
- `value_revaluation`
- `earnings_or_event`
- `momentum_continuation`
- `long_term_accumulation`
- `defensive_rebalance`
- `other`

Suggested `planned_holding_period` values:

- `short_term`
- `swing`
- `medium_term`
- `long_term`

The most important field is `planned_invalidation`. If the system does not know what would prove the idea wrong, it cannot distinguish disciplined holding from refusal to admit a broken thesis.

Minimum valid lifecycle plan example:

```json
{
  "thesis": "Breakout from consolidation with institutional accumulation.",
  "setup_type": "breakout",
  "planned_holding_period": "swing",
  "planned_invalidation": "Close below MA20 with institutional flow turning distribution.",
  "planned_stop_price": 950.0,
  "planned_target_or_scale_out_rule": "Trim 50% near prior resistance, trail the rest above MA10.",
  "planned_risk_amount": 5000.0,
  "planned_risk_pct": 1.0,
  "position_sizing_rationale": "Initial probe only; add after breakout retest."
}
```

## Deterministic Metrics And Classifications

The lifecycle review should remain deterministic. LLMs must not calculate metrics, assign classifications, or decide whether an action was correct.

Additional metrics to support professional review:

- `planned_1r_amount`
- `realized_r_multiple`
- `mae_pct`
- `mae_r_multiple`
- `mfe_pct`
- `mfe_r_multiple`
- `mfe_capture_rate`
- `plan_adherence_score`
- `decision_quality_score`
- `capital_at_risk_by_event`
- `exposure_curve`
- `benchmark_relative_return_pct`
- `sector_relative_return_pct`

`plan_adherence_score`, `decision_quality_score`, and any future lifecycle score are trace fields first. They may drive deterministic tiers or labels, but the default review should not lead with exact `0-100` score values.

Classification examples should be rule-driven.

Example `averaging_down_into_weakness` rule shape:

```text
event_type = add_entry
AND reason_code = averaging_down
AND add price < previous weighted average cost
AND event close < MA20
AND market_regime not in recovery_confirmed / uptrend
```

Example `disciplined_scale_out` rule shape:

```text
event_type in partial_exit / full_exit
AND realized return is positive
AND reason_code in target_reached / trailing_stop_hit / risk_reduction / profit_protection
AND plan_adherence in yes / partial
AND remaining capital at risk decreases after the event
```

Example `premature_scale_out` rule shape:

```text
event_type = partial_exit
AND sold percentage is high relative to remaining position
AND event market_regime in uptrend / strong_momentum
AND no recorded invalidation or trailing stop trigger
AND reason_code not in target_reached / planned_scale_out / risk_reduction
```

Every classification should include:

- `classification`
- `confidence` as a tier or calibrated label, not a prominent raw score
- `supporting_signals`
- `conflicting_signals`
- `caveats`
- `source_events`

## Review Output Template

The first version should use fixed templates rather than LLM-generated summaries.

Template output should include:

- Overall conclusion.
- What worked.
- What needs review.
- Event-level evidence.
- Next-operation rules.
- Data quality notes.

Template output should lead with labels, reasons, and caveats. If numeric scores are included, they should appear only as secondary trace detail or advanced evidence, not as the headline conclusion.

Example template shape:

```text
Overall conclusion:
This lifecycle is classified as good_entry_poor_exit with medium confidence.

What worked:
The initial entry aligned with breakout_confirmation and institutional flow remained supportive.

What needs review:
The second add_entry was classified as averaging_down_into_weakness because it happened below MA20 and below the previous weighted average cost.

Evidence:
- 2026-01-18 add_entry: price 940, MA20 955, reason_code averaging_down, plan_adherence no.
- Maximum favorable excursion reached +14.2%, but final captured profit was +5.1%.

Next-operation rules:
- Do not add below MA20 unless a recovery confirmation event is present.
- After profit exceeds 10%, activate a MA10 or planned trailing-stop rule for the remaining position.
```

Do not output vague advice such as "be more careful". Every suggested improvement should be concrete enough to follow during the next trade.

## Optional LLM Boundary

LLM usage is optional and should come after deterministic review output is stable.

Allowed LLM responsibilities:

- Rewrite structured review results into more natural language.
- Help the user discuss conflicting signals.
- Suggest alternative rule wording based only on the evidence payload.
- Summarize user-provided notes without inventing missing intent.

Forbidden LLM responsibilities:

- Calculate PnL, R-multiple, MAE, MFE, weighted cost, or indicators.
- Assign lifecycle classifications directly.
- Infer user intent when no reason or plan was recorded.
- Judge an action using future data that was unavailable at the event date.
- Replace deterministic template output as the source of truth.
- Turn raw internal scores into advisory claims, win-rate claims, or stronger conviction than the deterministic labels support.

Product recommendation:

- Built-in review should use deterministic templates.
- Provide a copyable evidence payload for deeper discussion in an external AI chat if needed.
- Add optional `llm_summary` only after the deterministic metrics, classifications, and templates are accepted.

## API Shape

Potential future endpoints:

```text
GET /portfolio/groups/{position_group_id}/events
GET /portfolio/groups/{position_group_id}/lifecycle-review
POST /portfolio/groups/{position_group_id}/lifecycle-review
```

Behavior should mirror the MVP persistence rule, with the later stale-review correction that saved reviews are reused only while their event-ledger and lifecycle-plan inputs remain unchanged:

- `GET` returns a saved review if present.
- `POST` creates the first saved review if absent.
- If a saved review exists and its source event/plan watermarks are unchanged, `POST` returns it rather than recomputing.
- If later `PositionEvent` rows or a `PositionLifecyclePlan` backfill/update are newer than the saved review, `POST` recomputes and updates the same versioned review row so the whole-lifecycle analysis reflects the current ledger.
- Future narrative/LLM refresh must be explicit and version-aware.
- Save `review_result` and `evidence_payload` atomically.

## Frontend UX

This should not replace the MVP exit-batch review.

Suggested future UI:

- `/portfolio/closed` group header can show a `整體部位檢討` button only after lifecycle review exists.
- Exit batch rows keep their existing `檢討分析` buttons.
- Lifecycle modal shows a chronological timeline:
  - entries
  - add entries
  - partial exits
  - final exit
- Each event can expand into point-in-time indicators.
- Summary sections:
  - `整體結果`
  - `分批進場檢討`
  - `持倉管理檢討`
  - `分批出場檢討`
  - `下次操作規則`
  - `資料品質`
- Default presentation should show tiers, labels, reasons, and source events rather than raw `0-100` lifecycle scores.
- Raw score breakdowns may be available in a collapsed advanced trace or copyable evidence payload, but should not be the primary visual hierarchy.

The UI must clearly distinguish:

- single exit-batch review: one sell decision.
- lifecycle review: the whole multi-entry/multi-exit operation.

Entry and exit forms should show system-calculated broker handling fee and transaction tax as separate amounts, record both into the event ledger, and avoid requiring Taiwan securities transaction tax as manual user input. Broker handling fee may expose broker discount, minimum-fee, or actual-fee override controls; transaction tax should default from configurable market/product/broker rules, especially sell-side Taiwan stock defaults. Existing close-flow compatibility should be preserved by keeping current fee/tax ledger values readable and writable while adding default calculation on top.

## Migration / Backfill Notes

Existing data may not contain true event history.

Backfill should be conservative:

- Existing active rows can become one synthetic `initial_entry` event.
- Existing full-close rows can become one synthetic `initial_entry` and one `full_exit` event.
- Existing partial-close rows can become synthetic `partial_exit` events if `position_group_id` connects them.
- Historical add-entry decisions cannot be reconstructed unless they were recorded before this feature exists.

Backfilled events should carry a `source` or `data_quality` note such as `synthetic_from_portfolio_row`.

Do not pretend reconstructed events are exact user decisions.

## Existing Position Decision-Context Backfill UX

Existing active positions that were created before this feature should remain reviewable, but the system must not pretend they had a complete original plan.

Recommended UX:

- Existing active positions show a `missing operation plan` or equivalent prompt when lifecycle review fields are absent.
- The user can click a `backfill trade plan` action to fill thesis, setup type, invalidation, planned stop, planned risk, and scale-out rule.
- The backfill form must clearly state: `This plan is filled after entry. It will be used for future review, but it is not treated as the original pre-entry plan.`
- If the user does not backfill the plan, position diagnosis and lifecycle review can still run, but lifecycle review must show `decision_context: insufficient`.
- Future new positions, add entries, trims, and exits should capture fixed reason fields at event time instead of relying on later backfill.

Recommended provenance values:

```text
synthetic_from_portfolio_row
user_backfilled
user_recorded_at_event_time
manual_record_correction
not_recorded
```

Existing active row backfill behavior:

```json
{
  "event_type": "initial_entry",
  "source": "synthetic_from_portfolio_row",
  "event_date": "<existing entry_date>",
  "price": "<existing entry_price>",
  "quantity": "<existing quantity>",
  "plan_adherence": "not_recorded",
  "decision_context": "insufficient"
}
```

If the user later fills the missing plan fields, store those fields with:

```json
{
  "source": "user_backfilled",
  "created_after_entry": true
}
```

Do not auto-default intent-sensitive fields such as `reason_code`, `plan_adherence`, `confidence_level`, `planned_invalidation`, or `planned_risk_pct`. These fields should remain `not_recorded` or `unknown` until the user explicitly supplies them.

## Implementation Phases For Future Discussion

### Phase A: Event Ledger Foundation

- Add `position_event` model and migration.
- Write events when creating a new position.
- Write events when partially or fully closing a position.
- Add default fee/tax calculation for event writing, with broker handling fees and transaction taxes recorded as ledger fields and Taiwan stock defaults supplied by configurable market/product/broker settings.
- Backfill synthetic events for existing rows.
- Keep current Single Trade Review behavior unchanged.

### Phase A2: Decision Context Foundation

- Add fixed operation reason fields to lifecycle events.
- Add lifecycle-level plan fields such as thesis, invalidation, planned stop, planned risk, and scale-out rule.
- Distinguish real user-entered events from synthetic backfill and manual record corrections.
- Keep free-text notes optional and secondary to fixed reason fields.
- Do not require LLM usage for any decision-context capture.

### Phase B: Event Timeline API And UI

- Add read-only event timeline endpoint.
- Show timeline under grouped closed positions.
- No lifecycle judgment yet.

### Phase C: Lifecycle Metrics Engine

- Compute lifecycle metrics from event chronology.
- Compute point-in-time indicator snapshots per event.
- Compute R-multiple, MAE, MFE, MFE capture rate, exposure curve, and plan adherence metrics when enough data exists.
- Keep score-like metrics as internal or advanced trace fields unless later calibrated and promoted.
- Produce deterministic metrics and evidence payloads for later lifecycle review persistence, but do not save lifecycle reviews in this phase.

### Phase D: Deterministic Classification And Template Review

- Add rule-based entry sequence, exit sequence, lifecycle, and plan-adherence classifications.
- Add fixed template output for overall conclusion, strengths, issues, evidence, and next-operation rules.
- Add reusable entry-event and exit-event review fragments so the lifecycle result can explain individual buy/sell decisions without a separate event-review API yet.
- Ensure every template sentence traces back to event data, metrics, classifications, or recorded reasons.
- Make templates lead with tiers, labels, reasons, caveats, and source events rather than exact `0-100` scores.
- Keep `llm_summary` disabled by default.

### Phase E: Lifecycle Review UI

- Persist lifecycle reviews separately from `trade_review`, including `review_version`, `review_result`, and `evidence_payload`.
- Keep source freshness explicit: unchanged event/plan inputs reuse the saved review, while later ledger or plan updates recompute the same deterministic review version in place.
- Add group-level lifecycle review action.
- Show timeline-based review.
- Show event-level review fragments inside the lifecycle timeline, while keeping standalone entry/exit event review APIs as future extension unless explicitly scoped.
- Add copyable lifecycle evidence payload.
- Show whether the review is based on real events, synthetic events, or mixed provenance.
- Default UI should show tiers, labels, reasons, and data-quality warnings; raw score breakdown belongs only in advanced trace or evidence payload.
- Clearly separate single exit-batch review from whole-lifecycle review.

### Phase F: Optional Narrative Layer

- Add optional LLM summary only after deterministic metrics and classifications are stable.
- LLM must summarize structured facts, not compute signals or invent intent.
- LLM must not convert raw scores into advisory confidence, win-rate language, or stronger claims than the deterministic labels support.

## Test-First Acceptance Plan

Future implementation should be test-first. Suggested acceptance areas:

- Model tests: `position_event`, lifecycle plan fields, reason fields, and lifecycle review persistence are present and required where appropriate.
- Event writing tests: creating, adding, partially exiting, fully exiting, and manual adjustments write the correct event types.
- Backfill tests: existing active and closed rows become synthetic events with explicit data-quality notes.
- Metrics tests: weighted cost, R-multiple, MAE, MFE, MFE capture, exposure curve, realized PnL, and profit giveback are deterministic.
- Fee/tax tests: broker handling fees and transaction taxes are calculated separately by default, Taiwan securities transaction tax is not required as manual input, actual broker fee override is supported, and calculated amounts are still persisted as event-ledger fields.
- Classification tests: averaging down, disciplined scaling in, premature scale out, late scale out, and coherent management return stable labels for fixed fixtures.
- Template tests: output includes overall conclusion, strengths, issues, evidence, next-operation rules, and data-quality notes.
- Score visibility tests or review checks: default templates and UI should not lead with exact raw `0-100` scores; raw score breakdown belongs to advanced trace or evidence payload.
- API contract tests: group event timeline and lifecycle review endpoints return saved reviews and do not silently recompute existing versions.
- Frontend tests or manual QA: timeline shows entries/adds/exits, group-level review is visually distinct from exit-batch review, and copyable evidence payload works.

## Spec Promotion Criteria

This document remains a planning note until these decisions are accepted:

- Exact `position_event` schema.
- Exact operation reason taxonomy.
- Lifecycle-level plan field requirements.
- Average-cost vs lot-matching policy.
- Active-position lifecycle review policy.
- Lifecycle review API request/response shape.
- Review versioning and refresh behavior.
- UI surface for entering operation reasons and viewing lifecycle reviews.

After those decisions are stable, promote the durable API/data contracts into `docs/specs/backend-api-technical-spec.md` and any relevant product specs.

## Explicit Non-Goals For This Future Plan

- Do not change the current Single Trade Review MVP prompts now.
- Do not block MVP implementation on event-ledger design.
- Do not require tax-lot accounting unless product scope explicitly expands.
- Do not require users to manually enter Taiwan securities transaction tax when market/product/broker defaults can calculate it; do not hardcode Taiwan defaults as permanent constants.
- Do not infer user strategy from price data alone.
- Do not add global all-trades statistics as part of lifecycle review.
- Do not require LLM summary for the first lifecycle review version.
- Do not make exact raw scores the primary lifecycle review UI unless calibration and product language have been accepted.

## Open Questions

1. Should add-entry events be entered manually by users, or inferred from future portfolio quantity increases?
2. Should the system use average-cost accounting only, or eventually support explicit lot matching?
3. Should lifecycle review include active positions, or only fully closed position groups?
4. Should `position_group_id` be created before or at the same time as `position_event`?
5. How much event detail should be shown in the UI before it becomes overwhelming?

## Decision For Now

Finish the current Single Trade Review MVP first.

After that version is stable, revisit this document to decide whether the next step is an event ledger foundation or a smaller lifecycle-review prototype based on the existing `position_group_id` grouping.
