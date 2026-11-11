#!/usr/bin/env python3
"""Bounded, explicitly reviewed next-open repair. No implicit apply or commit."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import next_open_history_repair as repair
from ai_stock_sentinel.daily_radar.collection_transactions import read_only_collection
from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar


def read_json(path):
    with Path(path).open('rb') as handle:
        raw = handle.read(repair.MAX_BYTES + 1)
    if len(raw) > repair.MAX_BYTES:
        raise ValueError('repair_input_file_too_large')
    return json.loads(raw)


def main(argv=None, *, provider=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inventory', 'capture', 'plan', 'apply', 'rollback'))
    parser.add_argument('--calendar')
    parser.add_argument('--request')
    parser.add_argument('--evidence')
    parser.add_argument('--manifest')
    parser.add_argument('--apply-sha256')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if not 0 < args.limit <= repair.MAX_RESULTS or args.offset < 0:
        parser.error('offset >= 0 and 1 <= limit <= 250 required')
    writing = args.command in ('apply', 'rollback')
    if writing and (not args.manifest or not args.apply_sha256):
        parser.error('apply/rollback require --manifest and --apply-sha256')
    if not writing and not args.calendar:
        parser.error('--calendar required')
    if args.command in ('capture', 'plan') and not args.request:
        parser.error('--request required')
    if args.command == 'plan' and not args.evidence:
        parser.error('--evidence required')
    calendar = None if writing else TradingCalendar.parse(read_json(args.calendar))
    request = read_json(args.request) if args.command in ('capture', 'plan') else None
    if request is not None:
        request = request | {'results': request['results'][args.offset:args.offset + args.limit]}
    # Reserve the output before any apply; never overwrite a reviewed artifact.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    engine = None
    try:
        engine = create_engine(os.environ['DATABASE_URL'], hide_parameters=True)
        if args.command == 'inventory':
            with Session(engine, autoflush=False) as session, read_only_collection(session):
                payload = repair.inventory(session, calendar=calendar)
        elif args.command == 'capture':
            payload = repair.capture_evidence(engine, request, calendar=calendar, provider=provider)
        elif args.command == 'plan':
            payload = repair.build_repair_plan(engine, request, calendar=calendar, evidence=read_json(args.evidence))
        else:
            manifest = read_json(args.manifest)
            with Session(engine) as session, session.begin():
                payload = repair.apply_repair_plan(session, manifest, expected_sha256=args.apply_sha256,
                                                  rollback=args.command == 'rollback')
            payload |= {'manifest_sha256': args.apply_sha256, 'command': args.command}
        raw = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
        if len(raw) > repair.MAX_BYTES:
            raise ValueError('repair_output_file_too_large')
        with os.fdopen(descriptor, 'wb') as handle:
            descriptor = None
            handle.write(raw)
        return payload
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        Path(args.output).unlink(missing_ok=True)
        raise
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    main()
