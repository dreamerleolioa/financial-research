#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ai_stock_sentinel.daily_radar.calibration import (
    DEFAULT_BUCKET_THRESHOLDS,
    DEFAULT_RANK_CUTOFFS,
    build_calibration_report,
    calibration_candidates_from_fixture,
    calibration_candidates_from_runs,
    write_report,
)
from ai_stock_sentinel.db.session import _get_session_local


def main() -> None:
    args = _parse_args()
    if args.source == "fixture":
        candidates = calibration_candidates_from_fixture(
            fixture_dir=args.fixture_dir,
            run_date=args.run_date,
            market=args.market,
            candidate_limit=args.candidate_limit,
        )
    else:
        with _get_session_local()() as session:
            candidates = calibration_candidates_from_runs(
                session,
                market=args.market,
                start_date=args.start_date,
                end_date=args.end_date,
            )

    report = build_calibration_report(
        candidates,
        market=args.market,
        sample_source=args.source,
        rank_cutoffs=_int_csv(args.rank_cutoffs),
        bucket_thresholds=_int_csv(args.bucket_thresholds),
    )
    if args.output is not None:
        write_report(report, args.output)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic Daily Radar calibration report from replayable candidates.",
    )
    parser.add_argument("--source", choices=("fixture", "db"), default="fixture")
    parser.add_argument("--market", default="TW")
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=Path(__file__).parents[1] / "tests" / "fixtures" / "daily_radar",
    )
    parser.add_argument("--run-date", type=_date, default=date(2026, 5, 29))
    parser.add_argument("--start-date", type=_date, default=None)
    parser.add_argument("--end-date", type=_date, default=None)
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--rank-cutoffs", default=",".join(str(value) for value in DEFAULT_RANK_CUTOFFS))
    parser.add_argument("--bucket-thresholds", default=",".join(str(value) for value in DEFAULT_BUCKET_THRESHOLDS))
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD date, got {value!r}") from exc


def _int_csv(value: Any) -> list[int]:
    if isinstance(value, list):
        return [int(item) for item in value]
    return [int(item.strip()) for item in str(value).split(",") if item.strip()]


if __name__ == "__main__":
    main()
