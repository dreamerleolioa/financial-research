# Daily Radar Rule Pruning And Score Governance

| Metadata | Value |
| --- | --- |
| Status | Phase 3 implementation note |
| Date | 2026-06-12 |
| Scope | Rule registry, ablation diagnostics, monthly rule-review report |
| Related plan | `docs/plans/2026-06-11-investment-discipline-execution-plan.md` |

## Purpose

Rule governance turns deterministic forward validation into an auditable review process. It does not claim that a signal is effective without validation evidence, and it does not modify live scoring automatically.

The monthly report is a rule-quality and calibration diagnostic. It is not a public win-rate report, performance advertisement, trading recommendation, or human-approved strategy update.

## Rule Registry

Registry source:

```text
backend/src/ai_stock_sentinel/daily_radar/rule_registry.py
```

Each entry records:

- `rule_code`
- `description`
- `tier`
- `owner_module`
- `validation_status`
- `first_version`
- `last_reviewed_version`
- `ablation_group`

Supported tiers:

- `driver`
- `confirming_evidence`
- `risk_modifier`
- `context_only`
- `deprecated`

`context_only` and `deprecated` rules must not affect scoring. New signals should start as `context_only` or `confirming_evidence` unless validation evidence supports promotion.

## Ablation Workflow

Local fixture/debug command:

```bash
cd backend
uv run python scripts/daily_radar_rule_ablation.py \
  --source fixture \
  --market TW \
  --run-date 2026-05-29 \
  --as-of-date 2026-06-26 \
  --windows 5,10,20 \
  --output /tmp/daily-radar-rule-ablation.json
```

The ablation report compares validated forward outcomes for candidates with and without each ablation group. It is deterministic and includes sample counts, excess-return deltas, profit-factor-like diagnostics, and insufficient-sample cases.

Required ablation groups:

- `news_sentiment`
- `fundamental_valuation`
- `mfi`
- `obv`
- `kd`
- `donchian`
- `institutional_flow`
- `margin_related_risk_labels`
- `relative_strength`
- `market_regime_penalty`

Local CLI output is for fixture replay, local development, debug, and regression tests. It is not the official monthly source.

## Monthly Cloud Report

Official trigger:

```http
POST /internal/daily-radar/rule-review/monthly
```

```json
{
  "market": "TW",
  "year": 2026,
  "month": 6,
  "min_sample_count": 20
}
```

The API reads persisted production `daily_radar_forward_validation_results` joined to persisted Daily Radar candidate snapshots. It does not read local fixtures and does not reconstruct signal-time reasons from future data.

The response includes:

- `report_json`
- `report_markdown`

The report separates:

- automated recommendation
- human-approved versioned strategy update

The report never changes live scoring, ranking, rule tier, scoring version, or rule version.

## Artifact Delivery

Workflow:

```text
.github/workflows/daily-radar-rule-review.yml
```

Expected artifacts:

```text
reports/daily-radar/monthly/YYYY-MM-rule-review.json
reports/daily-radar/monthly/YYYY-MM-rule-review.md
```

GitHub Actions calls the cloud internal API with `DAILY_RADAR_API_BASE_URL` and `DAILY_RADAR_INTERNAL_TOKEN`, then uploads JSON and Markdown artifacts. The workflow does not access production DB directly.

## Versioning Boundary

Phase 3 adds governance and report surfaces only.

- `SCORING_VERSION` unchanged.
- `RULE_VERSION` unchanged.
- Live ranking behavior unchanged.
- Any future approved scoring change must bump scoring/rule version, update tests/fixtures/spec notes, and include a changelog.
