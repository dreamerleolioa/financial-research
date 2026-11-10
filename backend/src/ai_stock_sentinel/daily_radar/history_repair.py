"""Reviewable, bounded historical repairs. No provider I/O or implicit commit.

A dry-run freezes identities, inputs, preimages and postimages. Apply/rollback
lock in stable order, validate every precondition before any mutation, and require
the reviewed manifest hash. Original candidates/raw prices are never rewritten.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import math

from sqlalchemy import select, text

from ai_stock_sentinel.calibration.forward_validation import evaluate_forward_window
from ai_stock_sentinel.daily_radar.forward_validation import DAILY_RADAR_FORWARD_ADAPTER, FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.observation_validation import (
    evaluate_observation, load_observation_origins, strategy_cohort, OBSERVATION_DIAGNOSTIC_VERSION, canonical_radar_run_ids,
)
from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar, CALENDAR_STEP, SESSION_STEP
from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult, DailyRadarPreparedRun

VERSION = "daily-radar-history-repair-v1"
MAX_RESULTS = 250
RESULT_FIELDS = ("candidate_id", "window_days", "validation_version", "signal_date", "target_date",
                 "benchmark_symbol", "evaluation_as_of_date", "status", "skip_reason", "outcome")
MUTABLE_FIELDS = ("target_date", "evaluation_as_of_date", "status", "skip_reason", "outcome")


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def result_snapshot(row):
    return {field: (str(value) if isinstance(value, date) else deepcopy(value))
            for field in RESULT_FIELDS for value in [getattr(row, field)]}


def candidate_snapshot(c, run):
    return {"candidate_id": c.id, "run_id": c.run_id, "market": run.market,
            "symbol": c.symbol, "record_date": str(run.run_date),
            "input_snapshot": deepcopy(c.input_snapshot or {}), "data_dates": deepcopy(c.data_dates or {}),
            "selection_status": c.selection_status, "shadow_cohort": c.shadow_cohort}


def run_snapshot(row):
    return {"run_date": str(row.run_date), "market": row.market, "status": row.status,
            "errors": deepcopy(row.errors)}


def _verified_prices(evidence, c, target):
    """An explicit captured signal anchor must match the immutable snapshot units.

    Current adjusted Yahoo prices cannot be spliced onto older signal snapshots
    after a corporate action. A basis mismatch remains missing evidence.
    """
    if not evidence:
        return [], [], "historical_price_evidence_missing"
    if evidence.get("source_status") == "provider_failed":
        return [], [], "historical_price_provider_failed"
    rows = evidence.get("prices", [])
    benchmark = evidence.get("benchmark", [])
    proof = evidence.get("source") or {}
    if (proof.get("price_basis") != "signal_snapshot_adjusted" or not proof.get("provider")
            or not proof.get("captured_at") or proof.get("sha256") != fingerprint({"prices": rows, "benchmark": benchmark})):
        return [], [], "historical_price_provenance_invalid"
    signal = c["record_date"]
    anchors = [r for r in rows if r.get("date") == signal]
    snapshot_close = (c["input_snapshot"].get("ohlcv") or {}).get("close")
    if not anchors:
        return [], [], "historical_price_anchor_missing"
    try:
        close = float(snapshot_close)
        if (len(anchors) != 1 or not math.isfinite(close) or close <= 0
                or not math.isclose(float(anchors[0]["close"]), close, rel_tol=1e-8, abs_tol=1e-8)):
            return [], [], "historical_price_basis_mismatch"
    except (KeyError, ValueError, TypeError, OverflowError):
        return [], [], "historical_price_basis_mismatch"
    # Restrict the evidence to the original forward window. Future observations
    # cannot participate in reconstructed diagnostics or price extrema.
    return ([r for r in rows if signal <= r.get("date", "") <= str(target)],
            [r for r in benchmark if signal <= r.get("date", "") <= str(target)], None)


def diagnostic_origin_changed(candidate, diagnostic, origin):
    if not origin or diagnostic.get("first_seen_date") != origin.get("first_seen_date") or diagnostic.get("origin_scope") != origin.get("scope"):
        return True
    first = (origin.get("candidate_id") == candidate["candidate_id"]
             and origin.get("first_seen_date") == candidate["record_date"])
    return (diagnostic.get("status") == "not_first_observation") == first


def build_repair_plan(session, request, *, calendar: TradingCalendar, evidence):
    calendar = TradingCalendar.parse(calendar.payload())
    requested = request.get("results", [])
    run_ids = request.get("invalid_run_ids", [])
    ids = [int(r["id"]) for r in requested]
    if len(ids) > MAX_RESULTS or len(set(ids)) != len(ids) or len(run_ids) > 20:
        raise ValueError("repair_allowlist_invalid_or_too_large")
    invalid_dates = set()
    for run_id in run_ids:
        run = session.get(DailyRadarRun, int(run_id))
        if (run is None or run.market != "TW" or not calendar.start <= run.run_date <= calendar.through
                or run.run_date in calendar.sessions):
            raise ValueError("invalid_run_requires_official_closed_date")
        invalid_dates.add(run.run_date)
    runs = list(session.scalars(select(DailyRadarRun).where(
        DailyRadarRun.market == "TW", DailyRadarRun.run_date.in_(invalid_dates)).order_by(DailyRadarRun.id))) if invalid_dates else []
    if len(runs) > 20:
        raise ValueError("repair_run_allowlist_too_large")
    prepared = session.scalar(select(DailyRadarPreparedRun).where(
        DailyRadarPreparedRun.market == "TW", DailyRadarPreparedRun.run_date == calendar.through))
    if prepared is None:
        raise ValueError("prepared_run_required_for_calendar")
    canonical_ids = set(session.scalars(canonical_radar_run_ids(market="TW", end_date=calendar.through)))
    loaded = []
    for item in requested:
        row = session.get(DailyRadarForwardValidationResult, int(item["id"]))
        if row is None or row.validation_version != FORWARD_VALIDATION_VERSION or row.benchmark_symbol != "TAIEX":
            raise ValueError("repair_result_identity_invalid")
        c = session.get(DailyRadarCandidate, row.candidate_id)
        run = session.get(DailyRadarRun, c.run_id)
        if (run.id not in canonical_ids or run.market != "TW" or row.signal_date != run.run_date or row.signal_date in invalid_dates
                or row.signal_date not in calendar.sessions or row.window_days not in (5,10,20,40,60)
                or row.evaluation_as_of_date is not None and row.evaluation_as_of_date > calendar.through):
            raise ValueError("repair_result_scope_invalid")
        loaded.append((item, row, candidate_snapshot(c, run)))
    origins = load_observation_origins(session, [c for _,_,c in loaded], market="TW",
                                       through_date=calendar.through, excluded_dates=invalid_dates)
    # Only a complete recomputation may certify return inputs. Diagnostic-only
    # repair preserves the original return and its existing (or absent) proof.
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.validation_inputs import (
        bind_validation_input, validation_candidate_revision, validation_inputs_match,
    )
    from ai_stock_sentinel.daily_radar.window_identity import bind_window, window_error
    input_ids = [row.candidate_id for _, row, _ in loaded]
    evaluation_inputs = {c['candidate_id']: c | {'observation_origin':
        origins.get((c['symbol'], strategy_cohort(c)))} for c in forward_validation_candidates_from_runs(
            session, market='TW', end_date=calendar.through, candidate_ids=input_ids)} if input_ids else {}
    if set(input_ids) != set(evaluation_inputs):
        raise ValueError('repair_candidate_input_unavailable')
    revisions = {cid: validation_candidate_revision(c) for cid, c in evaluation_inputs.items()}
    seed = {"version": VERSION, "calendar": calendar.payload(),
            "results": [{"id": row.id, "operation": item["operation"], "before": result_snapshot(row),
                         "candidate_sha256": fingerprint(c),
                         "validation_candidate_revision": revisions[row.candidate_id],
                         "observation_origin": origins.get((c["symbol"],strategy_cohort(c)))} for item,row,c in loaded],
            "runs": [{"id": run.id, "before": run_snapshot(run)} for run in runs]}
    repair_id = fingerprint(seed)
    plan = {"version": VERSION, "repair_id": repair_id, "calendar": calendar.payload(), "results": [], "runs": []}
    markers = [{"date": str(d), "close": 1} for d in calendar.sessions]
    for item, row, c in loaded:
        operation = item["operation"]
        if operation not in ("diagnostic", "recompute"):
            raise ValueError("repair_operation_invalid")
        before = result_snapshot(row)
        days = [d for d in calendar.sessions if d > row.signal_date][:row.window_days]
        if len(days) != row.window_days:
            raise ValueError("repair_window_not_mature")
        target = days[-1]
        if operation == "diagnostic" and (row.target_date != target or before["outcome"].get("target_date", str(target)) != str(target)):
            raise ValueError("repair_calendar_mismatch_requires_recompute")
        existing_diag = before["outcome"].get("observation_diagnostic") or {}
        origin = origins.get((c["symbol"], strategy_cohort(c)))
        origin_changed = diagnostic_origin_changed(c, existing_diag, origin)
        if operation == "diagnostic" and existing_diag.get("version") == OBSERVATION_DIAGNOSTIC_VERSION:
            if not origin_changed and (existing_diag.get("status") != "insufficient_data" or not evidence.get(str(row.id))):
                continue
        if operation == "diagnostic" and row.status != "validated":
            raise ValueError("diagnostic_requires_validated_result")
        prices, benchmark, price_reason = _verified_prices(evidence.get(str(row.id)), c, target)
        c["observation_origin"] = origin
        if operation == "recompute":
            if (row.status == "validated" and row.target_date == target
                    and before["outcome"].get("target_date",str(target)) == str(target)
                    and validation_inputs_match(evaluation_inputs[row.candidate_id], before)
                    and window_error(
                        before, signal=row.signal_date, sessions=days, basis='signal_close') is None):
                raise ValueError("recompute_requires_historical_mismatch")
            # Avoid using valid-looking but inconsistent OHLC or ambiguous prices.
            from ai_stock_sentinel.calibration.forward_validation import price_conflict_fields, number
            seen = {}
            for bar in prices:
                day = bar.get("date")
                low, close, high = (number(bar.get(k)) for k in ("low", "close", "high"))
                if (low is None or close is None or high is None or not 0 < low <= close <= high
                        or price_conflict_fields(seen.get(day, {}), bar)):
                    price_reason = "historical_price_conflict_or_invalid_ohlc"
                seen[day] = bar
            benchmark_seen = {}
            for bar in benchmark:
                day = bar.get("date")
                low, close, high = (number(bar.get(k)) for k in ("low","close","high"))
                if (low is None or close is None or high is None or not 0 < low <= close <= high
                        or price_conflict_fields(benchmark_seen.get(day, {}), bar)):
                    price_reason = "historical_benchmark_conflict_or_invalid_ohlc"
                benchmark_seen[day] = bar
            result = evaluate_forward_window(c, price_series=prices, benchmark_prices=benchmark,
                adapter=DAILY_RADAR_FORWARD_ADAPTER, window_days=row.window_days,
                as_of_date=calendar.through, benchmark_symbol="TAIEX", validation_version=FORWARD_VALIDATION_VERSION,
                hit_threshold_pct=0, trading_calendar=calendar.sessions)
            if price_reason:
                result = {"status": "skipped", "target_date": None, "skip_reason": price_reason,
                          "outcome": {"expected_target_date": str(target)}}
            after = before | {"status": result["status"], "target_date": result.get("target_date"),
                "evaluation_as_of_date": str(calendar.through), "skip_reason": result.get("skip_reason"),
                "outcome": result["outcome"]}
        else:
            after = deepcopy(before)
        if after["status"] == "validated":
            diagnostic = evaluate_observation(c, price_series=prices, benchmark_prices=markers,
                                              window_days=row.window_days, as_of_date=calendar.through)
            if price_reason and diagnostic["status"] not in ("already_broken_out", "already_invalidated", "not_first_observation"):
                diagnostic = diagnostic | {"status": "insufficient_data", "missing_reason": price_reason}
            after["outcome"]["observation_diagnostic"] = diagnostic
        after["outcome"]["historical_repair"] = {"repair_id": repair_id, "operation": operation,
            "as_of_date": str(calendar.through), "before_sha256": fingerprint(before),
            "observation_origin": deepcopy(origin),
            "calendar_sha256": fingerprint(calendar.payload()),
            "price_evidence_sha256": fingerprint(evidence.get(str(row.id)))}
        if operation == 'recompute':
            after = bind_window(after, calendar.sessions, 'signal_close')
            after['outcome'] = bind_validation_input(evaluation_inputs[row.candidate_id], after)['outcome']
        plan["results"].append({"id": row.id, "operation": operation, "before": before, "after": after,
                                "observation_origin": deepcopy(origin),
                                "validation_candidate_revision": revisions[row.candidate_id],
                                "candidate_sha256": fingerprint(candidate_snapshot(session.get(DailyRadarCandidate, row.candidate_id),
                                    session.get(DailyRadarRun, session.get(DailyRadarCandidate, row.candidate_id).run_id)))})
    for run in runs:
        before = run_snapshot(run)
        if before["status"] == "invalidated":
            continue
        after = deepcopy(before)
        after["status"] = "invalidated"
        after["errors"] = list(after["errors"] or [])
        after["errors"].append({"code": "non_trading_signal_date", "repair_id": repair_id,
            "original_status": before["status"], "calendar_sha256": fingerprint(calendar.payload())})
        plan["runs"].append({"id": run.id, "before": before, "after": after})
    step_keys = [CALENDAR_STEP]
    step_after = {CALENDAR_STEP: calendar.payload()}
    if calendar.through in calendar.sessions:
        step_keys.append(SESSION_STEP)
        step_after[SESSION_STEP] = {"status": "open", "run_date": str(calendar.through), "market": "TW",
                                  "provider": "twse", "dataset": "MI_5MINS_HIST"}
    plan["prepared"] = {"id": prepared.id, "run_date": str(prepared.run_date),
        "before": {k: deepcopy((prepared.step_statuses or {}).get(k)) for k in step_keys}, "after": step_after}
    # Shared metadata has one separate, reviewed manifest. Result batches must
    # not each claim its original preimage, which would make their rollbacks
    # mutually dependent when planned from the same database snapshot.
    if plan["results"] and plan["prepared"]["before"] != step_after:
        raise ValueError("repair_calendar_phase_required")
    for entry in plan['results']:
        _verify_repair_marker(plan, entry)
    plan["sha256"] = fingerprint(plan)
    return plan


def apply_repair_plan(session, plan, *, expected_sha256, rollback=False):
    return _apply_result_manifest(session, plan, expected_sha256=expected_sha256, rollback=rollback,
                                  manifest_version=VERSION, result_version=FORWARD_VALIDATION_VERSION)


def _storage_fingerprint(value):
    """JSONB normalizes numeric spelling/sign of zero, but not JSON types.

    Tag every node so user strings/objects cannot collide with numeric tags.
    Decimal tuples avoid rounding large integers under the ambient context.
    Source/capture hashes keep their original canonical representation.
    """
    def encode(item):
        if item is None: return ['null']
        if type(item) is bool: return ['bool', item]
        if type(item) in (int, float):
            number = Decimal(str(item))
            if not number.is_finite(): raise ValueError('repair_snapshot_json_invalid')
            sign, digits, exponent = number.as_tuple(); digits = list(digits)
            if not any(digits): return ['number', 0, [0], 0]
            while len(digits) > 1 and digits[-1] == 0:
                digits.pop(); exponent += 1
            return ['number', sign, digits, exponent]
        if isinstance(item, str): return ['string', item]
        if isinstance(item, (list, tuple)): return ['array', [encode(v) for v in item]]
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            return ['object', [[k, encode(item[k])] for k in sorted(item)]]
        raise ValueError('repair_snapshot_json_invalid')
    return fingerprint(encode(value))


def _verify_repair_marker(plan, entry):
    """Bind ownership to the same preimage and sources as the reviewed manifest."""
    def digest(value):
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
    marker = entry['after']['outcome'].get('historical_repair')
    if not isinstance(marker, dict) or not digest(plan.get('repair_id')) or entry['operation'] not in ('recompute', 'diagnostic'):
        raise ValueError('repair_marker_invalid')
    expected = {'repair_id': plan['repair_id'], 'operation': entry['operation'],
        'before_sha256': fingerprint(entry['before']), 'calendar_sha256': fingerprint(plan['calendar'])}
    if plan['version'] == 'daily-radar-next-open-repair-v2':
        if entry['operation'] != 'recompute' or not digest(plan.get('capture_sha256')):
            raise ValueError('repair_marker_invalid')
        expected.update(version=plan['version'], capture_sha256=plan['capture_sha256'],
                        prior_evidence=entry['before']['outcome'])
    else:
        expected.update(as_of_date=plan['calendar']['covered_through'], observation_origin=entry['observation_origin'])
        if not digest(marker.get('price_evidence_sha256')):
            raise ValueError('repair_marker_invalid')
    if (any(key not in marker for key in expected)
            or fingerprint({key: marker[key] for key in expected}) != fingerprint(expected)):
        raise ValueError('repair_marker_invalid')


def _apply_result_manifest(session, plan, *, expected_sha256, rollback, manifest_version,
                           result_version, verify_after=None, verify_context=None):
    if (plan.get("version") != manifest_version or plan.get("sha256") != expected_sha256
            or fingerprint({k:v for k,v in plan.items() if k != "sha256"}) != expected_sha256):
        raise ValueError("repair_manifest_hash_mismatch")
    TradingCalendar.parse(plan["calendar"])
    if len(plan["results"]) > MAX_RESULTS or len(plan["runs"]) > 20:
        raise ValueError("repair_allowlist_too_large")
    if not rollback:
        for entry in plan['results']:
            _verify_repair_marker(plan, entry)
    # Legacy applied snapshots can rollback without new marker requirements;
    # every current row must still match its exact frozen pre/postimage below.
    # Row locks cannot prevent a new public revision (or a candidate change)
    # from changing the first observation between the origin read and commit.
    # Reserve the publication relations before any origin/canonical reads.
    # Plain public SELECTs remain available while the bounded repair commits.
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))
        session.execute(text("LOCK TABLE daily_radar_runs, daily_radar_candidates IN SHARE ROW EXCLUSIVE MODE"))
    elif dialect == "sqlite":
        # SQLite's writer reservation is database-wide; this updates no rows
        # but blocks a distinct connection from publishing before our commit.
        session.execute(text("UPDATE daily_radar_runs SET id = id WHERE 0"))
    else:
        raise ValueError("repair_database_dialect_unsupported")
    # Match owning validation lock order, then prepared metadata. No external I/O.
    locked = []
    for table, kind, snapshot in ((DailyRadarRun,"runs",run_snapshot),
                                   (DailyRadarForwardValidationResult,"results",result_snapshot)):
        ids = [entry["id"] for entry in plan[kind]]
        if len(set(ids)) != len(ids):
            raise ValueError("repair_duplicate_identity")
        order = ((table.candidate_id, table.window_days, table.validation_version, table.id)
                 if kind == "results" else (table.id,))
        rows = {row.id:row for row in session.scalars(select(table).where(table.id.in_(ids)).order_by(*order)
            .with_for_update().execution_options(populate_existing=True))} if ids else {}
        if kind == "results" and rows:
            list(session.scalars(select(DailyRadarCandidate).where(DailyRadarCandidate.id.in_(
                [r.candidate_id for r in rows.values()])).order_by(DailyRadarCandidate.id)
                .with_for_update().execution_options(populate_existing=True)))
            contexts = {r.candidate_id: candidate_snapshot(session.get(DailyRadarCandidate,r.candidate_id),
                session.get(DailyRadarRun,session.get(DailyRadarCandidate,r.candidate_id).run_id)) for r in rows.values()}
            cutoff = date.fromisoformat(plan["calendar"]["covered_through"])
            excluded = {date.fromisoformat(r["before"]["run_date"]) for r in plan["runs"]}
            current_origins = load_observation_origins(session,list(contexts.values()),market="TW",
                                                       through_date=cutoff,excluded_dates=excluded)
            current_public = set(session.scalars(canonical_radar_run_ids(market="TW",end_date=cutoff)))
            from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
            from ai_stock_sentinel.daily_radar.validation_inputs import (
                validation_candidate_revision, PROOF_KEY, VERSION as INPUT_VERSION,
            )
            current_inputs = {c['candidate_id']: c | {'observation_origin':
                current_origins.get((c['symbol'], strategy_cohort(c)))}
                for c in forward_validation_candidates_from_runs(session, market='TW',
                    end_date=cutoff, candidate_ids=list(contexts))}
        for entry in plan[kind]:
            row = rows.get(entry["id"])
            if row is None:
                raise ValueError("repair_row_changed_or_missing")
            for key in (set(entry["before"]) - set(MUTABLE_FIELDS) if kind == "results" else ("run_date","market")):
                if entry["before"][key] != entry["after"][key]:
                    raise ValueError("repair_immutable_identity_changed")
            if kind == "results":
                if row.validation_version != result_version:
                    raise ValueError("repair_result_identity_invalid")
                c = session.get(DailyRadarCandidate, row.candidate_id)
                run = session.get(DailyRadarRun, c.run_id)
                if fingerprint(candidate_snapshot(c,run)) != entry["candidate_sha256"]:
                    raise ValueError("repair_candidate_changed")
                context = contexts[row.candidate_id]
                if (run.id not in current_public or current_origins.get((context["symbol"],strategy_cohort(context)))
                        != entry["observation_origin"]):
                    raise ValueError("repair_observation_origin_changed")
                expected_revision = entry.get('validation_candidate_revision')
                proof = entry['after']['outcome'].get(PROOF_KEY)
                if expected_revision is not None:
                    current_input = current_inputs.get(row.candidate_id)
                    if current_input is None or validation_candidate_revision(current_input) != expected_revision:
                        raise ValueError('repair_validation_inputs_changed')
                    if entry['operation'] == 'recompute' and (not isinstance(proof, dict)
                            or proof.get('version') != INPUT_VERSION
                            or proof.get('candidate_revision') != expected_revision):
                        raise ValueError('repair_validation_proof_invalid')
                elif entry['operation'] == 'recompute' and proof is not None:
                    raise ValueError('repair_validation_proof_missing_revision')
                if verify_after is not None:
                    verify_after(current_inputs[row.candidate_id], entry['after'])
                if entry["operation"] == "diagnostic":
                    def returns(payload):
                        return {k:v for k,v in payload.items() if k not in ("observation_diagnostic","historical_repair")}
                    if returns(entry["before"]["outcome"]) != returns(entry["after"]["outcome"]):
                        raise ValueError("diagnostic_repair_changed_returns")
            current = snapshot(row)
            source, target = ("after","before") if rollback else ("before","after")
            current_sha, source_sha, target_sha = (_storage_fingerprint(v) for v in (current, entry[source], entry[target]))
            if current_sha not in (source_sha, target_sha):
                raise ValueError("repair_row_changed")
            locked.append((kind,row,entry[target],current_sha == target_sha))
    entry = plan["prepared"]
    prepared = session.scalar(select(DailyRadarPreparedRun).where(DailyRadarPreparedRun.id==entry["id"])
        .with_for_update().execution_options(populate_existing=True))
    source, target = ("after","before") if rollback else ("before","after")
    if prepared is None or str(prepared.run_date) != entry["run_date"] or prepared.market != "TW":
        raise ValueError("repair_prepared_changed")
    current_steps = {k:(prepared.step_statuses or {}).get(k) for k in entry["before"]}
    if _storage_fingerprint(current_steps) not in (_storage_fingerprint(entry[source]), _storage_fingerprint(entry[target])):
        raise ValueError("repair_calendar_changed")
    if verify_context is not None:
        verify_context()
    if rollback and (plan["runs"] or _storage_fingerprint(entry["before"]) != _storage_fingerprint(entry["after"])):
        dependency = session.scalar(select(DailyRadarForwardValidationResult.id).where(
            DailyRadarForwardValidationResult.id.not_in([r["id"] for r in plan["results"]]),
            DailyRadarForwardValidationResult.outcome["historical_repair"]["calendar_sha256"].as_string()
                == fingerprint(plan["calendar"])).limit(1))
        if dependency is not None:
            raise ValueError("repair_dependent_manifest_still_applied")
    for kind,row,desired,done in locked:
        if not done:
            for key in (MUTABLE_FIELDS if kind == "results" else ("status","errors")):
                value = deepcopy(desired[key])
                if key in ("target_date","evaluation_as_of_date") and value is not None:
                    value = date.fromisoformat(value)
                setattr(row,key,value)
    steps = dict(prepared.step_statuses or {})
    for key,value in entry[target].items():
        if value is None:
            steps.pop(key,None)
        else:
            steps[key] = deepcopy(value)
    metadata_written = _storage_fingerprint(steps) != _storage_fingerprint(prepared.step_statuses)
    if metadata_written:
        prepared.step_statuses = steps
    session.flush()
    return {"written":sum(not done for *_,done in locked), "already_applied":sum(done for *_,done in locked),
            "metadata_written": int(metadata_written)}
