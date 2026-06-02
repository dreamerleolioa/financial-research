# Daily Radar Rollout Checklist

> status: rollout checklist ready
> date: 2026-06-02
> related plan: `docs/plans/2026-06-01-daily-stock-radar-mvp-implementation-plan.md`
> workflow: `.github/workflows/daily-radar.yml`

## Scope Boundary

This checklist separates static implementation verification from future manual and live rollout checks.

For this local implementation pass, the rollout plan is complete when the checklist exists, references the exact endpoint, environment variable, repository secret, and workflow names, and keeps Daily Radar copy in observation and risk language.

Live DB access, Google login, browser or manual frontend QA, Zeabur environment setup, and Zeabur workflow dispatch are future manual rollout steps. They are not blockers for this local implementation pass because Google login and DB access are not available now.

No secret values belong in this document. Use the variable names only.

## Static Implementation Verification

Use these checks before any live rollout. They verify the implementation shape and local behavior without touching Zeabur, a live DB, browser, or external workflow dispatch.

1. [ ] Confirm `.github/workflows/daily-radar.yml` exists.
2. [ ] Confirm the workflow supports `workflow_dispatch`.
3. [ ] Confirm the workflow keeps the weekday cron schedule for Daily Radar.
4. [ ] Confirm the workflow calls `POST /internal/daily-radar/run` only.
5. [ ] Confirm the workflow builds the endpoint from `ZEABUR_BACKEND_URL`.
6. [ ] Confirm the workflow sends `Authorization: Bearer ${DAILY_RADAR_INTERNAL_TOKEN}`.
7. [ ] Confirm backend internal auth still accepts `Authorization: Bearer` and `X-Internal-Token` for the shared token path.
8. [ ] Confirm the public read endpoints remain available:
   1. `GET /daily-radar/latest`
   2. `GET /daily-radar/{run_date}`
   3. `GET /daily-radar/symbol/{symbol}`
9. [ ] Confirm candidate and explanation copy stays in observation, tracking, attention, and risk language.
10. [ ] Confirm Daily Radar contains no new 交易指令措辭、價格承諾措辭、機率承諾措辭，或方向指令語言。
11. [ ] Confirm Daily Radar selection, ranking, bucket assignment, and risk deductions do not use LLM logic.
12. [ ] Confirm live run uses the FinMind all-market dual-track universe, not a full configured Taiwan stock universe count.
13. [ ] Confirm FinMind requests do not pass per-symbol params such as `stock_id`, `data_id`, or `symbol`.
14. [ ] Confirm FinMind makes the two all-market data pulls expected by the current flow: same-day `date` and recent `date` range.
15. [ ] Confirm yfinance runs one batch download only for selected symbols missing final raw rows.
16. [ ] Confirm live `POST /internal/daily-radar/run` disables fixture fallback.
17. [ ] Confirm current live run limitations are understood: no full live margin fetch yet and no full market context fetch yet.

## Local Fixture Run

Run this only in a local backend environment with Daily Radar fixtures and service code available.

1. [ ] Load the backend Daily Radar fixture set.
2. [ ] Run the Daily Radar service against the fixture data without external network calls.
3. [ ] Verify the run completes with deterministic rule-based output.
4. [ ] Verify all four buckets have candidates when the fixture set supports it:
   1. `institutional_accumulation`
   2. `price_volume_strengthening`
   3. `bottoming_reversal`
   4. `support_retest`
5. [ ] Verify rejected symbols carry observable risk or data reasons such as stale data, data gaps, overextended structure, market weakness, or margin crowding.
6. [ ] Verify each candidate includes `primary_bucket`, `secondary_buckets`, `observation_score`, `risk_labels`, `matched_rules`, `score_breakdown`, `explanation`, and `data_dates`.

## Local FastAPI Internal Endpoint Check

Run this only with a local FastAPI process and a local shared token environment variable. Do not paste token values into logs or docs.

1. [ ] Start the local FastAPI backend.
2. [ ] Set `DAILY_RADAR_INTERNAL_TOKEN` in the local shell.
3. [ ] Call the internal endpoint with the Bearer token path:

```bash
curl -X POST "http://127.0.0.1:8000/internal/daily-radar/run" \
  -H "Authorization: Bearer ${DAILY_RADAR_INTERNAL_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"market":"TW"}'
```

4. [ ] Optionally repeat with `X-Internal-Token` if the local auth path needs direct coverage:

```bash
curl -X POST "http://127.0.0.1:8000/internal/daily-radar/run" \
  -H "X-Internal-Token: ${DAILY_RADAR_INTERNAL_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"market":"TW"}'
```

5. [ ] Verify the response includes `run_id`.
6. [ ] Verify the response includes `status`.
7. [ ] Verify the response includes `universe_count`.
8. [ ] Verify the response includes `prefilter_count`.
9. [ ] Verify the response includes `candidate_count`.
10. [ ] Verify the response includes `errors` or an equivalent errors summary.
11. [ ] Verify the run can be read back through `GET /daily-radar/latest` when local persistence is available.
12. [ ] Verify `universe_count` reflects the selected dual-track universe, usually at most around 100 before overlap and dedupe.

## Future Manual Frontend Verification

These checks are intentionally deferred until Google login and DB access are available. They require manual browser QA by the user or release owner.

1. [ ] Log in with Google.
2. [ ] Confirm the frontend can reach the backend DB-backed Daily Radar API.
3. [ ] Open `/daily-radar`.
4. [ ] Verify the page shows the latest completed run from `GET /daily-radar/latest`.
5. [ ] Verify bucket tabs are visible and switch the candidate list correctly.
6. [ ] Verify the candidate list shows symbol, name, primary bucket, observation score, risk labels, and observation summary.
7. [ ] Verify the detail drawer opens for a candidate.
8. [ ] Verify the detail drawer shows matched rules, score breakdown, data dates, risk labels, and explanation text.
9. [ ] Verify loading state appears while data is pending.
10. [ ] Verify empty state appears when there is no completed run or no candidates.
11. [ ] Verify error state appears for API failure.
12. [ ] Verify stale state appears when data dates are outside the allowed freshness window.
13. [ ] Verify all frontend copy stays in observation and risk language.

## Future Zeabur Environment Verification

These checks are deferred until Zeabur access is available.

1. [ ] Set `DAILY_RADAR_INTERNAL_TOKEN` in Zeabur backend environment variables.
2. [ ] Set `DATABASE_URL` in Zeabur backend environment variables.
3. [ ] Confirm Alembic/startup migrations have run against the live Zeabur database, and verify the live DB contains `daily_radar_runs` and `daily_radar_candidates` before workflow dispatch is considered ready.
4. [ ] Confirm normal backend auth and CORS environment variables are present as needed for the deployed backend:
   1. `GOOGLE_CLIENT_ID`
   2. `JWT_SECRET`
   3. `CORS_ORIGINS`
5. [ ] Confirm any existing backend runtime variables required by the deployed service remain present.
6. [ ] Do not store secret values in code, docs, workflow logs, or screenshots.

## Future GitHub Secrets Verification

These checks are deferred until GitHub repository settings access is available.

1. [ ] Set repository secret `ZEABUR_BACKEND_URL`.
2. [ ] Set repository secret `DAILY_RADAR_INTERNAL_TOKEN`.
3. [ ] Confirm `ZEABUR_BACKEND_URL` points to the Zeabur backend base URL, not the frontend URL.
4. [ ] Confirm `DAILY_RADAR_INTERNAL_TOKEN` matches the Zeabur backend `DAILY_RADAR_INTERNAL_TOKEN` value.
5. [ ] Do not print either secret value during verification.

## Future Workflow Dispatch Verification

These checks are deferred until Zeabur, DB, and repository secrets are ready.

1. [ ] Open GitHub Actions for `.github/workflows/daily-radar.yml`.
2. [ ] Trigger `workflow_dispatch` manually.
3. [ ] Confirm the workflow runs job `run-daily-radar`.
4. [ ] Confirm step `Trigger Daily Radar` calls `POST /internal/daily-radar/run`.
5. [ ] Confirm the request uses `Authorization: Bearer ${DAILY_RADAR_INTERNAL_TOKEN}` through repository secrets.
6. [ ] Confirm the workflow finishes successfully on a `2xx` HTTP status.
7. [ ] If the workflow fails, capture the HTTP status and non-secret response summary for diagnosis.

## Future Run Log Inspection

Inspect the GitHub Actions log and backend run log after manual dispatch. These values must be visible without exposing secret values.

1. [ ] HTTP status is `2xx`.
2. [ ] `run_id` is present.
3. [ ] `status` is present and matches the backend run result.
4. [ ] `universe_count` is present.
5. [ ] `prefilter_count` is present.
6. [ ] `candidate_count` is present.
7. [ ] `errors_count` is present.
8. [ ] `universe_count` is consistent with the selected dual-track universe for that run, not a full-market scan count.
9. [ ] `prefilter_count` is less than or equal to `universe_count`.
10. [ ] `candidate_count` is less than or equal to `prefilter_count`.
11. [ ] `errors_count` is reviewed even when the workflow succeeds.
12. [ ] Any data gap, stale data, or source error is recorded as an observation or risk item, not as trading advice.

## Future Three Trading-Day Observation

Observe the first three trading-day runs after rollout. Treat this as production signal watching, not a one-time pass/fail gate.

1. [ ] Record `run_id`, `status`, `universe_count`, `prefilter_count`, `candidate_count`, and `errors_count` for each trading day.
2. [ ] Compare candidate counts across the three runs and flag sudden zero-count or extreme-count changes for review.
3. [ ] Confirm the stale guard blocks or marks stale data instead of letting stale inputs look fresh.
4. [ ] Review data gaps by source, symbol, and bucket.
5. [ ] Review errors and separate expected data-source gaps from implementation defects.
6. [ ] Confirm repeated symbols have sensible repeat or cooldown status.
7. [ ] Confirm bucket distribution remains explainable through matched rules and risk labels.
8. [ ] Confirm frontend `/daily-radar` continues to show the latest completed run after each daily run.

## MVP Acceptance Checklist

The MVP is accepted when these checks pass in the relevant environment.

1. [ ] Zeabur workflow can trigger a Daily Radar run through `.github/workflows/daily-radar.yml`.
2. [ ] GitHub Actions uses `ZEABUR_BACKEND_URL` and `DAILY_RADAR_INTERNAL_TOKEN` repository secrets.
3. [ ] The backend accepts the shared token for `POST /internal/daily-radar/run`.
4. [ ] The pipeline is deterministic and rule-based.
5. [ ] The pipeline persists run logs and candidates.
6. [ ] `GET /daily-radar/latest` returns the latest completed Daily Radar result.
7. [ ] `GET /daily-radar/{run_date}` returns the requested run date result when available.
8. [ ] `GET /daily-radar/symbol/{symbol}` returns symbol-level Daily Radar history when available.
9. [ ] The frontend `/daily-radar` page shows latest run details.
10. [ ] The frontend `/daily-radar` page supports bucket tabs.
11. [ ] The frontend `/daily-radar` page shows the candidate list.
12. [ ] The frontend `/daily-radar` page opens the detail drawer.
13. [ ] Loading, empty, error, and stale states are visible and understandable.
14. [ ] No LLM is used for selection, ranking, bucket assignment, or risk deduction.
15. [ ] Explanations use rule-based templates.
16. [ ] Copy uses observation-language wording and risk-language wording.
17. [ ] Copy avoids 交易指令措辭、價格承諾措辭與機率承諾措辭。

## Rollout Notes

1. Keep `POST /internal/daily-radar/run` as the only scheduled Daily Radar trigger for MVP rollout.
2. Keep public reads on `GET /daily-radar/latest`, `GET /daily-radar/{run_date}`, and `GET /daily-radar/symbol/{symbol}`.
3. Keep run-log review focused on data freshness, candidate count health, source gaps, and error patterns.
4. Treat live frontend checks, Zeabur dispatch, and DB-backed reads as future manual steps once login and DB access are ready.
5. Treat margin and market-context gaps as known current live limitations until full live fetches are added.
