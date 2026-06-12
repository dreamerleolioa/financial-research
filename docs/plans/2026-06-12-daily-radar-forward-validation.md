# Daily Radar Forward Outcome Validation

| Metadata | Value |
| --- | --- |
| Status | Phase 1 implementation note |
| Date | 2026-06-12 |
| Scope | Deterministic Daily Radar forward behavior validation |
| Related plan | `docs/plans/2026-06-11-investment-discipline-execution-plan.md` |

## Purpose

Forward validation measures what happened after a Daily Radar setup appeared. It is a rule-quality and calibration diagnostic for buckets, matched rules, risk labels, market regime, relative strength, repeat status, score decile, and data freshness.

This report is not a user-facing win-rate report, target-price promise, performance advertisement, or trading recommendation. It does not change live scoring, ranking, bucket selection, risk penalties, `SCORING_VERSION`, or `RULE_VERSION`.

## Trigger Surfaces

Local fixture/debug command:

```bash
cd backend
uv run python scripts/daily_radar_forward_validation.py \
  --source fixture \
  --market TW \
  --run-date 2026-05-29 \
  --as-of-date 2026-06-26 \
  --windows 5,10,20 \
  --output /tmp/daily-radar-forward-validation.json
```

DB debug command:

```bash
cd backend
uv run python scripts/daily_radar_forward_validation.py \
  --source db \
  --market TW \
  --start-date 2026-06-01 \
  --end-date 2026-06-30 \
  --as-of-date 2026-07-31 \
  --windows 5,10,20
```

Official production validation should use the cloud backend internal API:

```http
POST /internal/daily-radar/forward-validation/run
```

```json
{
  "mode": "due",
  "market": "TW",
  "as_of_date": "2026-07-31",
  "windows": [5, 10, 20],
  "benchmark_symbol": "TAIEX"
}
```

The local CLI is for fixture replay, local development, debug, and regression tests. Formal validation and later monthly rule-review artifacts must come from the cloud backend using production DB snapshots and production market data.

## Report Shape

The report is stable JSON with sorted keys when written by the CLI. It has no wall-clock timestamp.

Top-level sections:

- `metadata`
- `sample_summary`
- `bucket_outcomes`
- `secondary_bucket_outcomes`
- `rule_outcomes`
- `risk_label_outcomes`
- `market_regime_outcomes`
- `relative_strength_bucket_outcomes`
- `repeat_status_outcomes`
- `score_decile_outcomes`
- `data_freshness_outcomes`
- `ablation_candidates`
- `skip_reasons`
- `version_manifest`

Each outcome aggregation includes:

- `sample_count`
- `average_forward_return_pct`
- `average_excess_return_vs_benchmark_pct`
- `average_max_favorable_excursion_pct`
- `average_max_adverse_excursion_pct`
- `close_below_defense_reference_count`
- `close_below_defense_reference_ratio`
- `hit_rate_above_threshold`
- `profit_factor_like_ratio`

`hit_rate_above_threshold` is diagnostic only. It must not be presented as a public win-rate claim.

## Persistence And Idempotency

The internal API writes `daily_radar_forward_validation_results`. The idempotency key is:

```text
candidate_id + window_days + validation_version
```

Both validated and skipped rows are persisted so missing future price, missing benchmark, stale candidate price, and future signal/date gaps remain auditable.

## Data Boundaries

Validation uses persisted Daily Radar candidate snapshots as the signal-time setup record. It does not reconstruct a historical candidate reason from future data.

Missing data is explicit:

- missing candidate entry price -> `missing_candidate_entry_price`
- insufficient future candidate rows -> `missing_future_price`
- missing benchmark entry or window rows -> `missing_benchmark`
- candidate OHLCV date older than signal date -> `stale_candidate_price`
- signal date after as-of date -> `future_signal_date`

These skip reasons are part of the report and persisted API results.
