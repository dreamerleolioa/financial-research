# Daily Radar v2 Phase 1D Backtest Calibration Workflow

| Metadata | Value |
| --- | --- |
| Status | Phase 1D implementation note |
| Date | 2026-06-11 |
| Scope | Deterministic calibration report workflow |
| Related requirements | `docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md` |

This phase adds a replayable Daily Radar calibration workflow. It does not change live scoring weights, ranking behavior, bucket rules, relative strength scoring, or Daily Radar request patterns.

## Command

Fixture mode, offline and deterministic:

```bash
cd backend
uv run python scripts/daily_radar_calibration.py \
  --source fixture \
  --run-date 2026-05-29
```

Write a diffable JSON artifact:

```bash
cd backend
uv run python scripts/daily_radar_calibration.py \
  --source fixture \
  --run-date 2026-05-29 \
  --output /tmp/daily-radar-calibration-report.json
```

Read persisted Daily Radar snapshots:

```bash
cd backend
uv run python scripts/daily_radar_calibration.py \
  --source db \
  --market TW \
  --start-date 2026-06-01 \
  --end-date 2026-06-30
```

Optional knobs:

- `--rank-cutoffs 10,20,50,100`
- `--bucket-thresholds 45,55,65`
- `--candidate-limit 100` for fixture replay prefiltering

## Report Shape

The report is stable JSON with sorted keys and no wall-clock timestamp. It includes:

- `sample_count` and `excluded_sample_count`
- `bucket_distribution`
- `rank_cutoff_impact`
- `bucket_threshold_impact`
- `risk_penalty_impact`
- `overheat_impact`
- `relative_strength_impact`
- `skip_reasons`
- `version_manifest`

`version_manifest.live_scoring_changed` is `false` for Phase 1D. The current scoring/rule versions remain:

- `daily-radar-scoring-v2.1c`
- `daily-radar-rules-v2.1c`

## Interpretation Notes

- Rank cutoff impact shows how many candidates and which buckets would be included under each cutoff. It is a calibration diagnostic, not a trading instruction.
- Bucket threshold impact shows how many bucket scores meet each tested threshold and the average number of matched buckets per sample.
- Risk penalty impact summarizes deterministic penalty labels and score adjustments already present in `score_breakdown`.
- Overheat impact is the subset of risk penalty impact for `overextended`.
- Relative strength impact summarizes existing deterministic relative strength trace, including missing or stale cases.
- Skip reasons are explicit so data insufficiency is visible and not silently dropped.

This report does not calculate or advertise win rate, target price, or guaranteed outcomes. Any future live scoring change should be a separate phase with a scoring/rule version bump and updated scoring/service/API tests.
