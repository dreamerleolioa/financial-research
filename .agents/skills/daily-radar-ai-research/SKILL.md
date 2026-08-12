---
name: daily-radar-ai-research
description: Build or backfill the personal premarket AI candidate list from every final supported Taiwan stock raw-data row for the source date plus current public market information. Use for the weekday 07:30 scheduled report, manual requests such as "補跑今天的 AI 雷達", and diagnosis of a missing or stale report.
---

# Daily Radar AI Research

Use the same evidence contract for scheduled and manual runs. Keep the production database read-only and build the AI shortlist from the complete date-scoped raw pool. The prepared run anchors date readiness and market context only; it never determines pool membership. Do not consume the deterministic Daily Radar candidate results.

## Run the workflow

1. Work from the `financial-research` project root. Do not modify source files or production data during a report run.
2. Export every final supported Taiwan stock raw-data row for the latest completed TW Daily Radar source date into a private temporary file:

   ```bash
   backend/.venv/bin/python backend/scripts/export_codex_daily_radar.py --user-id 1 --output /tmp/codex-daily-radar-raw-universe.json
   ```

   The exporter obtains the dedicated reader password from macOS Keychain service `financial-research-production-db`. Never use or request the production root credential.
3. Read `/tmp/codex-daily-radar-raw-universe.json` metadata first. Verify that `contract_version` is `codex-daily-radar-raw-pool-v2`, `source.transaction_read_only` is `on`, `raw_pool.selection` is `all_final_supported_tw_stock_raw_rows_for_source_date`, and `raw_pool.symbol_count` equals the non-empty `raw_universe` length. Stop and report the exact failure when the export fails, the raw pool is empty, counts disagree, or the database is unavailable. Never substitute a previous local export.
4. Read every `raw_universe` row in separate slices of at most 6 symbols. Do not concatenate all slices into one tool call, skip later slices, or shortlist before every slice has been reviewed. Record provisional evidence by symbol, then form the shortlist only after the final slice.
5. Read [references/report-contract.md](references/report-contract.md) before gathering external context or writing the report.
6. Gather current public information with web search. Prefer primary sources. Include source links and exact data/event dates. Missing information remains `missing`; do not infer values.
7. Build the shortlist only from `raw_universe`. Use `portfolio.active_positions` and `watchlist` only after the independent shortlist exists, to identify overlap, concentration, or conflicts. External information may change priority or add a caveat but may not introduce a new stock candidate.
8. Produce the Traditional Chinese report defined in the contract. Never expose email, user ID, database host, credentials, or raw personal notes. Remove only the exact temporary export after completing the report.

## Time semantics

- Scheduled runs are Monday through Friday at 07:30 Asia/Taipei.
- Select the latest completed TW run, not `today - 1 day`. Monday normally uses Friday's source date; holidays use the latest completed trading date. Pool membership comes from all matching final raw rows on that date and may grow as ingestion coverage grows.
- Before the TWSE open, label the output `盤前分析`.
- At or after the TWSE open, label a manual run `盤中補跑`, include the current Taiwan session state when available, and do not present it as a premarket snapshot.
- A rerun replaces no prior result in Phase 1. Report the run ID and generation time so the user can distinguish runs.

## Hard boundaries

- Do not write to the production database, repository, portfolio, watchlist, or scoring configuration.
- Do not query or infer pool membership from `daily_radar_prepared_runs.selected_symbols`, `daily_radar_candidates`, `observation_score`, canonical buckets, risk labels, matched rules, prefilter outcomes, universe rank, or track scores.
- Do not treat `raw_universe` array order as priority. It is alphabetized specifically to remove source-rank bias.
- Treat `raw_data_is_final` as persistence finality, not proof of analytical completeness. Keep every exported row visible, but exclude or caveat symbols missing essential OHLCV, indicator, price-history, institutional, or fundamental evidence before candidate selection.
- Evaluate raw values and data dates directly. Separate data-quality exclusion, evidence interpretation, candidate selection, and portfolio interaction in that order.
- Do not change canonical Daily Radar scoring or `/analyze/position` Hold/Trim/Exit actions.
- Do not invent numeric levels, company events, causal explanations, or source attribution.
- Do not expose chain-of-thought. Give concise reasons and resolvable evidence.
- If an external source conflicts with persisted data, preserve both dates and flag the conflict instead of silently choosing one.
- If the computer was off at schedule time, a later manual invocation runs the identical workflow against the latest completed run.
