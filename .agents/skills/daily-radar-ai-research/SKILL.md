---
name: daily-radar-ai-research
description: Produce or backfill the personal premarket Daily Radar AI research report from the production read-only export plus current public market information. Use for the weekday 07:30 scheduled report, manual requests such as "補跑今天的 AI 雷達", and diagnosis of a missing or stale report.
---

# Daily Radar AI Research

Use the same evidence contract for scheduled and manual runs. Keep the production database read-only and treat the deterministic Daily Radar as the canonical candidate pool.

## Run the workflow

1. Work from the `financial-research` project root. Do not modify source files or production data during a report run.
2. Export the latest completed TW Daily Radar run for the configured user:

   ```bash
   backend/.venv/bin/python backend/scripts/export_codex_daily_radar.py --user-id 1
   ```

   The exporter obtains the dedicated reader password from macOS Keychain service `financial-research-production-db`. Never use or request the production root credential.
3. Stop and report the exact failure when the export fails, the run is incomplete, or the database is unavailable. Never substitute a previous local export.
4. Read [references/report-contract.md](references/report-contract.md) before gathering external context or writing the report.
5. Gather current public information with web search. Prefer primary sources. Include source links and exact data/event dates. Missing information remains `missing`; do not infer values.
6. Use only symbols present in `candidates`, `portfolio.active_positions`, or `watchlist`. External information may change priority or add a caveat but may not introduce a new stock recommendation.
7. Produce the Traditional Chinese report defined in the contract. Never expose email, user ID, database host, credentials, or raw personal notes.

## Time semantics

- Scheduled runs are Monday through Friday at 07:30 Asia/Taipei.
- Select the latest completed TW run, not `today - 1 day`. Monday normally uses Friday's run; holidays use the latest completed trading date.
- Before the TWSE open, label the output `盤前分析`.
- At or after the TWSE open, label a manual run `盤中補跑`, include the current Taiwan session state when available, and do not present it as a premarket snapshot.
- A rerun replaces no prior result in Phase 1. Report the run ID and generation time so the user can distinguish runs.

## Hard boundaries

- Do not write to the production database, repository, portfolio, watchlist, or scoring configuration.
- Do not change `observation_score`, canonical buckets, or `/analyze/position` Hold/Trim/Exit actions.
- Do not invent numeric levels, company events, causal explanations, or source attribution.
- Do not expose chain-of-thought. Give concise reasons and resolvable evidence.
- If an external source conflicts with persisted data, preserve both dates and flag the conflict instead of silently choosing one.
- If the computer was off at schedule time, a later manual invocation runs the identical workflow against the latest completed run.

