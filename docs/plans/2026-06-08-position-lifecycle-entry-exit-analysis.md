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

## API Shape

Potential future endpoints:

```text
GET /portfolio/groups/{position_group_id}/events
GET /portfolio/groups/{position_group_id}/lifecycle-review
POST /portfolio/groups/{position_group_id}/lifecycle-review
```

Behavior should mirror the MVP persistence rule:

- `GET` returns a saved review if present.
- `POST` creates the first saved review if absent.
- If a saved review exists, `POST` returns it rather than silently recomputing.
- Future refresh must be explicit and version-aware.
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

The UI must clearly distinguish:

- single exit-batch review: one sell decision.
- lifecycle review: the whole multi-entry/multi-exit operation.

## Migration / Backfill Notes

Existing data may not contain true event history.

Backfill should be conservative:

- Existing active rows can become one synthetic `initial_entry` event.
- Existing full-close rows can become one synthetic `initial_entry` and one `full_exit` event.
- Existing partial-close rows can become synthetic `partial_exit` events if `position_group_id` connects them.
- Historical add-entry decisions cannot be reconstructed unless they were recorded before this feature exists.

Backfilled events should carry a `source` or `data_quality` note such as `synthetic_from_portfolio_row`.

Do not pretend reconstructed events are exact user decisions.

## Implementation Phases For Future Discussion

### Phase A: Event Ledger Foundation

- Add `position_event` model and migration.
- Write events when creating a new position.
- Write events when partially or fully closing a position.
- Backfill synthetic events for existing rows.
- Keep current Single Trade Review behavior unchanged.

### Phase B: Event Timeline API And UI

- Add read-only event timeline endpoint.
- Show timeline under grouped closed positions.
- No lifecycle judgment yet.

### Phase C: Lifecycle Metrics Engine

- Compute lifecycle metrics from event chronology.
- Compute point-in-time indicator snapshots per event.
- Persist lifecycle review separately from `trade_review`.

### Phase D: Lifecycle Review UI

- Add group-level lifecycle review action.
- Show timeline-based review.
- Add copyable lifecycle evidence payload.

### Phase E: Optional Narrative Layer

- Add optional LLM summary only after deterministic metrics and classifications are stable.
- LLM must summarize structured facts, not compute signals or invent intent.

## Explicit Non-Goals For This Future Plan

- Do not change the current Single Trade Review MVP prompts now.
- Do not block MVP implementation on event-ledger design.
- Do not require tax-lot accounting unless product scope explicitly expands.
- Do not infer user strategy from price data alone.
- Do not add global all-trades statistics as part of lifecycle review.

## Open Questions

1. Should add-entry events be entered manually by users, or inferred from future portfolio quantity increases?
2. Should the system use average-cost accounting only, or eventually support explicit lot matching?
3. Should lifecycle review include active positions, or only fully closed position groups?
4. Should `position_group_id` be created before or at the same time as `position_event`?
5. How much event detail should be shown in the UI before it becomes overwhelming?

## Decision For Now

Finish the current Single Trade Review MVP first.

After that version is stable, revisit this document to decide whether the next step is an event ledger foundation or a smaller lifecycle-review prototype based on the existing `position_group_id` grouping.
