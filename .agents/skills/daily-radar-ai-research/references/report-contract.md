# Phase 1 report contract

## Inputs

The production export is the source of truth for:

- Daily Radar run identity, status, dates, counts, and errors.
- Canonical candidates, observation scores, buckets, rule evidence, risks, technical snapshots, institutional flow, margin, market context, and data dates.
- The configured user's active positions and watchlist.
- Recent canonical candidate appearances.

External research may supplement:

- Latest completed US session: S&P 500, Nasdaq, Philadelphia Semiconductor Index, VIX, US 10-year yield, and TSM ADR.
- USD/TWD and material Taiwan index futures/options context when a dated primary source is available.
- Material company announcements, earnings events, policy events, or supply-chain news affecting an exported symbol.
- Weekend events for Monday reports.

Do not require every optional lane. State `資料缺失` when a reliable dated value is unavailable.

## Required report sections

1. `執行摘要`
   - `盤前分析` or `盤中補跑`
   - generation time, canonical run ID/date, candidate count
   - data quality and missing/stale lanes
2. `市場風險溫度`
   - persisted TW regime first
   - current global context second
   - classify as `積極`, `選擇性`, `防守`, or `資料不足`
3. `候選二次排序`
   - list at most ten exported candidates
   - retain canonical score and bucket beside the AI priority
   - give evidence, conflict, and invalidation/caveat
4. `持股互動`
   - identify already-held candidates, duplicate exposure, concentration, and thesis conflicts
   - refer to positions by symbol, never by user identifiers
5. `今日觀察計畫`
   - group into `優先觀察`, `等待確認`, and `降低優先級`
   - use observation language, not execution instructions
6. `資料來源與限制`
   - link every external source
   - list market dates and unresolved gaps
   - state that this is AI secondary research and not the canonical score or trading instruction

## Ranking rules

- Rank only within exported candidates.
- A high canonical score is not enough when data is stale, external event risk is material, or the candidate duplicates an existing position.
- Portfolio interaction changes personal priority, not the canonical candidate order.
- Do not manufacture a precise target price, stop, probability, or expected return.
- If evidence is insufficient, retain the canonical order and say why AI did not override it.

## Failure output

When the report cannot be produced, return a short failure record containing:

- attempted time
- failed stage: `production_export`, `external_context`, or `report_validation`
- exact non-secret error
- whether a manual retry is safe

Never fall back to stale local files or a previous report without explicit user instruction.

