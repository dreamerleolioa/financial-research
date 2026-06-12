# Investment Discipline Release Gate

Date: 2026-06-12

Status: Phase 6 release gate skeleton is implemented for local and CI verification.

## Scope

This gate prevents future changes from reintroducing unvalidated scoring drivers, signal-zoo drift, command-language primary copy, validation-as-marketing claims, or portfolio-risk action misuse.

It does not add scoring, ranking, portfolio mutation, or user-facing trading-action features.

## Required Checklist

Before merging a change that touches Daily Radar scoring, validation, frontend copy, portfolio risk, or investment-discipline docs:

1. **Scoring signal registry**
   - Every scoring rule code used by live Daily Radar scoring has a rule registry entry.
   - Each registry entry has rule code, description, tier, owner module, validation status, first version, and last reviewed version.
   - New signals start as `context_only` or `confirming_evidence` unless validation evidence supports driver status.

2. **Rule tier and scoring impact**
   - `deprecated` and `context_only` rules must not affect score, ranking, bucket, risk penalty, or live candidate sorting.
   - Any live scoring/ranking behavior change requires scoring/rule version bump, changelog/spec note, and fixture/test update.

3. **Validation evidence**
   - Forward validation reports remain deterministic, sorted, and free of wall-clock timestamps.
   - Validation output stays positioned as rule-quality / calibration diagnostics.
   - Validation metrics must not be presented as public win-rate claims or performance marketing.

4. **Copy guard**
   - Primary user-facing surfaces use risk state, discipline trigger, observation condition, risk-control reference, and data caveat language.
   - Command-like terms are allowed only when explicitly allowlisted for user-recording controls, negative boundary statements, or legacy/internal compatibility docs.
   - Compatibility fields such as `recommended_action`, `trailing_stop`, `exit_reason`, and `command_language_deprecated` remain secondary.

5. **Portfolio risk data gaps**
   - Portfolio risk summary remains read-only.
   - Missing price, missing defense reference, zero quantity, and stale price must produce `data_quality.caveats[]`.
   - Missing required data must not be converted into neutral evidence or fabricated zero risk.
   - Portfolio risk diagnostics must not produce portfolio actions, trading commands, or recommended actions.

6. **Cloud reporting boundary**
   - Formal forward validation and monthly rule-review artifacts come from cloud backend production DB / production market data.
   - Local CLI and fixtures are for development, debug, and regression tests only.
   - Monthly rule-review reports produce automated recommendations only; they do not modify live scoring.

## Automated Checks

Run backend release-gate tests:

```bash
cd backend
uv run pytest -q \
  tests/test_daily_radar_rule_governance.py \
  tests/test_daily_radar_forward_validation.py \
  tests/test_risk_language_copy_guard.py \
  tests/test_portfolio_risk_summary.py \
  tests/test_portfolio_router.py \
  tests/test_portfolio_history.py \
  tests/test_investment_discipline_release_gate.py \
  tests/test_compatibility_deprecation_audit.py
```

Run frontend build/typecheck when frontend copy or UI is touched:

```bash
cd frontend
pnpm build
```

## CI Gate

`.github/workflows/investment-discipline-release-gate.yml` runs the backend release-gate test set and frontend build on pull requests targeting `main` and manual dispatch. It intentionally does not run on pushes to `main`.

The workflow does not call production internal APIs. Cloud forward validation and monthly rule review remain scheduled/triggered separately through internal endpoints and artifact workflows.

## Monitoring Hooks

1. Daily forward validation should continue to run over due windows through the cloud internal API.
2. Monthly rule-review should continue to save JSON and Markdown artifacts for human review.
3. Any failing release-gate test is a blocker for merging investment-discipline surfaces.

## Deferred

Compatibility deprecation audit is recorded in `docs/plans/2026-06-12-compatibility-deprecation-audit.md`.

Current removal decision: **no-go**.

Legacy compatibility fields still have dependencies in API schemas, historical cache, portfolio history, frontend secondary views, internal analysis context, and external-client contracts. Do not remove them until the audit closure steps are completed and verified.
