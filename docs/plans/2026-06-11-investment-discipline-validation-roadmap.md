# Investment Discipline Validation And Risk Roadmap

| Metadata | Value |
| --- | --- |
| Status | planned, after current Daily Radar v2 optimization |
| Date | 2026-06-11 |
| Scope | Strategy validation, rule pruning, portfolio risk, and compliance-language alignment |
| Related specs | `docs/specs/daily-stock-radar-spec.md`, `docs/specs/backend-api-technical-spec.md`, `docs/specs/ai-stock-sentinel-position-diagnosis-spec.md` |

This roadmap records the next product-quality layer after the current Daily Radar v2 optimization finishes. It is not a replacement for the current Daily Radar v2 work. It should start only after the shared background context/cache work reaches a stable merge point.

The goal is to move AI Stock Sentinel from a signal-rich stock analysis tool toward a replayable investment discipline system. The project should not become an AI stock-picking product whose value depends on persuasive explanations. Its value should come from verified setup behavior, explicit risk controls, and consistent research language.

## 1. Problems To Solve

### 1.1 Signal zoo risk

The current system uses many reasonable-looking signals: MA, RSI, MACD, KD, ADX, OBV, ATR, MFI, Donchian, institutional flow, margin, lending, news, and fundamentals.

The risk is not that any single signal is wrong. The risk is that the system can always find a post-hoc explanation. If every candidate can be justified by some combination of signals, the score becomes narrative complexity rather than edge.

This roadmap must add a mechanism that can demote, remove, or isolate weak signals.

### 1.2 Compliance-language conflict

Daily Radar already uses observation language, but position diagnosis and action-plan surfaces still expose command-like wording such as `Hold`, `Trim`, `Exit`, entry zones, and stop-loss suggestions.

For personal use, this is tolerable. For external use, it moves the product toward individualized investment advice. The system should instead describe risk state, discipline triggers, and observation conditions. It can support user decisions without issuing trading commands.

### 1.3 Missing portfolio-level risk

Single-symbol analysis does not answer the questions a professional advisor must ask:

- How much can this position lose?
- Does this setup increase concentration risk?
- Are multiple holdings exposed to the same factor, sector, or market regime?
- Is the user adding risk in a weak market?
- Does this idea fit the portfolio, or is it only attractive in isolation?

The product needs a portfolio risk layer before it can credibly present itself as a discipline system.

## 2. Product Positioning Decision

Recommended positioning:

```text
AI Stock Sentinel is a Taiwan equity research discipline system:
daily setup radar, position risk monitoring, and trade review with replayable evidence.
```

Non-positioning:

```text
AI stock picker, AI investment advisor, buy/sell recommendation engine, guaranteed win-rate scanner.
```

This distinction should drive copy, API naming, UI hierarchy, and tests.

## 3. Phase Order

| Phase | Name | Purpose | Mergeability |
| --- | --- | --- | --- |
| A | Language Boundary Alignment | Remove command-like language from user-facing research surfaces. | Can ship alone; lowers product/compliance risk immediately. |
| B | Forward Outcome Validation | Measure future return and drawdown by bucket/rule/risk label. | Can ship as offline reports without changing live scoring. |
| C | Rule Pruning And Score Governance | Use validation results to demote, remove, or isolate weak signals. | Can ship rule-versioned scoring changes after reports exist. |
| D | Portfolio Risk Layer | Add position and portfolio-level risk diagnostics. | Can ship read-only risk dashboards before changing any flow. |
| E | Release Gate And Ongoing Monitoring | Make validation and language checks part of future changes. | Can ship as CI/scripts/docs after A-D exist. |

If only one phase can be done first, choose Phase B. It creates the evidence needed to simplify rules instead of debating signal value subjectively.

## 4. Phase A: Language Boundary Alignment

### Goal

Make all public-facing language consistent with a research/discipline product, not a buy/sell advisory product.

### Scope

1. Audit user-facing API fields, frontend labels, generated explanations, docs, and prompt text for command-like language.
2. Replace trade-command fields in presentation layers with risk-state and discipline-trigger language.
3. Keep internal backward-compatible fields temporarily where needed, but stop using them as primary UI copy.
4. Add copy snapshot tests for Daily Radar, position diagnosis, and review surfaces.

### Recommended Vocabulary

| Current wording | Preferred wording |
| --- | --- |
| Buy, sell, add, exit | Observe, monitor, risk elevated, condition triggered |
| `Hold` | `risk_state: stable` or `position_condition: intact` |
| `Trim` | `risk_state: elevated` and `discipline_trigger: profit_protection_review` |
| `Exit` | `risk_state: critical` and `discipline_trigger: defense_line_breached` |
| Entry zone | Observation zone |
| Stop-loss | Risk-control reference or defense reference |
| Recommended action | Risk state, triggered conditions, review prompts |
| Strong recommendation | High-confluence observation |

### API Direction

Do not break existing clients immediately. Add additive fields first, then migrate UI.

Suggested additive fields:

```text
position_analysis.risk_state
position_analysis.risk_state_label
position_analysis.discipline_triggers
position_analysis.observation_conditions
position_analysis.risk_control_reference
position_analysis.command_language_deprecated
```

Existing fields such as `recommended_action`, `trailing_stop`, and `exit_reason` can remain for compatibility during migration, but frontend should prefer the new risk-language fields.

### Acceptance Criteria

1. Daily Radar, `/analyze`, `/analyze/position`, portfolio diagnosis, single trade review, and lifecycle review use consistent observation/risk language in primary UI.
2. No user-facing heading or primary CTA says the system recommends buying, selling, adding, trimming, or exiting.
3. Compatibility fields are documented as legacy/internal or secondary.
4. Snapshot tests fail if command-like copy returns to primary user-facing surfaces.

### Suggested Tests

| Area | Suggested test |
| --- | --- |
| Backend response copy | API snapshot tests for representative analysis and position diagnosis responses. |
| Frontend copy | Component/page tests or string scan for banned command terms in primary surfaces. |
| Docs/spec sync | `rg` scan for command language in specs with allowlist for compatibility-field definitions. |

## 5. Phase B: Forward Outcome Validation

### Goal

Prove which setup buckets and rules have useful forward behavior. Validation should not be used to advertise win rate; it is a rule-quality and calibration mechanism.

### Scope

Build an offline deterministic validation workflow that evaluates historical Daily Radar candidates and, later, `/analyze` new-position setup outputs.

Minimum forward windows:

- 5 trading days
- 10 trading days
- 20 trading days

Minimum outcome metrics:

| Metric | Purpose |
| --- | --- |
| forward_return_pct | Basic future return after setup appears. |
| excess_return_vs_benchmark_pct | Whether setup outperformed TAIEX or configured benchmark. |
| max_favorable_excursion_pct | Whether the setup offered upside before the window ended. |
| max_adverse_excursion_pct | Practical drawdown risk after appearance. |
| close_below_defense_reference | Whether risk-control reference was violated. |
| hit_rate_above_threshold | Diagnostic only, not user-facing win-rate marketing. |
| profit_factor_like_ratio | Aggregate upside/downside quality. |
| sample_count | Prevent overreading tiny buckets. |

Minimum grouping dimensions:

- primary bucket
- secondary bucket
- matched rule code
- risk label
- market regime
- relative strength bucket
- repeat status
- score decile
- data freshness status

### Validation Rules

1. Use point-in-time candidate snapshots where available.
2. Do not use future data to reconstruct why a candidate was selected.
3. Treat missing future prices as explicit skip reasons.
4. Include transaction-cost and slippage assumptions as optional diagnostics, not default claims.
5. Report sample size prominently before any performance metric.

### Report Shape

The report should be stable JSON and optionally a Markdown summary.

Suggested sections:

```text
metadata
sample_summary
bucket_outcomes
rule_outcomes
risk_label_outcomes
market_regime_outcomes
score_decile_outcomes
ablation_candidates
skip_reasons
version_manifest
```

### Acceptance Criteria

1. A single command can generate a deterministic report for a date range.
2. Report includes sample counts, forward returns, drawdowns, benchmark-relative outcomes, and skip reasons.
3. Report can compare at least current scoring version vs one prior saved version or fixture baseline.
4. No live scoring behavior changes in Phase B.
5. Documentation explicitly states that this report is for calibration, not public performance advertising.

### Suggested Command

```bash
cd backend
uv run python scripts/daily_radar_forward_validation.py \
  --market TW \
  --start-date 2026-06-01 \
  --end-date 2026-06-30 \
  --windows 5,10,20 \
  --output /tmp/daily-radar-forward-validation.json
```

### Suggested Tests

| Area | Suggested test |
| --- | --- |
| Outcome math | Unit tests for forward return, MFE, MAE, and benchmark excess return. |
| Missing data | Fixture tests for stale candidate price, missing benchmark, suspension-like gaps. |
| Grouping | Tests that bucket/rule/risk label aggregations are stable. |
| Report stability | Snapshot test for fixture report sorted keys and no wall-clock timestamp. |

## 6. Phase C: Rule Pruning And Score Governance

### Goal

Turn validation into a rule-management process. Weak signals should be removed, demoted, or moved from scoring to explanation.

### Rule Tiers

| Tier | Meaning | Allowed use |
| --- | --- | --- |
| Driver | Validation supports signal value and enough sample size exists. | Can affect bucket score or ranking. |
| Confirming evidence | Useful as secondary context but weak standalone signal. | Can appear in explanations or cross-confirmation. |
| Risk modifier | Does not predict upside but helps detect drawdown or overextension. | Can affect risk labels or penalties. |
| Context only | Helpful to understand background but not validated as edge. | Can appear in detail view only. |
| Deprecated | No longer useful or too noisy. | Must not affect scoring or primary explanations. |

### Required Governance

1. Every scoring signal must have a rule code, tier, owner module, and validation status.
2. Every scoring-version change must list which rules changed and why.
3. Any new indicator added to scoring must first ship as `context_only` or `confirming_evidence` unless validation supports promotion.
4. If ablation shows a signal does not improve outcome or drawdown quality, demote it.
5. If sample size is too small, keep it out of driver tier.

### Ablation Tests

Minimum ablation groups:

- remove news sentiment
- remove fundamental valuation
- remove MFI
- remove OBV
- remove KD
- remove Donchian
- remove institutional flow
- remove margin-related risk labels
- remove relative strength
- remove market regime penalty

The goal is not to prove every signal matters. The goal is to simplify the system until remaining driver signals have evidence.

### Acceptance Criteria

1. Each rule has a tier and validation status.
2. Scoring code and docs can distinguish score drivers from context-only signals.
3. At least one validation report can recommend rule demotion or confirm no demotion is justified.
4. Live scoring changes require a scoring/rule version bump.
5. API trace exposes rule version and rule codes enough to interpret historical candidates.

### Suggested Files

| Purpose | Suggested file |
| --- | --- |
| Rule registry | `backend/src/ai_stock_sentinel/daily_radar/rule_registry.py` |
| Validation status | `backend/src/ai_stock_sentinel/daily_radar/rule_validation.py` |
| Ablation script | `backend/scripts/daily_radar_rule_ablation.py` |
| Tests | `backend/tests/test_daily_radar_rule_governance.py` |

## 7. Phase D: Portfolio Risk Layer

### Goal

Move the product from single-symbol analysis toward portfolio-level risk discipline.

### Scope

Start read-only. Do not change orders, positions, or portfolio rows in this phase.

Minimum portfolio diagnostics:

| Diagnostic | Purpose |
| --- | --- |
| position_risk_amount | Estimated loss if defense reference is breached. |
| position_risk_pct_of_portfolio | Single-position risk relative to portfolio value. |
| total_at_risk | Sum of active position risk estimates. |
| concentration_by_symbol | Prevent oversized single names. |
| concentration_by_sector_or_theme | Prevent hidden same-factor exposure when sector data exists. |
| correlated_setup_count | Count positions/candidates with same bucket, regime, or risk label. |
| market_regime_exposure | Show how much portfolio is exposed during risk-off regime. |
| cash_buffer | If cash is tracked, show ability to add risk without concentration. |

If sector/theme data is not reliable yet, start with symbol-level and setup-bucket concentration. Do not invent sector classifications.

### Risk Language

Portfolio risk must not say "buy more" or "sell now." It should say:

- portfolio risk is concentrated
- single-position loss would exceed configured threshold
- multiple holdings share the same adverse market regime exposure
- new candidate would increase already-crowded exposure
- risk budget is available or constrained

### Suggested API Surface

Additive endpoint:

```text
GET /portfolio/risk-summary
```

Suggested response fields:

```text
portfolio_value
total_unrealized_pnl
total_at_risk
total_at_risk_pct
position_risks[]
concentration[]
shared_exposures[]
risk_budget_status
data_quality
```

Per-position fields:

```text
symbol
quantity
current_price
entry_price
market_value
defense_reference
estimated_risk_amount
estimated_risk_pct_of_portfolio
risk_state
discipline_triggers
data_quality
```

### Acceptance Criteria

1. User can see portfolio-level risk without running new LLM analysis.
2. Risk summary is deterministic and explainable from stored portfolio rows plus market data.
3. Missing prices or missing defense references produce data-quality caveats, not fabricated risk.
4. No endpoint creates portfolio actions or trade commands.
5. Frontend presents portfolio risk as monitoring/discipline information.

### Suggested Tests

| Area | Suggested test |
| --- | --- |
| Risk math | Unit tests for position risk amount and portfolio percentage. |
| Data gaps | Missing price, zero quantity, missing defense reference. |
| User scoping | Portfolio risk only includes current user's positions. |
| API contract | `GET /portfolio/risk-summary` schema test. |
| Copy | Snapshot or string scan for command-like language. |

## 8. Phase E: Release Gate And Ongoing Monitoring

### Goal

Prevent future work from reintroducing signal zoo complexity or command-like advisory language.

### Required Gates

1. New scoring signals require rule-tier classification.
2. New driver signals require validation report reference or a temporary experimental flag.
3. User-facing copy cannot introduce buy/sell command language without explicit product decision.
4. Scoring-version changes require version bump and changelog entry.
5. Portfolio risk changes must include data-quality behavior for missing prices and stale data.

### Suggested Automation

```bash
cd backend
uv run pytest -q tests/test_daily_radar_rule_governance.py
```

```bash
cd frontend
pnpm build
```

Optional copy scan:

```bash
rg -n "建議買|建議賣|買進|賣出|加碼|減碼|出場|必買|目標價|勝率" frontend/src backend/src docs/specs docs/plans
```

The copy scan needs an allowlist because specs may define deprecated compatibility fields. It should fail only on primary user-facing copy after Phase A creates the allowlist.

Phase 6 implementation note:

- The maintained checklist is `docs/plans/2026-06-12-investment-discipline-release-gate.md`.
- The automated CI gate is `.github/workflows/investment-discipline-release-gate.yml`.
- The primary copy guard uses an explicit allowlist in `backend/tests/test_risk_language_copy_guard.py` for user-recording controls, negative boundary statements, and compatibility docs.
- The gate is intentionally test/build only; it does not call cloud internal APIs or produce public validation claims.

## 9. Data And Compliance Guardrails

1. Do not advertise validation metrics as guaranteed win rate.
2. Do not let LLM generate or overwrite scoring outcomes, portfolio actions, risk states, or validation metrics.
3. Do not use future data in lifecycle review or forward validation inputs.
4. Do not hide low sample size behind averages.
5. Do not promote a signal to driver tier only because it sounds financially plausible.
6. Do not treat missing data as neutral evidence.
7. Do not convert portfolio risk diagnostics into trade commands.
8. Do not add suitability claims unless the product deliberately collects investor profile, objectives, risk tolerance, horizon, constraints, and consent.

## 10. Non-Goals

1. This roadmap does not turn the product into a licensed investment advisor workflow.
2. This roadmap does not add broker execution or order routing.
3. This roadmap does not promise alpha, win rate, or target prices.
4. This roadmap does not require immediate removal of existing compatibility fields.
5. This roadmap does not require full sector/factor modeling before basic position risk exists.
6. This roadmap does not block current Daily Radar v2 shared background context work.

## 11. Recommended Start Criteria

Start this roadmap only after:

1. Current Daily Radar v2 optimization is merged or paused at a stable checkpoint.
2. Daily Radar candidates persist enough trace to identify bucket, matched rules, score breakdown, and input dates.
3. Shared background context work has a clear boundary so validation can distinguish trigger signals from background context.
4. Existing dirty worktree is clean enough that language changes and validation scripts do not mix with unrelated feature work.

## 12. First Implementation Slice

Recommended first slice after current optimization:

1. Add Phase B fixture-based forward validation script for Daily Radar candidates.
2. Generate report for a small deterministic fixture set.
3. Add rule-level grouping and sample counts.
4. Document that no live scoring changed.

This first slice is useful even before portfolio risk exists because it creates the evidence needed to simplify the scoring model.
