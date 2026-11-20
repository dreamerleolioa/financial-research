"""Replay a reviewed historical manifest against the identified local restore."""

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from process_daily_radar_backlog import load, local_engine, reserve_validation_commit

from ai_stock_sentinel.daily_radar import publication_close_manifest, reviewed_reduction_window
from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-socket", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--run-date", required=True, type=date.fromisoformat)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--exchange-capture", required=True, action="append", type=Path)
    parser.add_argument("--vendor-history", type=Path)
    parser.add_argument("--benchmark-history", type=Path)
    parser.add_argument("--split-chart", type=Path, help="Gzip JSON pair [raw chart, receipt]")
    parser.add_argument("--mode", choices=["verify", "apply", "rollback"], default="verify")
    args = parser.parse_args()
    plan = load(args.manifest)
    calendar = TradingCalendar.parse(plan["calendar"])
    if calendar.through != args.run_date:
        parser.error("manifest and restore cutoff differ")
    captures = [load(f) for f in args.exchange_capture]
    if plan["version"] == publication_close_manifest.VERSION and not (
        args.vendor_history or args.benchmark_history or args.split_chart
    ):
        method = publication_close_manifest.apply
        evidence = {"captures": {c["symbol"]: c for c in captures}}
        if len(captures) != len(evidence["captures"]):
            parser.error("duplicate symbol in exchange captures")
    elif (
        plan["version"] == reviewed_reduction_window.VERSION
        and len(captures) == 1
        and args.vendor_history
        and args.benchmark_history
        and args.split_chart
    ):
        method = reviewed_reduction_window.apply
        evidence = {
            "capture": captures[0],
            "archive": load(args.vendor_history),
            "benchmark": load(args.benchmark_history),
            "chart": load(args.split_chart),
        }
    else:
        parser.error("manifest version or evidence files do not match")
    engine = local_engine(args.local_socket, args.port, args.database, args.user, args.run_date)
    try:
        with Session(engine) as session:
            reserve_validation_commit(session, freeze_candidates=True)
            result = method(
                session,
                plan,
                expected_sha256=args.expected_sha256,
                calendar=calendar,
                rollback=args.mode == "rollback",
                **evidence,
            )
            if args.mode == "verify":
                session.rollback()
            else:
                session.commit()
        print({"mode": args.mode, **result})
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
