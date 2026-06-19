# AVWAP and Volume Profile Implementation Plan

> Temporary execution plan.
>
> This file is not a long-term architecture source of truth. Delete this file after the plan is fully implemented and reviewed.
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

Add cost-line and volume-distribution indicators for swing-trading observation, starting with daily Anchored VWAP. The first release should support watchlist quick lookup and analysis technical indicators with simple state-based copy, without turning the signals into direct buy / sell commands.

The core principle is data honesty: every output must expose its source granularity, anchor, and whether it is estimated. Daily, 5-minute, and tick-level calculations have different reliability and must not be presented as equivalent.

## Non-Goals

- Do not implement 5-minute or tick-level data in Phase 1.
- Do not implement precise Volume Profile / POC in Phase 1.
- Do not let LLMs estimate AVWAP, VWAP, POC, or value-area numbers.
- Do not let shared background context overwrite Daily Radar ranking, action, verdict, or classification.
- Do not present estimated daily AVWAP as true intraday or tick-level cost basis.

## Current Baseline

The current system already has daily OHLCV through yfinance-backed raw data paths and explicit `technical_indicators` outputs for indicators such as Bollinger Band Width, ATR, OBV, MFI, ADX, and Donchian Channel.

The missing input for a reliable daily AVWAP is daily traded amount / turnover value. FinMind's free tier can provide this for single-symbol daily queries through `TaiwanStockPrice` or `TaiwanStockPriceAdj`, using `Trading_money` and `Trading_Volume`. Without amount, the system can only approximate daily VWAP using a typical price formula, which must be marked as estimated.

## Phase 1: Daily AVWAP

Detailed Phase 1 planning now lives in `docs/plans/2026-06-18-phase-1-daily-avwap-plan.md`.

Phase 1 should be independently mergeable and useful even if Phase 2 and Phase 3 never ship. It should use FinMind single-symbol daily `Trading_money` and `Trading_Volume` to compute daily AVWAP from deterministic anchors, then generate five current-day observation lists from the prior close:

1. Current-day pullback observation candidates.
2. Current-day breakout confirmation candidates.
3. Current-day holding add / hold / warning candidates.
4. Current-day holding risk alerts.
5. Current-day overheated do-not-chase candidates.

Phase 1 should run on a bounded managed universe only: active holdings, watchlist symbols, and Daily Radar selected candidates. It should not attempt whole-market per-symbol pulls on the FinMind free tier, and arbitrary Analyze symbols should not trigger on-demand FinMind historical backfill in the first release.

Before Phase 1 relies on active holdings, remove the current hard 8-position active portfolio cap. Larger portfolios should be handled through quota-aware Phase 1 refresh, cache reuse, and explicit data-quality caveats, not by blocking users from tracking more holdings.

The first release should preserve Daily Radar ranking and scoring. AVWAP may appear as detail trace and supporting evidence, but not as a direct scoring modifier.

Phase 1 should not add a new public endpoint in the first release. It should project into existing responses:

- `AnalyzeResponse.phase1_observation`
- `PortfolioRiskSummary.position_risks[].phase1_position_state`
- `DailyRadarCandidate.input_snapshot.phase1_avwap_context`
- Watchlist continues using `/analyze` with `skip_ai: true`

For Analyze, `phase1_observation` is a read projection over existing managed-universe results. If the symbol is outside active holdings, watchlist, and Daily Radar selected candidates, the response should omit Phase 1 data or expose `missing_reason: "not_in_phase1_universe"`.

## Phase 2: 5-Minute VWAP and Intraday AVWAP

### Purpose

Support intraday observation: whether high chasers are trapped, whether an intraday breakout holds above VWAP, and whether heavy-volume moves retain their average cost line.

### Data Requirements

| Field | Notes |
| --- | --- |
| `timestamp` | 5-minute bar start or end time. |
| `open` / `high` / `low` / `close` | 5-minute OHLC. |
| `volume` | 5-minute traded volume. |
| `amount` | 5-minute traded amount; strongly preferred. |
| `session` | Regular session, after-hours, odd-lot, or other market segment. |
| `is_realtime` | Whether the bar is final or still changing. |
| `delay_seconds` | Provider delay disclosure. |

### Output Contract

| Field | Meaning |
| --- | --- |
| `intraday_vwap` | Current day's VWAP. |
| `avwap_from_open` | Average cost from market open. |
| `avwap_from_intraday_low` | Average cost from the intraday low anchor. |
| `avwap_from_intraday_breakout` | Average cost from the intraday breakout anchor. |
| `price_vs_intraday_vwap` | Whether current price is above, near, or below intraday VWAP. |
| `intraday_breakout_hold` | Whether price still holds above breakout AVWAP. |
| `source_granularity` | Must be `5m`. |
| `delay_seconds` | Required when provider data is delayed. |

## Phase 3: 5-Minute Bar-Based Volume Profile

### Purpose

Provide swing support/resistance context through volume concentration zones. The first implementation can be bar-based and approximate; it must not be labeled as tick-level Volume Profile.

### Data Requirements

| Field | Notes |
| --- | --- |
| 5-minute `high` / `low` / `close` / `volume` | Minimum input for approximate profile. |
| `tick_size` | Price buckets must follow market tick-size rules. |
| `profile_window` | Example windows: 20 trading days, 60 trading days, from swing low, from breakout. |
| `allocation_method` | Example: close-only, typical-price, or high-low distributed volume. |
| `source_granularity` | Must be `5m_bar` unless tick-level data is used. |

### Output Contract

| Field | Meaning |
| --- | --- |
| `poc` | Price bucket or range with the largest allocated volume. |
| `value_area_high` | Upper boundary of the high-volume value area. |
| `value_area_low` | Lower boundary of the high-volume value area. |
| `low_volume_zones` | Sparse-volume price ranges. |
| `profile_window_start` | Start of the profile calculation window. |
| `profile_window_end` | End of the profile calculation window. |
| `confidence` | Confidence score or label for the approximate profile. |
| `source_granularity` | `5m_bar` for the first version. |
| `estimated` | `true` for bar-allocated profile. |

### Interpretation

| Condition | Interpretation |
| --- | --- |
| Price below dense overhead value area | Possible supply / trapped-holder zone above. |
| Price above POC and value area | Cost structure supports continuation. |
| Breakout enters low-volume zone | Move can accelerate, but false-break risk needs VWAP confirmation. |
| Price loses value-area low | Prior cost support failed. |

## Recommended Execution Order

1. Implement the standalone Phase 1 plan in `docs/plans/2026-06-18-phase-1-daily-avwap-plan.md`.
2. Revisit 5-minute provider choice before Phase 2.
3. Revisit Volume Profile only after 5-minute data quality is proven.

## Phase 1 Success Criteria

See `docs/plans/2026-06-18-phase-1-daily-avwap-plan.md` for the detailed Phase 1 success criteria. At the total-roadmap level, Phase 1 is successful when it removes the outdated 8-position active portfolio cap, ships daily AVWAP fields, and provides the five deterministic current-day observation lists for the managed universe through existing response contracts without requiring a new public endpoint, arbitrary Analyze-symbol FinMind backfill, intraday data, whole-market FinMind pulls, or Daily Radar scoring changes.

## Documentation Sync Checklist After Implementation

When this plan is implemented, delete this file and move durable facts into canonical docs:

| Canonical doc | Sync when |
| --- | --- |
| `docs/specs/backend-api-technical-spec.md` | Any API response fields, schemas, missing reasons, or technical indicator contract changes. |
| `docs/specs/ai-stock-sentinel-architecture-spec.md` | Any new ingestion path, DB payload shape, service/module boundary, or data-flow change. |
| `docs/specs/daily-stock-radar-spec.md` | Any Daily Radar detail trace, candidate payload, request budget, or scoring/non-scoring boundary change. |
| `docs/specs/frontend-architecture-spec.md` | Any Analyze, Watchlist, or Daily Radar frontend state/API-boundary display change. |
| `README.md` | Any user-visible capability, endpoint summary, or operational setup change. |
| `docs/backend-self-study-guide.md` | Any backend learning path, maintenance flow, or troubleshooting path change. |

## Open Discussion Points

1. Should Phase 1 default to `TaiwanStockPriceAdj`, or should it use unadjusted `TaiwanStockPrice` for shorter windows and keep adjusted data as an explicit mode?
2. Should `swing_low` default to 60 trading days, or should the UI expose 20-day and 60-day variants?
3. Should `breakout` prioritize 20-day or 60-day highs for the primary displayed AVWAP?
4. Should Daily Radar only display AVWAP trace in detail, or should a later version add a small scoring component after validation?
5. Which provider should be trusted for 5-minute amount and volume before Phase 2 begins?
