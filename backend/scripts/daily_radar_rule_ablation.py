#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_FORWARD_WINDOWS,
    build_forward_validation_report,
    default_due_start_date,
    forward_validation_candidates_from_runs,
    forward_validation_fixture_inputs,
    load_price_series_from_raw_data,
    write_report,
)
from ai_stock_sentinel.daily_radar.rule_governance import (
    build_ablation_report,
)
from ai_stock_sentinel.db.session import _get_session_local


def main() -> None:
    args = _parse_args()
    windows = _int_csv(args.windows)
    if args.source == "fixture":
        candidates, prices_by_symbol, benchmark_prices, benchmark_symbol = forward_validation_fixture_inputs(
            fixture_dir=args.fixture_dir,
            run_date=args.run_date,
            market=args.market,
            candidate_limit=args.candidate_limit,
        )
        evaluation = build_forward_validation_report(
            candidates,
            price_series_by_symbol=prices_by_symbol,
            benchmark_prices=benchmark_prices,
            market=args.market,
            sample_source="fixture",
            as_of_date=args.as_of_date,
            windows=windows,
            benchmark_symbol=args.benchmark_symbol or benchmark_symbol,
        )
        sample_source = "fixture"
    else:
        with _get_session_local()() as session:
            start_date = args.start_date or default_due_start_date(args.as_of_date)
            candidates = forward_validation_candidates_from_runs(
                session,
                market=args.market,
                start_date=start_date,
                end_date=args.end_date or args.as_of_date,
            )
            symbols = {str(candidate["symbol"]) for candidate in candidates}
            price_series = load_price_series_from_raw_data(
                session,
                symbols=sorted(symbols | {args.benchmark_symbol}),
                start_date=start_date,
                end_date=args.as_of_date,
            )
            evaluation = build_forward_validation_report(
                candidates,
                price_series_by_symbol={symbol: price_series.get(symbol, []) for symbol in symbols},
                benchmark_prices=price_series.get(args.benchmark_symbol, []),
                market=args.market,
                sample_source="db",
                as_of_date=args.as_of_date,
                windows=windows,
                benchmark_symbol=args.benchmark_symbol,
            )
            sample_source = "db"

    report = build_ablation_report(
        evaluation.outcomes,
        market=args.market,
        sample_source=sample_source,
        min_sample_count=args.min_sample_count,
    )
    if args.output is not None:
        write_report(report, args.output)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic Daily Radar rule-ablation diagnostic report.",
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
    parser.add_argument("--as-of-date", type=_date, default=date.today())
    parser.add_argument("--windows", default=",".join(str(value) for value in DEFAULT_FORWARD_WINDOWS))
    parser.add_argument("--benchmark-symbol", default="TAIEX")
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--min-sample-count", type=int, default=20)
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
