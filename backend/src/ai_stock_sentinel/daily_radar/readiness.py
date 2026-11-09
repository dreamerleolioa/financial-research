"""Read-only operational inventory; execution success is not data readiness."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select, func, inspect

from ai_stock_sentinel.calibration.forward_validation import is_terminal_forward_validation_skip_reason, parse_date
from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_VALIDATION_WINDOWS
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.dependencies import DAILY_RADAR_REQUIRED_REFRESH_STEPS
from ai_stock_sentinel.daily_radar.forward_validation import (
    FORWARD_VALIDATION_VERSION, forward_validation_candidates_from_runs,
    persisted_forward_validation_outcomes,
)
from ai_stock_sentinel.daily_radar.market_session import require_prepared_open_session
from ai_stock_sentinel.daily_radar.observation_validation import (
    OBSERVATION_DIAGNOSTIC_VERSION, EVENT_STATUSES, canonical_radar_run_ids,
)
from ai_stock_sentinel.daily_radar.pipeline_support import _require_complete_daily_radar_raw_rows
from ai_stock_sentinel.daily_radar.repository import (
    get_daily_radar_prepared_run, get_final_raw_data_rows_for_symbols,
)
from ai_stock_sentinel.daily_radar.research_validation import (
    RESEARCH_VALIDATION_VERSION, RESEARCH_TERMINAL_REASONS, RESEARCH_PRICE_BASIS,
)
from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar
from ai_stock_sentinel.daily_radar.terminal_integrity import terminal_evidence_matches
from ai_stock_sentinel.daily_radar.validation_inputs import (
    validation_candidate_revision, validation_inputs_match, UNVERIFIED,
)
from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarCandidate


def recovery_action(reason):
    """Stable classes consumed by reports and the durable executor."""
    if reason in {"conflicting_price_records", "adjusted_history_changed", "price_basis_mismatch",
                  "research_basis_mismatch", "terminal_evidence_unverified", "calendar_evidence_conflict", UNVERIFIED}:
        return "needs_review"
    if reason in {"research_recovery_budget_exhausted", "budget_exhausted"}:
        return "resume"
    if reason in {"missing_observation_diagnostic", "observation_diagnostic_incomplete"}:
        return "repair_diagnostic"
    if reason in {"official_calendar_unavailable", "calendar_window_mismatch", "non_trading_signal_date"}:
        return "repair_calendar"
    if reason in {"invalid_response", "invalid_research_ohlc"}:
        return "failed_internal"
    return "waiting_provider"


def _needs_diagnostic(candidate):
    origin = candidate.get("observation_origin") or {}
    return (candidate.get("selection_status") == "selected"
            and origin.get("candidate_id") == candidate["candidate_id"])


def classify_window(candidate, row, *, basis, window, as_of_date, calendar):
    """One shared readiness decision for reports and persisted work completion."""
    signal = date.fromisoformat(candidate["record_date"])
    calendar_ready = calendar is not None and calendar.through >= as_of_date
    future = [d for d in calendar.sessions if signal < d <= as_of_date] if calendar_ready else []
    target = future[window - 1] if len(future) >= window else None
    reason, state = None, "validated"
    if not calendar_ready:
        state, reason = "unknown_maturity", "official_calendar_unavailable"
    elif signal not in calendar.sessions:
        state, reason = "invalid_window", "non_trading_signal_date"
    elif target is None:
        state = "immature"
    elif row is None:
        state, reason = "missing_outcome", "validation_outcome_missing"
    elif not validation_inputs_match(candidate, row):
        state, reason = 'input_unverified', UNVERIFIED
    elif row["status"] == "skipped":
        reason = row["skip_reason"] or "validation_not_available"
        terminal = (reason in RESEARCH_TERMINAL_REASONS if basis == "next_open"
                    else is_terminal_forward_validation_skip_reason(reason))
        if terminal and not terminal_evidence_matches(candidate, row, basis=basis,
                sessions=future[:window], as_of_date=as_of_date):
            terminal, reason = False, "terminal_evidence_unverified"
        state = "terminal" if terminal else "retryable"
    elif row["target_date"] != str(target):
        state, reason = "invalid_window", "calendar_window_mismatch"
    elif basis == "next_open" and (
            row["outcome"].get("return_basis") != "next_open"
            or row["outcome"].get("price_basis") != RESEARCH_PRICE_BASIS):
        state, reason = "invalid_window", "research_basis_mismatch"
    elif basis == "signal_close" and _needs_diagnostic(candidate):
        diagnostic = row["outcome"].get("observation_diagnostic") or {}
        if (diagnostic.get("version") != OBSERVATION_DIAGNOSTIC_VERSION
                or diagnostic.get("status") == "not_first_observation"
                or diagnostic.get("first_seen_date") not in (None, candidate["record_date"])):
            state, reason = "missing_diagnostic", "missing_observation_diagnostic"
        elif (diagnostic.get("status") not in EVENT_STATUSES | {"already_broken_out", "already_invalidated"}
              or diagnostic.get("missing_reason")):
            state, reason = "missing_diagnostic", "observation_diagnostic_incomplete"
    return state, reason, target


def inventory_items(session, *, as_of_date, lookback_days=365, candidate_ids=None):
    """Yield every expected window, including absent outcomes and diagnostics.

    Scope is explicit. No price-derived fallback can prove a missing window is
    immature. Caller may aggregate or persist work without truncating inventory.
    """
    start = as_of_date - timedelta(days=lookback_days - 1)
    candidates = forward_validation_candidates_from_runs(
        session, market="TW", start_date=start, end_date=as_of_date, candidate_ids=candidate_ids)
    first = min((date.fromisoformat(c["record_date"]) for c in candidates), default=as_of_date)
    calendar = load_calendar(session, start_date=first, as_of_date=as_of_date)
    calendar_ready = calendar is not None and calendar.through >= as_of_date
    versions = {"signal_close": FORWARD_VALIDATION_VERSION, "next_open": RESEARCH_VALIDATION_VERSION}
    for basis, version in versions.items():
        active = candidates if basis == "signal_close" else [c for c in candidates
            if c["selection_status"] == "selected"
            or (c["selection_status"] == "shadow" and c["shadow_cohort"] == "comparable")]
        saved = {(r["candidate_id"], r["window_days"]): r for r in persisted_forward_validation_outcomes(
            session, active, windows=DAILY_RADAR_VALIDATION_WINDOWS,
            as_of_date=as_of_date, validation_version=version)
            if not r.get("evaluation_as_of_date") or r["evaluation_as_of_date"] <= str(as_of_date)}
        for candidate in active:
            signal = date.fromisoformat(candidate["record_date"])
            for window in DAILY_RADAR_VALIDATION_WINDOWS:
                row = saved.get((candidate["candidate_id"], window))
                state, reason, target = classify_window(candidate, row, basis=basis, window=window,
                    as_of_date=as_of_date, calendar=calendar)
                yield {"candidate_id": candidate["candidate_id"], "symbol": candidate["symbol"],
                    "candidate_revision": validation_candidate_revision(candidate),
                    "calendar_revision": fingerprint([str(d) for d in calendar.sessions if signal <= d <= target]) if target else None,
                    "signal_date": str(signal), "return_basis": basis, "validation_version": version,
                    "window_days": window, "expected_target_date": str(target) if target else None,
                    "state": state, "reason": reason, "next_action": recovery_action(reason) if reason else None}


def validation_inventory(session, *, as_of_date, lookback_days=365, sample_limit=100):
    calendar = load_calendar(session, start_date=as_of_date, as_of_date=as_of_date)
    reports = {basis: {"validation_version": version, "counts": Counter(), "reasons": Counter(), "gaps": []}
        for basis, version in (("signal_close", FORWARD_VALIDATION_VERSION), ("next_open", RESEARCH_VALIDATION_VERSION))}
    for item in inventory_items(session, as_of_date=as_of_date, lookback_days=lookback_days):
        report = reports[item["return_basis"]]
        report["counts"][item["state"]] += 1
        if item["state"] not in {"validated", "terminal", "immature"}:
            report["reasons"][item["reason"]] += 1
            if len(report["gaps"]) < sample_limit:
                report["gaps"].append(item)
    for report in reports.values():
        counts = report.pop("counts")
        for name in ("validated", "terminal", "immature", "unknown_maturity", "invalid_window",
                     "missing_outcome", "missing_diagnostic", "retryable", "input_unverified"):
            report[name + "_count"] = counts[name]
        gap_count = sum(report["reasons"].values())
        report.update(ready=gap_count == 0, calendar_ready=bool(
            calendar and calendar.through >= as_of_date and not counts["unknown_maturity"]),
            expected_window_count=sum(counts.values()),
            mature_window_count=sum(counts.values()) - counts["immature"] - counts["unknown_maturity"],
            unresolved_count=gap_count, gaps_truncated=gap_count > len(report["gaps"]),
            reasons=dict(report["reasons"]))
    return reports


def scoring_prerequisite_inputs(session, *, run_date):
    """Actual publication prerequisites without scanning validation history."""
    from ai_stock_sentinel.daily_radar.work_queue import durable_enabled
    prepared = get_daily_radar_prepared_run(session, run_date=run_date, market="TW")
    steps = prepared.step_statuses if prepared else {}
    blockers = []
    cache_rows = []
    if prepared is None:
        blockers.append({"reason": "prepared_run_missing", "next_action": "prepare_universe"})
    else:
        for step in DAILY_RADAR_REQUIRED_REFRESH_STEPS:
            data = steps.get(step) or {}
            if data.get("status") != "completed":
                blockers.append({"step": step, "reason": "refresh_step_incomplete",
                    "missing_symbols": data.get("missing_symbols", []),
                    "missing_symbol_reasons": data.get("missing_symbol_reasons", {}),
                    "next_action": "resume"})
        if durable_enabled():
            from ai_stock_sentinel.daily_radar.integrity import background_gaps
            for step, missing in background_gaps(session, prepared).items():
                if missing:
                    blockers.append({'step': step, 'reason': 'same_day_context_incomplete',
                        'missing_symbols': missing, 'next_action': 'resume'})
        try:
            require_prepared_open_session(prepared)
            cache_rows = _require_complete_daily_radar_raw_rows(get_final_raw_data_rows_for_symbols(
                session, run_date=run_date, symbols=prepared.selected_symbols),
                selected_symbols=prepared.selected_symbols, run_date=run_date)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": str(exc.detail)}
            blockers.append({"reason": detail.get("code"),
                "missing_symbols": detail.get("missing_symbols", []), "next_action": "resume"})
        if not prepared.market_context:
            blockers.append({"reason": "market_context_missing", "next_action": "resume"})
    if durable_enabled():
        from ai_stock_sentinel.daily_radar.pipeline_support import _required_institutional_archive_details
        from ai_stock_sentinel.db.models import TaiwanInstitutionalReportSnapshot
        try:
            if not inspect(session.connection()).has_table(TaiwanInstitutionalReportSnapshot.__tablename__):
                raise HTTPException(409, detail={'missing_markets': ['TW', 'TWO']})
            _required_institutional_archive_details(session, run_date=run_date)
        except HTTPException as exc:
            blockers.append({'step': 'refresh-institutional-flows',
                'reason': 'daily_radar_institutional_archive_incomplete',
                'missing_markets': exc.detail.get('missing_markets', ['TW', 'TWO']), 'next_action': 'resume'})
    return prepared, steps, blockers, cache_rows


def read_readiness(session, *, run_date: date, lookback_days=365):
    """Caller holds a read-only/no-autoflush session; never fetches providers."""
    prepared, steps, blockers, cache_rows = scoring_prerequisite_inputs(session, run_date=run_date)
    run = session.scalar(select(DailyRadarRun).where(DailyRadarRun.id.in_(canonical_radar_run_ids(
        market="TW", start_date=run_date, end_date=run_date))))
    stored_selected_count = session.scalar(select(func.count()).select_from(DailyRadarCandidate).where(
        DailyRadarCandidate.run_id == run.id, DailyRadarCandidate.selection_status == "selected")) if run else None
    output_complete = bool(run and stored_selected_count == run.candidate_count)
    validation = validation_inventory(session, as_of_date=run_date, lookback_days=lookback_days)
    from ai_stock_sentinel.daily_radar.source_snapshots import snapshots_enabled
    input_summary = None
    current_digest = None
    if snapshots_enabled() and prepared is not None and not blockers:
        from ai_stock_sentinel.daily_radar.input_manifests import (
            collect_execution_inputs, prepared_input_context, manifest_envelope,
        )
        from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_cache_records
        from ai_stock_sentinel.daily_radar.repository import get_shared_background_context_trace_by_symbol, BACKGROUND_CONTEXT_TYPES
        from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
        contexts = get_shared_background_context_trace_by_symbol(session, symbols=prepared.selected_symbols,
            context_types=BACKGROUND_CONTEXT_TYPES, reference_date=run_date, point_in_time=True)
        current_inputs = collect_execution_inputs(session, run_date=run_date, market="TW",
            records=load_daily_radar_cache_records(cache_rows), market_context=prepared.market_context,
            background_contexts=contexts, input_context=prepared_input_context(prepared, cache_rows))
        current_digest = fingerprint(manifest_envelope(run_date=run_date, market="TW", payload=current_inputs))
    if run and snapshots_enabled():
        from ai_stock_sentinel.daily_radar.input_manifests import published_input_summary
        input_summary = published_input_summary(session, run)
        input_summary["matches_current_inputs"] = bool(current_digest and input_summary.get("fingerprint") == current_digest)
        if input_summary["state"] == "recorded":
            output_complete = output_complete and input_summary["output_complete"]
    return {"version": "radar-readiness-v1", "market": "TW", "requested_run_date": str(run_date),
        "effective_run_date": str(run_date), "lookback_days": lookback_days,
        "sample_start_date": str(run_date - timedelta(days=lookback_days - 1)),
        "scoring": {"ready": not blockers, "blockers": blockers,
            "prepared_run_id": prepared.id if prepared else None,
            "input_fingerprint": current_digest,
            "symbol_count": len(prepared.selected_symbols) if prepared else 0,
            "steps": {key: steps.get(key) for key in DAILY_RADAR_REQUIRED_REFRESH_STEPS}},
        "publication": {"state": "published" if run else "not_published", "run_id": run.id if run else None,
            "status": run.status if run else None, "candidate_count": run.candidate_count if run else None,
            "stored_selected_count": stored_selected_count, "output_complete": output_complete,
            "input_manifest": input_summary},
        "validation": validation,
        "auxiliary": {key: steps.get(key) for key in ("refresh-avwap", "refresh-managed-raw-data")},
        "data_ready": bool(run and run.status == "completed" and output_complete and not blockers
                           and (input_summary is None or (input_summary["state"] == "recorded"
                                and input_summary["matches_current_inputs"]))
                           and all(r["ready"] for r in validation.values()))}
