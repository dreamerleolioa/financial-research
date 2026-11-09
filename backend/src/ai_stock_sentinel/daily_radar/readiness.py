"""Read-only operational inventory; execution success is not data readiness."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from ai_stock_sentinel.calibration.forward_validation import is_terminal_forward_validation_skip_reason, parse_date
from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_VALIDATION_WINDOWS
from ai_stock_sentinel.daily_radar.dependencies import DAILY_RADAR_REQUIRED_REFRESH_STEPS
from ai_stock_sentinel.daily_radar.forward_validation import (
    FORWARD_VALIDATION_VERSION, forward_validation_candidates_from_runs, _proven_termination,
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
from ai_stock_sentinel.db.models import DailyRadarRun


def recovery_action(reason):
    """Stable classes consumed by reports and the durable executor."""
    if reason in {"conflicting_price_records", "adjusted_history_changed", "price_basis_mismatch",
                  "research_basis_mismatch", "terminal_evidence_unverified"}:
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


def inventory_items(session, *, as_of_date, lookback_days=365):
    """Yield every expected window, including absent outcomes and diagnostics.

    Scope is explicit. No price-derived fallback can prove a missing window is
    immature. Caller may aggregate or persist work without truncating inventory.
    """
    start = as_of_date - timedelta(days=lookback_days - 1)
    candidates = forward_validation_candidates_from_runs(
        session, market="TW", start_date=start, end_date=as_of_date)
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
            future = [d for d in calendar.sessions if signal < d <= as_of_date] if calendar_ready else []
            for window in DAILY_RADAR_VALIDATION_WINDOWS:
                row = saved.get((candidate["candidate_id"], window))
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
                elif row["status"] == "skipped":
                    reason = row["skip_reason"] or "validation_not_available"
                    terminal = (reason in RESEARCH_TERMINAL_REASONS if basis == "next_open"
                                else is_terminal_forward_validation_skip_reason(reason))
                    if reason == "trading_terminated_in_window" and not (
                            parse_date(row["target_date"]) == target and _proven_termination(
                                symbol=candidate["symbol"], signal_date=signal, target_date=target,
                                evaluation_date=parse_date(row["evaluation_as_of_date"]), payload=row["outcome"])):
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
                yield {"candidate_id": candidate["candidate_id"], "symbol": candidate["symbol"],
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
                     "missing_outcome", "missing_diagnostic", "retryable"):
            report[name + "_count"] = counts[name]
        gap_count = sum(report["reasons"].values())
        report.update(ready=gap_count == 0, calendar_ready=bool(
            calendar and calendar.through >= as_of_date and not counts["unknown_maturity"]),
            expected_window_count=sum(counts.values()),
            mature_window_count=sum(counts.values()) - counts["immature"] - counts["unknown_maturity"],
            unresolved_count=gap_count, gaps_truncated=gap_count > len(report["gaps"]),
            reasons=dict(report["reasons"]))
    return reports


def read_readiness(session, *, run_date: date, lookback_days=365):
    """Caller holds a read-only/no-autoflush session; never fetches providers."""
    prepared = get_daily_radar_prepared_run(session, run_date=run_date, market="TW")
    steps = prepared.step_statuses if prepared else {}
    blockers = []
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
        try:
            require_prepared_open_session(prepared)
            _require_complete_daily_radar_raw_rows(get_final_raw_data_rows_for_symbols(
                session, run_date=run_date, symbols=prepared.selected_symbols),
                selected_symbols=prepared.selected_symbols, run_date=run_date)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": str(exc.detail)}
            blockers.append({"reason": detail.get("code"),
                "missing_symbols": detail.get("missing_symbols", []), "next_action": "resume"})
        if not prepared.market_context:
            blockers.append({"reason": "market_context_missing", "next_action": "resume"})
    run = session.scalar(select(DailyRadarRun).where(DailyRadarRun.id.in_(canonical_radar_run_ids(
        market="TW", start_date=run_date, end_date=run_date))))
    validation = validation_inventory(session, as_of_date=run_date, lookback_days=lookback_days)
    return {"version": "radar-readiness-v1", "market": "TW", "requested_run_date": str(run_date),
        "effective_run_date": str(run_date), "lookback_days": lookback_days,
        "sample_start_date": str(run_date - timedelta(days=lookback_days - 1)),
        "scoring": {"ready": not blockers, "blockers": blockers,
            "prepared_run_id": prepared.id if prepared else None,
            "symbol_count": len(prepared.selected_symbols) if prepared else 0,
            "steps": {key: steps.get(key) for key in DAILY_RADAR_REQUIRED_REFRESH_STEPS}},
        "publication": {"state": "published" if run else "not_published", "run_id": run.id if run else None,
            "status": run.status if run else None, "candidate_count": run.candidate_count if run else None},
        "validation": validation,
        "auxiliary": {key: steps.get(key) for key in ("refresh-avwap", "refresh-managed-raw-data")},
        "data_ready": bool(run and run.status == "completed" and not blockers
                           and all(r["ready"] for r in validation.values()))}
