# Professional Advisory Readiness Priorities

> Type: Research note / future discussion baseline
> Date: 2026-06-08
> Status: Discussion seed
> Scope: Advisor-grade readiness gaps and optimization priorities. This is intentionally not an implementation plan.

## Why This Exists

AI Stock Sentinel is already useful as an investment research, monitoring, and trading-discipline assistant. However, it should not be positioned as a professional advisory engine until its scores, data reliability, portfolio risk controls, lifecycle records, and advisory-language boundaries are stronger.

This note records the main professional-readiness gaps that future optimization discussions should start from.

## Current Positioning

Before these gaps are addressed, the product should be treated as:

```text
investment discipline system / research assistant / monitoring cockpit
```

It should not yet be treated as:

```text
validated alpha engine / fiduciary-grade advisory platform / automated recommendation system
```

## Core Gaps To Address First

### 1. Score Validity Is Not Yet Proven

The biggest gap is that the system has not yet proven that its scores are predictive.

`confidence_score`, Daily Radar `observation_score`, and bucket-level signals are explainable and rule-based, but professional use requires historical validation. A high score that is not calibrated to future outcomes can damage trust more than having no score at all.

Professional validation should include:

- 5 / 10 / 20 trading-day forward returns.
- Win rate by confidence bucket.
- Forward maximum drawdown after signal date.
- Return and drawdown by market regime.
- Daily Radar bucket-level performance.
- Monotonicity check: higher confidence should generally map to better future outcomes or lower risk.
- Calibration check: high-confidence signals should not simply select high-volatility names.

Until this exists, `confidence_score` and `observation_score` should be framed as internal signal-strength indicators, not reliable alpha scores.

The product does not need to make exact raw scores the main user-facing surface. It should keep the scoring engine internally for ranking, guardrails, calibration, backtesting, debugging, and traceability, while default UI copy should prefer tiers, labels, reasons, caveats, and data-quality warnings.

Preferred framing before calibration:

```text
internal score -> user-facing tier / label / reason
```

Avoid framing such as:

```text
73/100 confidence -> implied win rate or recommendation strength
```

Raw `0-100` values and `score_breakdown` can remain available for advanced trace, developer review, and calibration work, but should not be the headline decision signal for normal users.

### 2. Data Source Reliability Is A Practical Risk

Daily Radar and stock analysis depend on multiple data sources. Known gaps such as incomplete margin data, missing market context, fallback paths, stale rows, and provider drift can materially affect output quality.

Professional use requires a data quality layer that is visible and actionable.

Future discussions should prioritize:

- Data freshness per dimension: OHLCV, institutional flow, margin, market context, news, fundamentals.
- Provider success/failure/fallback status.
- Stale-data blocking rules.
- Missing-field severity classification.
- Whether a result is based on final close data or incomplete intraday data.
- Clear UI display of source dates and data-quality warnings.
- Provider health dashboard for success rate, latency, timeout, fallback rate, and missing fields.

If data is incomplete, the system should downgrade or block strong conclusions rather than produce confident-looking output.

### 3. LLM Narrative Can Amplify False Confidence

The system has strong rule-based guardrails, but the user ultimately reads natural-language conclusions. If the LLM makes neutral signals sound compelling, softens data-quality warnings, or overstates weak evidence, user trust will be harmed.

LLM output evaluation is therefore required, not optional.

Future discussions should cover:

- Whether LLM text faithfully follows rule-based labels and scores.
- Whether `final_verdict` conflicts with deterministic outputs.
- Whether data-quality caveats remain visible in the narrative.
- Whether neutral or low-confidence setups are written too persuasively.
- Whether advisory language drifts into unsupported recommendation language.
- Regression fixtures for common failure cases.

LLM should remain a narrative assistant. It should not compute scores, assign classifications, infer missing intent, or override deterministic evidence.

## Highest-Priority Optimization Tracks

### Track 1: Backtesting And Confidence Calibration

This should be the first major professional-readiness track.

Goals:

- Validate `confidence_score`.
- Validate Daily Radar `observation_score`.
- Validate bucket-level setup performance.
- Validate 5 / 10 / 20 trading-day forward outcomes.
- Measure forward max drawdown and risk-adjusted behavior.
- Segment results by market regime.
- Decide which raw scores deserve user-facing tiers, which need recalibration, and which should remain trace-only.

Expected outcome:

- The product can state which scores are historically useful, which need recalibration, which should be downgraded in UI language, and which should stay internal.

### Track 2: Data Quality Dashboard And Blocking Rules

This should be the second major professional-readiness track.

Goals:

- Surface source freshness and completeness clearly.
- Track provider health and fallback status.
- Add stale-data warnings and blocking rules.
- Prevent strong conclusions when critical data is missing.
- Distinguish final close analysis from incomplete intraday analysis.

Expected outcome:

- Users can immediately see whether a conclusion is based on clean, timely, complete data.

### Track 3: Lifecycle Review As Trading Discipline Core

This should become the core of the product's long-term learning loop.

Goals:

- Implement event ledger for entries, add entries, partial exits, full exits, and manual adjustments.
- Capture user plan, invalidation condition, planned risk, and scale-out rule.
- Capture reason codes and plan adherence at event time.
- Compute MAE, MFE, R-multiple, MFE capture rate, and exposure curve.
- Distinguish disciplined adaptation from emotional deviation.
- Preserve `decision_context: insufficient` when the original intent was not recorded.

Expected outcome:

- The product shifts from a market-watching tool into a decision-improvement system.

## Professional-Grade Roadmap Order

If the product is moving toward professional usefulness, future discussions should start from this sequence:

1. Complete backtesting and confidence calibration.
2. Build portfolio-level risk dashboard.
3. Implement event ledger and lifecycle review.
4. Add data freshness, provider health, and stale-data blocking.
5. Tighten advisory language with suitability, disclaimer, and audit-trail boundaries.

## Portfolio-Level Risk Gap

Current functionality is mostly single-symbol and single-position oriented. Professional advisory workflows need portfolio-level controls before outputs can be treated as advice-grade.

Future discussions should include:

- Account-level risk budget.
- Risk-per-trade based on stop distance.
- Position sizing by capital at risk, not generic percentage language.
- Sector and theme concentration.
- Aggregate exposure.
- Correlation and beta where data is available.
- Portfolio drawdown monitoring.
- Cash and liquidity constraints.

Until these exist, position sizing language should remain conservative and research-oriented.

## Advisory Language And Compliance Boundary

The product currently contains advice-like outputs such as entry zones, stop loss, suggested position size, Hold / Trim / Exit, and exit reasons. These can be useful, but they require stronger framing if the product moves toward professional use.

Future discussions should include:

- Suitability and user risk-profile boundaries.
- Research-only vs advisory-mode language.
- Explicit disclaimers and limitation disclosures.
- Saved user thesis and approval notes.
- Audit trail for accepted, rejected, or ignored suggestions.
- Clear distinction between observation, diagnosis, and recommendation.

## Working Principle

The product should become more trustworthy before it becomes more assertive.

Preferred sequence:

```text
validation -> data quality -> risk controls -> lifecycle discipline -> stronger UX
```

Avoid the opposite sequence:

```text
stronger claims -> richer UI -> more confident language -> validation later
```

## Summary

Before the professional-readiness tracks are complete, AI Stock Sentinel is best described as a strong investment discipline system. After backtesting, data-quality controls, lifecycle review, portfolio-risk management, and advisory-language boundaries are added, it can move closer to a professional advisor workflow.
