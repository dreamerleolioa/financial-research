"""Bounded, reversible corrections that keep the published economic result intact."""

from copy import deepcopy

from ai_stock_sentinel.daily_radar import history_repair as history
from ai_stock_sentinel.daily_radar import publication_close_reports, reviewed_publication_close as review
from ai_stock_sentinel.daily_radar.next_open_history_repair import _calendar_context
from ai_stock_sentinel.daily_radar.validation_inputs import validation_candidate_revision
from ai_stock_sentinel.db.models import DailyRadarCandidate

VERSION = review.VERSION


def _after(entry, correction, repair_id, calendar):
    before = entry["before"]
    previous = before["outcome"]
    marker = {
        "version": VERSION,
        "repair_id": repair_id,
        "operation": "diagnostic",
        "before_sha256": history.fingerprint(before),
        "calendar_sha256": history.fingerprint(calendar.payload()),
        "as_of_date": str(calendar.through),
        "observation_origin": entry["observation_origin"],
        "price_evidence_sha256": entry["capture_sha256"],
        "publication_close_correction": correction,
        "original_diagnostic": deepcopy(previous.get("observation_diagnostic")),
        "prior_evidence": {"historical_repair": deepcopy(previous["historical_repair"])}
        if "historical_repair" in previous
        else {},
    }
    diagnostic = correction["diagnostic"] | {
        "price_basis_version": VERSION,
        "original_quote_status": correction["original_quote_status"],
    }
    return before | {
        "outcome": previous | {"observation_diagnostic": diagnostic, "historical_repair": marker}
    }


def build(records, calendar, prepared):
    results = []
    for item in records:
        entry = {
            k: item[k]
            for k in (
                "id",
                "symbol",
                "before",
                "observation_origin",
                "candidate_sha256",
                "validation_candidate_revision",
                "capture_sha256",
                "earlier_date",
                "unverified",
            )
        }
        results.append(entry | {"operation": "diagnostic"})
    repair_id = history.fingerprint([VERSION, results, prepared])
    for entry, item in zip(results, records, strict=True):
        entry["after"] = _after(entry, item["correction"], repair_id, calendar)
    plan = {
        "version": VERSION,
        "calendar": calendar.payload(),
        "prepared": prepared,
        "repair_id": repair_id,
        "results": results,
        "runs": [],
    }
    return plan | {"sha256": history.fingerprint(plan)}


def apply(session, plan, *, expected_sha256, calendar, captures, rollback=False):
    if (
        not plan["results"]
        or plan["runs"] != []
        or plan["calendar"] != calendar.payload()
        or plan["prepared"] != _calendar_context(session, calendar)
    ):
        raise ValueError("publication_close_manifest_scope")
    scopes = {
        key: (publication_close_reports.replay(cap), history.fingerprint(cap))
        for key, cap in captures.items()
    }
    entries = {(r["before"]["candidate_id"], r["before"]["window_days"]): r for r in plan["results"]}

    def verify(candidate, after):
        entry = entries[after["candidate_id"], after["window_days"]]
        scope, digest = scopes[candidate["symbol"]]
        before = entry["before"]
        if (
            entry["operation"] != "diagnostic"
            or entry["symbol"] != candidate["symbol"]
            or digest != entry["capture_sha256"]
            or type(entry["unverified"]) is not bool
            or before["benchmark_symbol"] != "TAIEX"
            or entry["validation_candidate_revision"] != validation_candidate_revision(candidate)
            or (before["outcome"].get("observation_diagnostic") or {}).get("status")
            not in (None, "insufficient_data")
            or (before["outcome"].get("historical_repair") or {}).get("version") == VERSION
        ):
            raise ValueError("publication_close_manifest_evidence_mismatch")
        full = session.get(DailyRadarCandidate, candidate["candidate_id"]).input_snapshot
        correction = review.evaluate(
            candidate,
            full,
            before,
            calendar,
            scope,
            earlier_date=entry["earlier_date"],
            unverified=entry["unverified"],
        )
        if history.fingerprint(after) != history.fingerprint(
            _after(entry, correction, plan["repair_id"], calendar)
        ):
            raise ValueError("publication_close_manifest_replay_mismatch")

    return history._apply_result_manifest(
        session,
        plan,
        expected_sha256=expected_sha256,
        rollback=rollback,
        manifest_version=VERSION,
        result_version=history.FORWARD_VALIDATION_VERSION,
        verify_after=verify,
        verify_context=lambda: _calendar_context(session, calendar),
    )
