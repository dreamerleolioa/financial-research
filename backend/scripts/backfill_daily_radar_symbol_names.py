#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ai_stock_sentinel.daily_radar.name_backfill import backfill_daily_radar_symbol_names
from ai_stock_sentinel.db.session import _get_session_local


def main() -> None:
    args = _parse_args()
    with _get_session_local()() as session:
        result = backfill_daily_radar_symbol_names(
            session,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill persisted Daily Radar display names for rows stored as symbol codes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum Daily Radar candidate rows to scan in this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and report pending updates without writing to the database.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
