# Phase 1 report contract

## Inputs

The production export is the source of truth for:

- Completed source-run identity, source date, and prepared market context/readiness metadata.
- Every final supported Taiwan stock raw-data row on the source date, alphabetized and projecting available OHLCV, price history, neutral technical indicators, numeric institutional activity, margin, fundamentals, AVWAP, background contexts, and data dates while preserving missing values.
- The configured user's active positions and watchlist.

The export intentionally excludes:

- `daily_radar_candidates` and recent candidate history.
- `daily_radar_prepared_runs.selected_symbols`; prepared-universe membership does not limit the AI pool.
- `observation_score`, buckets, matched rules, risk labels, explanations, and prefilter outcomes.
- prepared-universe rank, primary track, track scores, and track reasons.
- `technical_profile` scores, institutional rank/flow labels, and fundamental yield signals.

External research may supplement:

- Latest completed US session: S&P 500, Nasdaq, Philadelphia Semiconductor Index, VIX, US 10-year yield, and TSM ADR.
- USD/TWD and material Taiwan index futures/options context when a dated primary source is available.
- Material company announcements, earnings events, policy events, or supply-chain news affecting an exported symbol.
- Weekend events for Monday reports.

Do not require every optional lane. State `資料缺失` when a reliable dated value is unavailable.

## Required report sections

1. `執行摘要`
   - `盤前分析` or `盤中補跑`
   - generation time, source run ID/date, raw-universe count, and AI shortlist count
   - data quality and missing/stale lanes
2. `市場風險溫度`
   - persisted TW regime first
   - current global context second
   - classify as `積極`, `選擇性`, `防守`, or `資料不足`
3. `AI 候選名單`
   - independently select at most ten symbols from `raw_universe`
   - give the raw technical, institutional, margin, fundamental, and AVWAP evidence used
   - state data-quality exclusions, conflicts, and invalidation/caveats
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

## Candidate selection rules

- Start from every exported `raw_universe` row. Never use array order as priority.
- Treat `raw_pool.symbol_count` as date-scoped ingestion coverage, not a fixed target or cumulative historical count. It may increase when more final raw rows are ingested for future source dates.
- Do not equate `raw_data_is_final` with analytical completeness. Report how many rows lack essential evidence and keep those rows out of the shortlist until the required raw fields are available.
- Check finality, dates, missing values, liquidity, and stale contexts before interpreting positive evidence.
- Select and rank only from raw values. Do not reconstruct or imitate the canonical Daily Radar score.
- Build the independent shortlist before reading portfolio and watchlist interaction.
- Portfolio interaction may change personal priority but may not retroactively alter the raw evidence.
- Do not manufacture a precise target price, stop, probability, or expected return.
- If evidence is insufficient, reduce the shortlist or return `資料不足`; never fall back to a canonical candidate list.

## Failure output

When the report cannot be produced, return a short failure record containing:

- attempted time
- failed stage: `production_export`, `external_context`, or `report_validation`
- exact non-secret error
- whether a manual retry is safe

Never fall back to stale local files or a previous report without explicit user instruction.
