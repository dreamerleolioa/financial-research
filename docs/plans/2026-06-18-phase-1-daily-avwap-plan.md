# Phase 1 Daily AVWAP Plan

> Temporary execution plan.
>
> This file is not a long-term architecture source of truth. Delete this file after Phase 1 is fully implemented and reviewed.
>
> After implementation, sync all durable changes into the relevant canonical docs:
>
> - `docs/specs/backend-api-technical-spec.md`
> - `docs/specs/ai-stock-sentinel-architecture-spec.md`
> - `docs/specs/daily-stock-radar-spec.md`
> - `docs/specs/frontend-architecture-spec.md`, if frontend surfaces change
> - `README.md`, if public capability or setup behavior changes
> - `docs/backend-self-study-guide.md`, if backend learning or maintenance paths change

## Goal

Add daily Anchored VWAP as a swing-trading observation layer, then use it to produce five current-day observation lists.

The timing semantics are:

```text
T-1 close data
-> Phase 1 / Daily Radar run during early T-day hours
-> T-day observation lists for open and intraday monitoring
```

The five lists are:

1. Current-day pullback observation candidates.
2. Current-day breakout confirmation candidates.
3. Current-day holding add / hold / warning candidates.
4. Current-day holding risk alerts.
5. Current-day overheated do-not-chase candidates.

Phase 1 should help answer practical post-close questions:

- Which holdings can continue to be held?
- Which holdings are close to add conditions?
- Which holdings are entering warning or invalidation territory?
- Which strong stocks should not be chased?
- Which non-held symbols are worth observing today?

The output must remain state-based. AVWAP is a cost line, not a standalone buy or sell signal.

## Non-Goals

- Do not implement 5-minute or tick-level data in Phase 1.
- Do not implement precise Volume Profile / POC in Phase 1.
- Do not use Phase 1 for intraday realtime decisions.
- Do not scan the whole Taiwan market through per-symbol FinMind calls.
- Do not keep the current hard 8-position active portfolio limit as part of the Phase 1 product boundary.
- Do not let LLMs estimate AVWAP, VWAP, support, resistance, or classification values.
- Do not let shared background context overwrite Daily Radar ranking, action, verdict, or classification.
- Do not present estimated daily AVWAP as true intraday or tick-level cost basis.
- Do not output direct trade commands such as "buy now", "sell now", "buy at price X", or "sell at price Y".

## Input Universe

Phase 1 should run over a bounded managed universe:

| Universe | Included | Notes |
| --- | --- | --- |
| Active holdings | Yes | Highest priority for management lists. |
| Watchlist | Yes | Good fit for quick lookup and current-day observation. |
| Daily Radar selected candidates | Yes | Detail trace only in first release. |
| Analyze arbitrary symbol | No | Analyze may read an existing Phase 1 result, but must not expand the managed universe or trigger on-demand FinMind historical backfill in the first release. |
| Whole market | No | Avoid full-market per-symbol calls on FinMind free tier. |

If whole-market Top 10 lists are needed later, they should come from an existing prefiltered universe, such as Daily Radar candidates, not from a direct all-market FinMind sweep.

If a user analyzes a symbol that is not in active holdings, watchlist, or Daily Radar selected candidates, `AnalyzeResponse.phase1_observation` should be absent or return a data-quality payload with `missing_reason: "not_in_phase1_universe"`. The first release should not fetch FinMind daily amount data for arbitrary one-off Analyze symbols.

## Data Source

Primary source should be FinMind single-symbol daily data, available on the free tier with token-based request limits.

| Source | Tier | Use |
| --- | --- | --- |
| `TaiwanStockPriceAdj` | Free with `data_id` | Preferred when adjusted prices are needed for longer historical windows. |
| `TaiwanStockPrice` | Free with `data_id` | Acceptable for unadjusted daily AVWAP when corporate-action adjustment is not required. |
| yfinance daily OHLCV | Free | Fallback for OHLCV only; does not provide traded amount. |

Do not use FinMind all-market daily queries in Phase 1, because all-stock queries without `data_id` require Backer/Sponsor access. Phase 1 should only query selected symbols.

Phase 1 default decision:

- Use `finmind:TaiwanStockPrice` with `data_id` for single-symbol daily data.
- Set `adjustment_mode: "unadjusted"`.
- Treat this as the free registered-user compatible mode.
- Do not use `TaiwanStockPriceAdj` as the Phase 1 default. Revisit adjusted mode only after the account is upgraded to a paid tier that supports it.

## Data Requirements

| Field | Required | Notes |
| --- | --- | --- |
| `date` | Yes | Trading date. |
| `open` / `high` / `low` / `close` | Yes | From FinMind `open`, `max`, `min`, `close`, or existing daily OHLC input. |
| `volume` | Yes | Normalize from FinMind `Trading_Volume` or existing daily volume input. |
| `amount` / `turnover_value` | Yes | Normalize from FinMind `Trading_money`; preferred source for daily VWAP and daily AVWAP. |
| `is_final` | Yes | Phase 1 should normally use final close data. |
| `source` | Yes | Include dataset and provider, for example `finmind:TaiwanStockPriceAdj`. |
| `adjustment_mode` | Yes | Records whether prices and amounts are adjusted for corporate actions. |
| `holding_entry_date` | Conditional | Required for `AVWAP_from_entry`; otherwise omit or mark unavailable. |
| `holding_avg_cost` | Conditional | Required for holding-specific state classification. |

## Portfolio Limit Dependency

The current product has a hard active portfolio limit of 8 holdings. Phase 1 should remove that hard product cap before relying on active holdings as a managed universe.

Required cleanup:

- Remove or raise backend `PORTFOLIO_LIMIT = 8` enforcement in portfolio creation.
- Remove frontend `MAX_PORTFOLIO_COUNT = 8` blocking behavior in Analyze.
- Update canonical docs that describe the 8-position cap.
- Keep risk and data-quality language for larger portfolios, but do not block users from tracking more holdings.

Phase 1 quota control should come from FinMind request budgeting, cache reuse, and managed-universe refresh strategy, not from a portfolio-count product cap. If a user has many active holdings, Phase 1 should still process the managed universe with explicit quota/data-quality caveats instead of refusing portfolio entries.

## Request Budget and Cache Strategy

FinMind free token usage is sufficient for Phase 1 if the implementation avoids all-market backfills. A single-symbol request can retrieve a full historical window, so quota usage should scale with selected symbol count, not trading-day count.

| Scenario | Expected request count |
| --- | ---: |
| Analyze one symbol already in Phase 1 universe | 0 new FinMind requests when a fresh Phase 1 result already exists |
| Watchlist refresh for 20 symbols | 20 requests |
| Active holdings refresh | 1 request per active holding, bounded by cache and hourly budget |
| Daily Radar selected 50 symbols | 50 requests |
| Daily Radar selected 100 symbols | 100 requests |

Phase 1 must follow these quota rules:

- Query only managed symbols: active holdings, watchlist symbols, or Daily Radar selected candidates.
- Do not trigger FinMind historical backfill for arbitrary Analyze symbols in the first release.
- Never perform full-market per-symbol backfill in the normal request path.
- Reuse final `stock_raw_data` rows before calling FinMind.
- Share the existing `FinMindClient` governance for token injection, hourly request ledger, response cache, and token-identity-aware cache keys.
- Cache by `dataset`, `symbol`, `start_date`, `end_date`, `token_identity`, and `adjustment_mode`.
- Treat quota exhaustion as a data-quality caveat with a missing reason, not as permission to fabricate neutral AVWAP values.

## Indicator Layer

### Daily VWAP

Preferred daily VWAP:

```text
daily_vwap = amount / volume
```

Fallback estimated daily VWAP:

```text
estimated_daily_vwap = (high + low + close) / 3
```

When the fallback is used, every derived AVWAP output must carry `estimated: true`.

### Anchored VWAP

Preferred Anchored VWAP:

```text
AVWAP(anchor -> today) = sum(amount from anchor to today) / sum(volume from anchor to today)
```

Required anchors:

| Anchor | Deterministic rule |
| --- | --- |
| `swing_low` | Recent swing low, preferably the lowest low in the last 60 trading days or a confirmed local low rule. |
| `breakout` | Close breaks above the prior 20-day or 60-day high with a volume-ratio threshold. |
| `high_volume` | Volume is at least 2x the 20-day average, or in the top 10% of the last 60 trading days. |
| `entry` | Holding entry date, if available. If only average cost is available, do not fabricate this anchor. |

Open decision: choose one canonical display anchor for breakout, while allowing backend trace to calculate both 20-day and 60-day variants if useful.

### Supporting Indicators

Phase 1 list classification should reuse existing daily indicators where possible:

| Indicator | Use |
| --- | --- |
| MA5 / MA20 | Pullback support, trend health, short-term weakness. |
| OBV | Volume-price confirmation. |
| MACD | Momentum weakening for warning states. |
| Donchian 20 / 60 | Breakout proximity and breakdown risk. |
| Bollinger Band / bandwidth | Overheat and volatility context. |
| Volume ratio | Breakout and high-volume risk checks. |

## Output Contract

### Response Projection Decision

Do not add a new public Phase 1 endpoint in the first release. Phase 1 outputs should be embedded into existing responses and surfaces.

| Surface | Response projection | Notes |
| --- | --- | --- |
| Analyze | `AnalyzeResponse.phase1_observation` | Single-symbol AVWAP and classification summary only when the symbol already belongs to the managed Phase 1 universe and has a fresh result. Out-of-universe symbols should return no Phase 1 observation or `missing_reason: "not_in_phase1_universe"`. |
| Portfolio risk summary | `PortfolioRiskSummary.position_risks[].phase1_position_state` | Holding-specific state such as `hold`, `add_watch`, `profit_take_watch`, `warning`, or `exit_risk`. |
| Daily Radar | `DailyRadarCandidate.input_snapshot.phase1_avwap_context` | Detail trace and supporting evidence only. Must not modify Daily Radar ranking, scoring, bucket, action, verdict, or classification in the first release. |
| Watchlist | Continue using `/analyze` with `skip_ai: true` | No separate watchlist indicator endpoint. Watchlist quick lookup reads `AnalyzeResponse.phase1_observation` when present. |

This keeps Phase 1 consistent with existing read paths and avoids adding a new public API contract before the data quality and UI shape are validated.

### Per-Symbol AVWAP Fields

| Field | Meaning |
| --- | --- |
| `avwap_from_swing_low` | Average cost from the swing-low anchor. |
| `avwap_from_breakout` | Average cost from the breakout anchor. |
| `avwap_from_high_volume` | Average cost from the high-volume anchor. |
| `avwap_from_entry` | Average cost from holding entry date, only when entry date is available. |
| `price_vs_avwap` | Whether current price is above, near, or below the relevant AVWAP. |
| `avwap_slope` | Whether the cost line is rising, flat, or falling. |
| `distance_to_avwap_pct` | Current price distance from AVWAP. |
| `anchor_date` | Date used for the anchor. |
| `anchor_reason` | Deterministic reason, such as `swing_low`, `breakout_20d`, `breakout_60d`, `high_volume`, or `entry`. |
| `source_granularity` | Must be `daily` for Phase 1. |
| `estimated` | `true` when amount is unavailable and typical-price fallback is used. |
| `missing_reason` | Explains insufficient history, missing amount, zero volume, stale data, unavailable anchor, or missing entry date. |

### Classification Fields

| Field | Meaning |
| --- | --- |
| `classification` | One primary all-symbol state. |
| `position_state` | One primary holding-specific state, if applicable. |
| `matched_rules` | Deterministic rule IDs that explain why the symbol appears in a list. |
| `confidence` | High, medium, or low based on data completeness and rule agreement. |
| `invalidating_level` | Price or condition that would invalidate the current observation. |
| `distance_to_support_pct` | Distance to the nearest relevant support level. |
| `distance_to_resistance_pct` | Distance to the nearest relevant resistance or breakout level. |
| `current_day_observation` | Short state-based text for what to monitor or control today. |

## State Taxonomy

### All Symbols

| State | Meaning |
| --- | --- |
| `strong_breakout` | Strong breakout structure, but still requires overheat and pullback checks. |
| `pullback_watch` | Healthy pullback near AVWAP / MA support. |
| `repairing_strength` | Previously weak or consolidating symbol is recovering above key levels. |
| `overheated` | Strong but extended; not suitable for chasing. |
| `weak_breakdown` | Price loses important trend or AVWAP support. |
| `range_watch` | No clear trend edge; keep observing. |

### Holdings

| State | Meaning |
| --- | --- |
| `hold` | Structure remains intact. |
| `add_watch` | Add conditions may be forming, but requires support hold or breakout confirmation. |
| `profit_take_watch` | Strong but extended; protect gains instead of chasing. |
| `warning` | Current-day weakness could damage the structure if support is not reclaimed. |
| `exit_risk` | Swing thesis may be invalidated; consider stop-loss or reduction review. |

### Product Decision Labels

The user-facing UI should collapse detailed states into four simple labels. These labels are easier to scan than raw indicator states and make the holding context explicit.

| Label | Applies to | Internal states | Meaning |
| --- | --- | --- | --- |
| `加碼` | Existing holdings | `add_watch` | Holding is already owned and conditions are strengthening. |
| `建倉` | Non-held watchlist or Daily Radar symbols | `strong_breakout`, `pullback_watch`, `repairing_strength` | Symbol is not held and conditions are strong enough to build confidence. |
| `續抱` | Existing holdings | `hold`, selected `profit_take_watch` cases | Holding structure remains intact; overextended cases should still show heat/risk evidence. |
| `停損警戒` | Existing holdings | `warning`, `exit_risk`, `weak_breakdown` | Holding structure is weakening; prioritize preventing loss expansion. |

The detailed internal state should still be returned for traceability. The short label is for scan-first UI, copy text, and list grouping.

## Five Current-Day Lists

### 1. Current-Day Pullback Observation Candidates

Purpose: find symbols suitable for current-day pullback observation.

Suggested conditions:

- `close > MA20`
- `close > AVWAP_from_swing_low`
- `AVWAP slope > 0`
- OBV confirms accumulation or has a rising 20-day trend.
- `distance_to_AVWAP_or_MA20_pct` is between 0% and 5%.
- Not a high-volume long black candle.
- Not already classified as overheated.

Suggested fields:

| Field | Meaning |
| --- | --- |
| `symbol` | Stock symbol. |
| `close` | Latest close. |
| `key_supports` | MA20, AVWAP, prior support, or swing support. |
| `distance_to_support_pct` | How close the close is to the relevant support. |
| `current_day_observation` | Example: "observe whether pullback holds AVWAP / MA20." |

### 2. Current-Day Breakout Confirmation Candidates

Purpose: find symbols near a breakout level that may confirm strength during the current trading day or by the current close.

Suggested conditions:

- `close > MA20`
- `close > AVWAP`
- Distance to 20-day high or Donchian upper band is less than 3%.
- Volume is greater than 20-day average volume.
- OBV 20-day trend is rising.
- Not overextended from MA20 or swing-low AVWAP.

Suggested fields:

| Field | Meaning |
| --- | --- |
| `symbol` | Stock symbol. |
| `close` | Latest close. |
| `breakout_level` | 20-day high, 60-day high, or Donchian upper. |
| `distance_to_breakout_pct` | How close price is to breakout confirmation. |
| `current_day_observation` | Example: "observe whether close confirms above breakout level." |

### 3. Holding Add / Hold / Warning Candidates

Purpose: manage existing positions before looking for new ideas.

Suggested add-watch conditions:

- Holding is currently profitable.
- `close > holding_avg_cost`
- `close > AVWAP_from_entry`, when available, or `close > AVWAP_from_breakout`
- `close > MA20`
- `AVWAP slope > 0`
- Pullback to MA5, MA20, or AVWAP holds.

Suggested hold conditions:

- `close > MA20`
- `close > AVWAP_from_swing_low`
- No major OBV deterioration.
- No close below breakout AVWAP.

Suggested fields:

| Field | Meaning |
| --- | --- |
| `symbol` | Holding symbol. |
| `position_state` | `hold`, `add_watch`, `profit_take_watch`, `warning`, or `exit_risk`. |
| `close` | Latest close. |
| `holding_avg_cost` | Portfolio average cost, if available. |
| `key_supports` | MA5, MA20, AVWAP, breakout level, or swing low. |
| `add_condition` | Non-command condition that would justify add observation. |

### 4. Stop-Loss Risk Alerts

Purpose: surface holdings whose swing thesis is weakening.

Warning conditions:

- Close breaks below MA5.
- Close breaks below short-term AVWAP.
- MACD momentum weakens.
- OBV 20-day trend turns down.

Exit-risk conditions:

- `close < MA20`
- `close < AVWAP_from_entry`, when available.
- `close < AVWAP_from_breakout`
- `close < previous_swing_low`
- `close < Donchian lower band`

Suggested fields:

| Field | Meaning |
| --- | --- |
| `symbol` | Holding symbol. |
| `position_state` | `warning` or `exit_risk`. |
| `close` | Latest close. |
| `broken_levels` | Levels lost on close. |
| `invalidating_level` | Key level that defines the swing thesis. |
| `current_day_observation` | Example: "observe whether price reclaims MA20 / breakout AVWAP." |

### 5. Overheated Do-Not-Chase Candidates

Purpose: identify strong symbols where chasing risk is elevated.

Suggested conditions:

- `close > AVWAP_from_swing_low * 1.10`
- `close > MA20 * 1.10`
- Close is near Bollinger upper band.
- Volume spikes relative to 20-day average.
- Price is near 20-day or 60-day resistance.

Suggested fields:

| Field | Meaning |
| --- | --- |
| `symbol` | Stock symbol. |
| `close` | Latest close. |
| `distance_to_avwap_pct` | Extension from AVWAP. |
| `distance_to_ma20_pct` | Extension from MA20. |
| `nearby_resistance` | 20-day high, 60-day high, or prior resistance. |
| `current_day_observation` | Example: "strong structure, but wait for AVWAP / MA support reset." |

## Classification Priority

Because one symbol can match multiple rules, Phase 1 should use deterministic priority:

1. Holdings `exit_risk`.
2. Holdings `warning`.
3. Overheated / profit-take watch.
4. Pullback observation.
5. Breakout confirmation.
6. Repairing strength.
7. Range watch.

Risk states should override opportunity states for holdings. A holding that is both extended and close to losing support should appear in risk management first, not in a candidate list.

## Interpretation Rules

| Condition | Interpretation |
| --- | --- |
| `close > AVWAP_from_swing_low` and `close > AVWAP_from_breakout` and `AVWAP slope > 0` | Long-side cost basis is favorable. |
| `low <= AVWAP` and `close > AVWAP` and `close > MA20` | Pullback tested average cost and recovered by close. |
| Recent breakout but `close < AVWAP_from_breakout` or `close < breakout_price` | Breakout buyers may be trapped; risk increases. |
| `close > AVWAP_from_swing_low * 1.10` and price is near 20-day high or Bollinger upper band | Strong but extended; chasing risk is elevated. |

Do not map these rules directly to buy or sell outputs. The product should display state, evidence, support/resistance context, and current-day observation.

## Product Copy Boundary

The UI copy should be simple and useful. It may use operation-intensity and risk-control language, but it must avoid direct buy / sell commands.

Allowed copy style:

| State | Short copy examples |
| --- | --- |
| `add_watch` | `可加重操作`, `條件轉強，可提高關注` |
| Non-held `strong_breakout` / `pullback_watch` / `repairing_strength` | `可建立信心`, `條件轉強，可提高關注` |
| `hold` | `結構健康，可續抱觀察`, `成本線仍有利` |
| `profit_take_watch` / overheated | `偏熱，不宜追高`, `強勢但需等回測` |
| `warning` | `轉弱警戒`, `避免擴大損失`, `需守住關鍵支撐` |
| `exit_risk` | `風險升高，優先控管損失`, `跌破成本線，需降低風險` |
| `range_watch` | `盤整觀察`, `暫無明確優勢` |

Preferred scan labels:

| Label | Supporting copy |
| --- | --- |
| `加碼` | `可加重操作` |
| `建倉` | `可建立信心` |
| `續抱` | `結構健康，可續抱觀察` |
| `停損警戒` | `避免擴大損失` |

Disallowed copy style:

| Avoid | Reason |
| --- | --- |
| `買進`, `賣出`, `立即進場`, `立即出場` | Direct trade command. |
| `明天必漲`, `一定反彈`, `穩賺` | Guarantees outcome. |
| `跌破就全部賣掉` | Direct sell instruction and position sizing command. |
| `主力成本精準在 X` | Phase 1 daily AVWAP is not true tick-level cost basis. |

Preferred output shape:

```text
狀態：可加重操作
標籤：加碼
條件：收盤站上 MA20 / AVWAP，OBV 20 日向上
觀察：回測 AVWAP 不破，結構維持健康
風險：跌破 breakout AVWAP 時轉為警戒
```

This keeps the language practical without turning Phase 1 into a direct recommendation engine.

## Suggested Product Placement

Recommended response-projection order after Phase 1A is complete:

1. Add `AnalyzeResponse.phase1_observation` first.
2. Let Watchlist continue calling `/analyze` with `skip_ai: true` and display `phase1_observation` when present.
3. Add `PortfolioRiskSummary.position_risks[].phase1_position_state` after portfolio entry/cost fields are confirmed.
4. Add `DailyRadarCandidate.input_snapshot.phase1_avwap_context` as detail trace only; defer scoring integration.

Daily Radar ranking and scoring should remain unchanged in the first Phase 1 release.

## Implementation Phases

Phase 1 should be landed in smaller mergeable steps. Each step must be useful without requiring the later steps to be complete.

### Phase 1A0: Remove Active Portfolio Hard Cap

Remove the current hard 8-position cap before using active holdings as a Phase 1 managed-universe source:

- Backend: remove or raise `PORTFOLIO_LIMIT = 8` in portfolio creation.
- Frontend: remove disabled "portfolio full" behavior and tooltip copy tied to 8 holdings.
- Tests: update portfolio creation tests that expect `422` when active holdings reach 8.
- Docs: update canonical portfolio API and automation-review docs that still describe the 8-position limit.

This phase is independently useful because it removes an artificial tracking constraint even before AVWAP ships. It also prevents Phase 1 from inheriting an outdated product boundary.

### Phase 1A: Managed Universe and Daily AVWAP Snapshot

Build the backend-only foundation:

- Resolve the managed universe from active holdings, watchlist symbols, and latest Daily Radar selected candidates.
- Fetch FinMind single-symbol daily historical data only for that managed universe.
- Reuse fresh stored rows before calling FinMind.
- Normalize `Trading_money`, `Trading_Volume`, OHLC, source, data date, and adjustment mode.
- Compute daily AVWAP anchors and data-quality fields.
- Persist or cache Phase 1 snapshots keyed by symbol and data date.

This phase can ship without UI changes. It proves data availability, quota behavior, and deterministic calculations.

Implementation status as of 2026-06-19:

- Completed backend-only foundation in `phase1_avwap/`.
- Added managed-universe resolver for active holdings, watchlist symbols, and latest Daily Radar candidates.
- Added FinMind `TaiwanStockPrice` daily row normalization with `adjustment_mode = "unadjusted"`.
- Added deterministic daily AVWAP snapshot calculation for swing-low, 20-day breakout, high-volume, and holding-entry anchors.
- Added `phase1_avwap_snapshots` persistence keyed by `symbol`, `data_date`, `dataset`, and `adjustment_mode`.
- Existing fresh snapshots are reused before single-symbol FinMind fetches; provider/row gaps persist as `freshness = "missing"` with `missing_reason`.
- A snapshot is only `fresh` when the latest FinMind row matches requested `data_date`; if FinMind only returns an older trading day, the row is persisted as missing with `daily_price_row_missing_for_data_date`.
- No public API or UI response projection was added in this phase; that remains Phase 1B.

### Phase 1B: Existing Response Projections

Expose the Phase 1 snapshot through existing responses only:

- `AnalyzeResponse.phase1_observation`
- `PortfolioRiskSummary.position_risks[].phase1_position_state`
- `DailyRadarCandidate.input_snapshot.phase1_avwap_context`
- Watchlist continues using `/analyze` with `skip_ai: true`

This phase should not add a new public endpoint. Analyze should not trigger FinMind backfill for out-of-universe symbols.

Implementation status as of 2026-06-19:

- Completed `AnalyzeResponse.phase1_observation` projection.
- The Analyze projection reads `phase1_avwap_snapshots` only; it does not call FinMind, refresh snapshots, or expand the managed universe.
- Out-of-universe symbols return `missing_reason = "not_in_phase1_universe"`; managed-universe symbols without a same-day snapshot return `missing_reason = "phase1_snapshot_missing"`.
- Completed `PortfolioRiskSummary.position_risks[].phase1_position_state` projection.
- The portfolio projection reads `phase1_avwap_snapshots` only; it does not call FinMind or refresh snapshots from the risk-summary read path.
- Portfolio holding state currently prefers the `entry` anchor, then `breakout_20d`, then `swing_low_60d`; missing snapshots, missing anchors, or read failures return a non-blocking `資料不足` state with explicit `missing_reason`.
- Completed `DailyRadarCandidate.input_snapshot.phase1_avwap_context` projection.
- The Daily Radar projection reads `phase1_avwap_snapshots` only and stores the context as detail trace under `input_snapshot.phase1_avwap_context`.
- Missing snapshots or read failures stay non-blocking in the trace; Daily Radar ranking, scoring, buckets, risk labels, matched rules, and `data_dates` remain unchanged by Phase 1 AVWAP context.

### Phase 1C: Current-Day Observation Lists and UI

Add the user-facing classification layer:

- Current-day pullback observation candidates.
- Current-day breakout confirmation candidates.
- Current-day holding add / hold / warning candidates.
- Current-day holding risk alerts.
- Current-day overheated do-not-chase candidates.

The first UI pass can prioritize holdings and watchlist before Daily Radar detail polish. Holding risk states should remain higher priority than opportunity states.

## Phase 1 Success Criteria

- A managed-universe symbol can return daily AVWAP from swing low, breakout, and high volume anchors.
- A holding can return `AVWAP_from_entry` only when entry date is available.
- Daily AVWAP uses FinMind `Trading_money / Trading_Volume` when available, not typical-price approximation.
- Every AVWAP output includes `anchor_date`, `anchor_reason`, `distance_to_avwap_pct`, `source_granularity`, and `estimated`.
- Missing or insufficient data returns explicit `missing_reason` instead of fabricated neutral values.
- If daily amount is unavailable, outputs are marked estimated and UI copy says daily estimate.
- Free-tier request usage remains bounded by selected symbol count and does not attempt all-market daily pulls.
- Phase 1 managed universe is limited to active holdings, watchlist symbols, and Daily Radar selected candidates.
- Active holdings are not capped at 8 by Phase 1; larger portfolios rely on quota-aware refresh and data-quality caveats.
- Analyze does not trigger FinMind historical backfill for out-of-universe symbols.
- Out-of-universe Analyze symbols return no Phase 1 observation or `missing_reason: "not_in_phase1_universe"`.
- The five current-day lists are generated from deterministic rules and include `matched_rules`.
- The UI can group results under the four scan labels: `加碼`, `建倉`, `續抱`, and `停損警戒`.
- Holding risk lists are prioritized over non-held opportunity lists.
- No new public Phase 1 endpoint is required for the first release.
- Analyze exposes `phase1_observation` when Phase 1 data is available.
- Watchlist continues to use `/analyze` with `skip_ai: true`.
- Portfolio risk summary exposes holding-specific `phase1_position_state`.
- Daily Radar stores AVWAP context only under `input_snapshot.phase1_avwap_context`.
- Frontend copy says daily average cost line, not true main-player cost or tick-level cost.
- Daily Radar ranking and scoring remain unchanged in the first release.

## Verification Plan

- Unit test daily VWAP and AVWAP calculations with known `amount / volume` fixtures.
- Unit test fallback estimated AVWAP and verify `estimated: true`.
- Unit test anchor selection for swing low, breakout, high volume, and missing entry date.
- Unit test list classification priority when one symbol matches multiple states.
- Unit test request budget behavior with a bounded selected symbol list.
- API contract test that missing data returns `missing_reason`.
- Portfolio creation test confirms the old 8-position hard cap no longer blocks active holdings.
- Manual acceptance check with at least one holding, one watchlist symbol, one Daily Radar selected candidate, and one out-of-universe Analyze symbol.

## Documentation Sync Checklist After Implementation

When Phase 1 is implemented, delete this file and move durable facts into canonical docs:

| Canonical doc | Sync when |
| --- | --- |
| `docs/specs/backend-api-technical-spec.md` | Any API response fields, schemas, missing reasons, or technical indicator contract changes. |
| `docs/specs/ai-stock-sentinel-architecture-spec.md` | Any new ingestion path, DB payload shape, service/module boundary, data-flow change, or FinMind tier/default dataset decision such as `TaiwanStockPrice` with `adjustment_mode: "unadjusted"`. |
| `docs/specs/ai-stock-sentinel-automation-review-spec.md` | Any removal or replacement of the active portfolio 8-position cap. |
| `docs/specs/daily-stock-radar-spec.md` | Any Daily Radar detail trace, candidate payload, request budget, scoring/non-scoring boundary change, or free-tier-compatible FinMind single-symbol query constraint. |
| `docs/specs/frontend-architecture-spec.md` | Any Analyze, Watchlist, Portfolio, or Daily Radar frontend state/API-boundary display change. |
| `README.md` | Any user-visible capability, endpoint summary, or operational setup change. |
| `docs/backend-self-study-guide.md` | Any backend learning path, maintenance flow, or troubleshooting path change. |

## Open Discussion Points

1. Should Phase 1 default to `TaiwanStockPriceAdj`, or should it use unadjusted `TaiwanStockPrice` for shorter windows and keep adjusted data as an explicit mode?
2. Should `swing_low` default to 60 trading days, or should the UI expose 20-day and 60-day variants?
3. Should `breakout` prioritize 20-day or 60-day highs for the primary displayed AVWAP?
4. What is the reliable source of holding `entry_date` for `AVWAP_from_entry`?
5. Should the first UI expose all five lists, or should holdings management lists ship first?
