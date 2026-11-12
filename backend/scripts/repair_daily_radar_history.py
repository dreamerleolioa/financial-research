#!/usr/bin/env python3
"""Inventory/capture/plan/apply/rollback bounded Daily Radar history repairs.

No command silently applies a plan. apply/rollback require its separately supplied
SHA256. Network capture is separate from DB transactions and never writes to DB.
"""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.history_repair import (
    VERSION, MAX_RESULTS, fingerprint, build_repair_plan, apply_repair_plan, diagnostic_origin_changed,
)
from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar, OfficialTradingCalendarProvider
from ai_stock_sentinel.daily_radar.forward_validation import (
    FORWARD_VALIDATION_VERSION, forward_validation_candidates_from_runs,
)
from ai_stock_sentinel.daily_radar.observation_validation import (
    OBSERVATION_DIAGNOSTIC_VERSION, load_observation_origins, strategy_cohort,
)
from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult


def read_json(path):
    p = Path(path)
    if p.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("repair_input_file_too_large")
    return json.loads(p.read_text())


def write_new(path, payload):
    # A preimage/rollback manifest must never be overwritten by a rerun.
    with Path(path).open("x") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def inventory(session, calendar):
    from ai_stock_sentinel.daily_radar.validation_inputs import validation_inputs_match
    from ai_stock_sentinel.daily_radar.window_identity import window_error
    candidates = forward_validation_candidates_from_runs(session, market="TW", end_date=calendar.through)
    closed_ids = list(session.scalars(select(DailyRadarRun.id).where(DailyRadarRun.market=="TW",
        DailyRadarRun.run_date.between(calendar.start,calendar.through),
        ~DailyRadarRun.run_date.in_(calendar.sessions), DailyRadarRun.status.in_(("completed","stale_data")))))
    closed_dates = {session.get(DailyRadarRun,i).run_date for i in closed_ids}
    candidates = [c for c in candidates if date.fromisoformat(c["record_date"]) in calendar.sessions]
    origins = load_observation_origins(session,candidates,market="TW",through_date=calendar.through,excluded_dates=closed_dates)
    by_id = {c["candidate_id"]:c for c in candidates}
    rows = session.scalars(select(DailyRadarForwardValidationResult).where(
        DailyRadarForwardValidationResult.candidate_id.in_(by_id),
        DailyRadarForwardValidationResult.validation_version==FORWARD_VALIDATION_VERSION,
        DailyRadarForwardValidationResult.benchmark_symbol=="TAIEX",
        DailyRadarForwardValidationResult.window_days.in_((5,10,20,40,60)),
        DailyRadarForwardValidationResult.status.in_(("validated","skipped"))).order_by(DailyRadarForwardValidationResult.id))
    targets = []
    for row in rows:
        if row.evaluation_as_of_date and row.evaluation_as_of_date>calendar.through:
            continue
        c=by_id[row.candidate_id]
        c=c | {'observation_origin': origins.get((c['symbol'],strategy_cohort(c)))}
        days=[d for d in calendar.sessions if d>row.signal_date][:row.window_days]
        if len(days)!=row.window_days:
            continue
        if row.status == "skipped" and not row.outcome.get("historical_repair"):
            continue
        unverified = not validation_inputs_match(c, {'status': row.status,
            'skip_reason': row.skip_reason, 'outcome': row.outcome})
        if (row.target_date!=days[-1] or row.outcome.get("target_date",str(days[-1]))!=str(days[-1])
                or unverified or row.status == 'validated' and window_error({'outcome': row.outcome},
                    signal=row.signal_date, sessions=days, basis='signal_close')):
            targets.append({"id":row.id,"operation":"recompute"})
        else:
            origin=origins.get((c["symbol"],strategy_cohort(c))) or {}
            diagnostic = row.outcome.get("observation_diagnostic") or {}
            current = diagnostic.get("version") == OBSERVATION_DIAGNOSTIC_VERSION
            changed = current and diagnostic_origin_changed(c,diagnostic,origin)
            if (c["selection_status"]=="selected"
                    and (origin.get("candidate_id")==c["candidate_id"] or changed)
                    and (not current or changed
                         or diagnostic.get("status")=="insufficient_data")):
                targets.append({"id":row.id,"operation":"diagnostic"})
    return {"version":VERSION,"as_of_date":str(calendar.through),"calendar_sha256":fingerprint(calendar.payload()),
            "invalid_run_ids":closed_ids,"results":targets,
            "result_count":len(targets),"max_results_per_plan":MAX_RESULTS}


def capture_prices(request, identities, *, provider=None):
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    provider=provider or YFinanceForwardPriceProvider()
    ids=[int(r["id"]) for r in request["results"]]
    if not ids or len(ids)>MAX_RESULTS or len(set(ids))!=len(ids):
        raise ValueError("capture_requires_bounded_allowlist")
    cutoff=date.fromisoformat(request["as_of_date"])
    earliest=min(day for _,day in identities.values())
    symbols=sorted({symbol for symbol,_ in identities.values()} | {"TAIEX"})
    prices={};failures=[]
    for offset in range(0,len(symbols),32):
        batch=symbols[offset:offset+32]
        try:
            prices.update(provider.fetch(batch,start_date=earliest,end_date=cutoff))
        except Exception as exc:
            failures.append({"symbols":batch,"reason":"price_provider_failed","error_type":type(exc).__name__})
    failed_symbols={symbol for failure in failures for symbol in failure["symbols"]}
    captured=datetime.now(timezone.utc).isoformat()
    evidence={}
    for result_id in ids:
        symbol,_=identities[result_id]
        payload={"prices":prices.get(symbol,[]),"benchmark":prices.get("TAIEX",[])}
        evidence[str(result_id)]={**payload,"source_status":("provider_failed" if symbol in failed_symbols or "TAIEX" in failed_symbols else "captured"),"source":{"provider":"yfinance_adjusted_history",
            "price_basis":"signal_snapshot_adjusted","captured_at":captured,"sha256":fingerprint(payload)}}
    return {"evidence":evidence,"failures":failures,"as_of_date":str(cutoff)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("calendar","inventory","capture","plan","apply","rollback"))
    parser.add_argument("--calendar")
    parser.add_argument("--start-date",type=date.fromisoformat)
    parser.add_argument("--as-of-date",type=date.fromisoformat)
    parser.add_argument("--request")
    parser.add_argument("--evidence")
    parser.add_argument("--manifest")
    parser.add_argument("--offset",type=int,default=0,help="Offset in a frozen inventory request")
    parser.add_argument("--limit",type=int,default=MAX_RESULTS,help="At most 250 result rows per plan")
    parser.add_argument("--only-invalid-runs",action="store_true",help="Prepare quarantine/calendar before result batches")
    parser.add_argument("--apply-sha256",help="Explicitly authorize this exact reviewed manifest")
    parser.add_argument("--output",required=True)
    args=parser.parse_args(argv)
    if args.offset<0 or not 1<=args.limit<=MAX_RESULTS:
        parser.error("offset/limit outside the bounded repair contract")
    if Path(args.output).exists():
        parser.error("output already exists; preserve previous evidence")
    if args.command=="calendar":
        if not args.start_date or not args.as_of_date or args.start_date>args.as_of_date:
            parser.error("calendar requires ordered --start-date and --as-of-date")
        write_new(args.output,OfficialTradingCalendarProvider().fetch(start_date=args.start_date,end_date=args.as_of_date).payload())
        return 0
    if args.command in ("apply","rollback") and (not args.manifest or not args.apply_sha256):
        parser.error("apply/rollback require --manifest and --apply-sha256")
    if args.command in ("inventory","plan") and not args.calendar:
        parser.error("inventory/plan require --calendar")
    if args.command in ("capture","plan") and not args.request:
        parser.error("capture/plan require --request")
    url=os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required")
    engine=create_engine(url)
    try:
        if args.command in ("apply","rollback"):
            plan=read_json(args.manifest)
            # Reserve the receipt before committing. The manifest already holds
            # complete preimages; interrupted receipt writing is recoverable by
            # idempotent replay/readback of the same manifest.
            with Path(args.output).open("x") as receipt:
                with Session(engine) as session, session.begin():
                    if engine.dialect.name=="postgresql":
                        from sqlalchemy import text
                        session.execute(text("SET LOCAL lock_timeout = '5s'"))
                        session.execute(text("SET LOCAL statement_timeout = '30s'"))
                    result=apply_repair_plan(session,plan,expected_sha256=args.apply_sha256,rollback=args.command=="rollback")
                json.dump({"manifest_sha256":args.apply_sha256,"command":args.command,
                           "as_of_date":plan["calendar"]["covered_through"],**result},receipt)
                receipt.write("\n")
            return 0
        calendar=TradingCalendar.parse(read_json(args.calendar)) if args.calendar else None
        with Session(engine) as session:
            if engine.dialect.name=="postgresql":
                from sqlalchemy import text
                session.execute(text("SET TRANSACTION READ ONLY"))
            if args.command=="inventory":
                result=inventory(session,calendar)
            else:
                request=read_json(args.request)
                request = dict(request) | {"results": ([] if args.only_invalid_runs else request["results"][args.offset:args.offset+args.limit])}
                if calendar is not None and (request.get("as_of_date")!=str(calendar.through)
                        or request.get("calendar_sha256")!=fingerprint(calendar.payload())):
                    raise ValueError("request_calendar_identity_mismatch")
                if args.command=="plan":
                    captured=read_json(args.evidence) if args.evidence else {}
                    result=build_repair_plan(session,request,calendar=calendar,evidence=captured.get("evidence",{}))
                else:
                    ids=[int(r["id"]) for r in request["results"]]
                    identities={}
                    for result_id in ids:
                        row=session.get(DailyRadarForwardValidationResult,result_id)
                        if row is None or row.validation_version!=FORWARD_VALIDATION_VERSION:
                            raise ValueError("capture_result_identity_invalid")
                        c=session.get(DailyRadarCandidate,row.candidate_id)
                        identities[result_id]=(c.symbol,row.signal_date)
            session.rollback()
        if args.command=="capture":
            result=capture_prices(request,identities)
        write_new(args.output,result)
        print(json.dumps({"command":args.command,"output":str(Path(args.output).resolve()),
                          "sha256":result.get("sha256"),"result_count":len(result.get("results",[]))}))
        return 0
    finally:
        engine.dispose()


if __name__=="__main__":
    raise SystemExit(main())
