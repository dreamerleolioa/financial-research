# Investment Discipline Execution Plan

| Metadata | Value |
| --- | --- |
| Status | planned |
| Date | 2026-06-11 |
| Scope | Turn AI Stock Sentinel into a replayable Taiwan equity research discipline system |
| Reference roadmap | `docs/plans/2026-06-11-investment-discipline-validation-roadmap.md` |
| Execution prompts | `prompt-investment-discipline-execution.md` |
| Execution commands | `docs/plans/2026-06-11-investment-discipline-execution-commands.md` |

## 1. Objective

The project should not optimize toward "recommending stocks that always make money." The correct objective is:

```text
Generate daily observable setups, capture the original strategy thesis at entry time, monitor position risk while holding, and review the full trade lifecycle with point-in-time evidence.
```

Success means the system can classify every completed operation into one of these outcomes:

1. Strategy quality issue: the setup/rule did not show useful forward behavior.
2. Execution discipline issue: the recorded plan was violated.
3. Risk management issue: position sizing, defense reference, or portfolio exposure was poor.
4. Data-quality issue: source data was missing, stale, or insufficient.
5. Outside-technical-analysis event: the outcome was driven by an event not represented in the available technical/chip/context evidence.

This plan is for research discipline and risk review. It must not create trading commands, broker execution, guaranteed win-rate claims, target-price promises, or public investment-advice positioning.

## 2. Current System Fit

The current codebase already has the right foundation:

| Area | Current state | Execution implication |
| --- | --- | --- |
| Daily Radar | Deterministic setup buckets, matched rules, scoring version, rule version, score breakdown, input snapshot. | Good base for forward outcome validation. |
| Entry context | Fixed-option taxonomy, backend persistence, provenance, frontend surfaces, and lifecycle decision context already exist. | Audit end to end and close only missing seams. |
| Position diagnosis | Rule-based position metrics, trailing defense reference, and action-like compatibility fields. | Keep deterministic behavior, migrate primary copy to risk language. |
| Trade review | Single closed-row review exists. | Useful for MVP review, but limited for partial entries/exits. |
| Lifecycle review | Event-level review, point-in-time shared context, and evidence payload exist. | Strong base for plan adherence and execution discipline review. |
| Shared context | Background context can be read as evidence/caveat. | Must remain context only, not a direct action driver. |

The main gap is not signal coverage. The main gap is rule validation and governance: too many plausible indicators can create post-hoc explanation risk unless weak signals can be measured, demoted, or removed.

## 3. Execution Order

The default execution order for the user's stated goal is:

| Order | Slice | Why first | Merge rule |
| --- | --- | --- | --- |
| 0 | Baseline readiness check | Avoid mixing this work with unfinished Daily Radar v2 changes. | Documentation/check only. |
| 1 | Forward Outcome Validation | Measures whether Daily Radar buckets/rules have useful forward behavior. | Offline report only; no live scoring change. |
| 2 | Entry Strategy Context Audit And Gap Closure | Verifies the existing entry-context implementation and closes gaps that still block strategy-vs-execution review. | Audit first; implement only missing seams. |
| 3 | Rule Pruning And Score Governance | Turns validation into a process for demoting weak signals. | Versioned rule registry; live scoring changes require evidence and tests. |
| 4 | Risk Language Alignment | Replaces primary user-facing command language with risk state and discipline triggers. | Backward-compatible; keep legacy fields secondary. |
| 5 | Portfolio Risk Layer | Adds position and portfolio-level risk discipline. | Read-only summary; no portfolio mutation or trading action. |
| 6 | Release Gate And Monitoring | Prevents future regression into signal zoo or command-language output. | Tests/checks/docs only. |

This intentionally starts with forward validation before major copy or scoring changes. If the system cannot measure setup outcomes, every later scoring debate remains subjective.

## 4. Coverage And Acceptance Matrix

This matrix is the checklist for whether the plan covers the user's requested optimization areas.

| Optimization area | Covered by | Acceptance target |
| --- | --- | --- |
| Recommend stocks to watch without becoming a trading-advice engine | Phase 1 and Phase 4 | Daily Radar candidates remain observation setups; validation report and UI copy do not present buy/sell/target-price/win-rate claims. |
| Use technical indicators as evidence, not post-hoc narrative | Phase 1 and Phase 3 | Forward validation groups outcomes by bucket, matched rule, risk label, market regime, relative strength, and data freshness; weak or low-sample rules can be demoted. |
| Evaluate whether a setup/rule actually works after selection | Phase 1 | Stable JSON report includes 5/10/20 trading-day forward return, benchmark excess return, MFE, MAE, defense-reference breach, sample count, and skip reasons. |
| Capture the original entry thesis before review hindsight appears | Phase 2 | Existing entry/add-entry context capture is audited end to end; any missing fixed-option/provenance/UI/review gaps are closed without duplicating implemented flows. |
| Distinguish strategy failure from execution deviation | Phase 2 and lifecycle review | Lifecycle review can use recorded strategy context for plan adherence; missing context returns `decision_context: insufficient` instead of inferred intent. |
| Distinguish data-quality failure from true strategy failure | Phase 1, Phase 2, Phase 5, Phase 6 | Validation, lifecycle, and portfolio risk surfaces preserve missing/stale/insufficient data caveats and skip reasons. |
| Identify outcomes outside available technical/chip/context evidence | Phase 2 and lifecycle review | Reviews preserve point-in-time evidence boundaries and classify insufficient/outside-evidence cases rather than forcing a technical explanation. |
| Turn many plausible indicators into governed rules | Phase 3 | Rule registry covers scoring drivers; each rule has tier, owner, validation status, and version trace. |
| Prevent unvalidated signal expansion | Phase 3 and Phase 6 | New signals start as `context_only` or `confirming_evidence`; new drivers require validation evidence or experimental flag. |
| Improve holding/exit discipline without command-language advice | Phase 4 | Primary user-facing surfaces show risk states, discipline triggers, observation conditions, and risk-control references; legacy action fields remain secondary/compatible. |
| Add portfolio-level risk context | Phase 5 | Read-only risk summary reports portfolio value, total at risk, position risks, concentration, shared exposures, risk budget status, and data quality. |
| Keep future work from regressing | Phase 6 | Release gate includes rule registry coverage, copy guard, deterministic validation report checks, and portfolio data-gap tests. |

The plan is incomplete if any phase ships without its acceptance target being demonstrably checked.

## 5. Phase Details

### Phase 0: Baseline Readiness Check

Goal:

- Confirm Daily Radar v2 is at a stable checkpoint.
- Confirm candidate snapshots include bucket, matched rules, score breakdown, data dates, and scoring/rule versions.
- Confirm shared background context is clearly separated from daily trigger signals.

Read first:

- `docs/specs/daily-stock-radar-spec.md`
- `docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md`
- `docs/plans/2026-06-11-daily-radar-v2-phase-1d-backtest-calibration.md`
- `backend/src/ai_stock_sentinel/daily_radar/scoring.py`
- `backend/src/ai_stock_sentinel/daily_radar/calibration.py`
- `backend/src/ai_stock_sentinel/daily_radar/repository.py`

Done when:

- There is a clear go/no-go note for Phase 1.
- Any blocker is listed as data, schema, or worktree status, not left vague.

### Phase 1: Forward Outcome Validation

Goal:

- Build deterministic forward validation for Daily Radar candidates.
- Provide a cloud internal API that validates matured production candidates after daily market data updates.
- Evaluate 5/10/20 trading-day forward behavior.
- Preserve missing/stale/future-data skip reasons.

Minimum metrics:

- `forward_return_pct`
- `excess_return_vs_benchmark_pct`
- `max_favorable_excursion_pct`
- `max_adverse_excursion_pct`
- `close_below_defense_reference`
- `hit_rate_above_threshold` as diagnostic only
- `profit_factor_like_ratio`
- `sample_count`

Minimum groupings:

- primary bucket
- secondary bucket
- matched rule code
- risk label
- market regime
- relative strength bucket
- repeat status
- score decile
- data freshness status

Non-goals:

- Do not change live scoring.
- Do not advertise win rate.
- Do not use future data to reconstruct the candidate reason.

Required trigger surfaces:

- Local CLI for fixture, local development, and regression testing only.
- Cloud internal API for official production validation, for example `POST /internal/daily-radar/forward-validation/run`.
- The official validation source must be cloud production DB snapshots and market data, not local fixture files.
- The internal API should run in `mode: due`, finding historical candidates whose 5/10/20 trading-day windows have matured by `as_of_date`.
- The validation write path must be idempotent by candidate/window/validation version.

Done when:

- A local fixture command can output stable JSON for tests.
- The cloud internal API can process due production candidates and return validated/skipped counts.
- Fixture tests prove outcome math and skip reasons.
- API tests prove idempotency, due-window selection, and skip-reason handling.
- Documentation states this is calibration research, not performance marketing.

### Phase 2: Entry Strategy Context Audit And Gap Closure

Goal:

- Audit the existing fixed-option entry strategy context implementation.
- Close only the gaps that still prevent reliable plan-adherence review.
- Make later review able to distinguish plan adherence from strategy failure without re-implementing already shipped flows.

Current implementation baseline to verify:

- `EntryRecordContext` already defines `entry_reason`, `planned_holding_period`, `default_stop_rule`, `add_entry_condition`, and optional `note`.
- `POST /portfolio` already accepts `entry_record`, creates an initial `PositionEvent`, and writes a `PositionLifecyclePlan` when lifecycle-plan fields are present.
- `PositionEvent` and `PositionLifecyclePlan` already include provenance/source fields.
- Frontend entry, backfill, add-entry, and lifecycle review surfaces already reference these concepts.

Required fields:

- `entry_reason`
- `planned_holding_period`
- `default_stop_rule`
- `add_entry_condition`
- optional note as secondary evidence only
- provenance such as `user_recorded_at_event_time`, `user_backfilled`, or `not_recorded`

Non-goals:

- Do not infer intent from price movement, PnL, or later outcomes.
- Do not make free-text journal analysis the primary data source.
- Do not block lifecycle review when fields are missing; show `decision_context: insufficient`.

Done when:

- Audit notes or tests confirm all entry-context surfaces still work end to end.
- Any missing capture, provenance, UI, or lifecycle-review gaps are closed.
- Existing/backfilled context remains marked with provenance.
- Lifecycle review can use the context without pretending backfilled data existed at entry time.
- No duplicate schema, endpoint, or UI flow is introduced for fields that already exist.

### Phase 3: Rule Pruning And Score Governance

Goal:

- Give every scoring signal a tier, owner, and validation status.
- Use forward validation/ablation to demote weak or low-sample rules.
- Provide a monthly cloud rule-review workflow that produces the report the user can hand back for strategy optimization.

Rule tiers:

- `driver`
- `confirming_evidence`
- `risk_modifier`
- `context_only`
- `deprecated`

Governance rules:

1. New indicators start as `context_only` or `confirming_evidence` unless validation supports promotion.
2. Low-sample signals cannot become drivers.
3. Live scoring changes require scoring/rule version bump.
4. Deprecated/context-only rules cannot affect score.

Done when:

- Rule registry covers scoring drivers.
- Ablation report is deterministic.
- Candidate traces can explain historical rule version and rule code.
- A monthly internal API or cloud job can generate rule-review JSON and Markdown from production validation results.
- The monthly report is delivered as a GitHub Actions artifact or equivalent downloadable artifact.
- The report clearly separates automated recommendations from human-approved versioned strategy updates.

Official report delivery:

- Local CLI reports are development artifacts only.
- Formal monthly rule-review reports must be produced by cloud backend using production DB.
- GitHub Actions should call the cloud internal API instead of reading production DB directly unless the workflow has approved DB access.
- Suggested endpoint: `POST /internal/daily-radar/rule-review/monthly`.
- Suggested artifact outputs:
  - `reports/daily-radar/monthly/YYYY-MM-forward-validation.json`
  - `reports/daily-radar/monthly/YYYY-MM-rule-review.md`
  - `reports/daily-radar/monthly/YYYY-MM-rule-review.json`
- The user should be able to download the monthly artifact and provide it for a versioned strategy update plan.

### Phase 4: Risk Language Alignment

Goal:

- Make primary user-facing output describe risk state, discipline triggers, observation conditions, and data caveats.

Compatibility strategy:

- Keep fields such as `recommended_action`, `trailing_stop`, and `exit_reason` temporarily for clients.
- Add risk-language fields and move UI primary copy to them.
- Mark command-like fields as compatibility/internal or secondary.

Done when:

- Primary UI copy does not present buy/sell/add/trim/exit commands.
- Backward-compatible fields still exist.
- Copy guard tests or scans prevent regression.

### Phase 5: Portfolio Risk Layer

Goal:

- Add read-only portfolio risk summary.
- Answer whether a candidate or active position fits the portfolio risk state.

Minimum output:

- portfolio value
- total unrealized PnL
- total at risk
- position-level risk amount
- position risk percentage of portfolio
- concentration by symbol
- shared setup/regime/risk-label exposure
- data quality caveats

Non-goals:

- Do not mutate positions.
- Do not create portfolio action or trading command.
- Do not invent sector classifications if reliable sector data is absent.

Done when:

- `GET /portfolio/risk-summary` or equivalent read-only surface exists.
- Missing price/defense reference/stale data returns caveats.
- User scoping is tested.

### Phase 6: Release Gate And Monitoring

Goal:

- Prevent future work from reintroducing unvalidated drivers, command-language copy, or hidden future-data leakage.

Required gates:

1. New scoring signal has rule-tier classification.
2. New driver signal references validation evidence or stays experimental.
3. User-facing copy avoids command-language primary surfaces.
4. Scoring/rule changes include version bump and changelog.
5. Portfolio risk handles missing/stale data.
6. Validation reports are deterministic and not public performance claims.

Done when:

- Release checklist exists.
- Automated checks cover rule registry, copy guard, validation report determinism, and portfolio risk data gaps.

## 6. Verification Strategy

Use the smallest verification set that proves the changed surface:

| Change type | Required verification |
| --- | --- |
| Docs only | Markdown review and link/path check. |
| Validation math | Unit tests for forward return, MFE, MAE, benchmark excess return. |
| Report shape | Snapshot test with stable sorted JSON and no wall-clock timestamp. |
| API addition | Contract tests and auth/user scoping tests where relevant. |
| Frontend copy/types | `pnpm build` and copy guard/string scan. |
| Scoring behavior | Existing scoring tests plus version trace assertion. |
| Lifecycle review | Point-in-time/future-data leakage tests. |

## 7. Execution Rules For Agents

1. Execute one phase at a time.
2. Stop after the phase and report changed files, verification commands, residual risks, and next phase.
3. Do not silently proceed from validation into live scoring changes.
4. Do not rewrite specs with planned behavior as if it is implemented.
5. Do not remove legacy fields unless a migration phase explicitly says so.
6. Do not use LLM output for scoring, risk states, validation metrics, or governance conclusions.
7. If data is missing, report the missing reason instead of imputing neutral evidence.
