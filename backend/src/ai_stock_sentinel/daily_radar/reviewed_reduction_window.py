"""Historical next-open reconciliation with a confirmed cash-reduction halt."""

from datetime import date
from decimal import Decimal

from ai_stock_sentinel.calibration import (
    official_corporate_actions as actions,
    twse_rights_recovery as rights,
)
from ai_stock_sentinel.daily_radar import history_repair as hist, trading_status
from ai_stock_sentinel.daily_radar.next_open_official_repair import prior_actions
from ai_stock_sentinel.daily_radar.research_validation import (
    evaluate_research_window,
    RESEARCH_VALIDATION_VERSION,
)
from ai_stock_sentinel.daily_radar.validation_inputs import (
    bind_validation_input,
    validation_candidate_revision,
)
from ai_stock_sentinel.daily_radar.reviewed_distribution_basis import _matches

VERSION = "reviewed-next-open-reduction-window-v1"


def reduction_witness(symbol, event, vendor, chart):
    day = date.fromisoformat(event["halt_start"])
    raw, source = chart
    if not actions.valid_split_receipt(raw, source, symbol, day):
        raise ValueError("reduction_window_chart_receipt")
    bar = vendor.get(str(day), {})
    if bar.get("dividends") != event["cash_per_share"] or not actions.reduction_precision(
        raw,
        symbol,
        day,
        str(Decimal(str(event["share_ratio"])) * 1000),
        bar.get("stock_splits"),
        bar.get("dividends"),
    ):
        raise ValueError("reduction_window_split_terms")


def verify_benchmark(archive, days, official):
    if (
        archive["symbol"] != "TAIEX"
        or archive.get("source") != "yfinance"
        or archive.get("error")
        or archive["basis"] != "next_open"
        or archive["sha256"] != hist.fingerprint({k: v for k, v in archive.items() if k != "sha256"})
    ):
        raise ValueError("reduction_window_benchmark_archive")
    rows = {r["date"]: r for r in archive["rows"]}
    if len(rows) != len(archive["rows"]):
        raise ValueError("reduction_window_duplicate_benchmark")
    for day in days:
        bar = rows.get(day, {})
        if (
            bar.get("price_basis") != "unadjusted_price"
            or bar.get("price_source") != "yfinance"
            or bar.get("corporate_actions_checked") is not True
            or bar.get("ohlc_conflict_fields")
            or bar.get("dividends") != 0
            or bar.get("stock_splits") not in (0, 1)
            or day not in official
            or not _matches(bar, official[day])
        ):
            raise ValueError("reduction_window_benchmark_conflict")


def evaluate(candidate, before, calendar, scope, history, chart, benchmark):
    symbol = candidate["symbol"]
    signal = candidate["record_date"]
    days = [str(d) for d in calendar.sessions if d.isoformat() > signal][: before["window_days"]]
    if (
        len(days) != before["window_days"]
        or date.fromisoformat(signal) not in calendar.sessions
        or before["validation_version"] != RESEARCH_VALIDATION_VERSION
        or before["signal_date"] != signal
        or before["candidate_id"] != candidate["candidate_id"]
        or before["benchmark_symbol"] != "TAIEX"
        or before["status"] != "skipped"
        or before["skip_reason"] not in ("stock_split_in_window", "conflicting_price_records")
        or prior_actions(before["outcome"])
        or scope["symbol"] != symbol
        or history["symbol"] != symbol
        or history["basis"] != "next_open"
        or history.get("error")
        or history.get("source") != "yfinance"
        or history.get("start", "") > signal
        or hist.fingerprint({k: v for k, v in history.items() if k != "sha256"}) != history["sha256"]
    ):
        raise ValueError("reduction_window_inputs")
    target = days[-1]
    saved_targets = (
        before.get("target_date"),
        before["outcome"].get("expected_target_date"),
        before["outcome"].get("target_date"),
    )
    if any(saved is not None and saved != target for saved in saved_targets):
        raise ValueError("reduction_window_original_target_mismatch")
    # Only a single, evidenced reduction may explain the conflicting archive.
    events = [e for e in scope["events"] if signal < e["date"] <= str(calendar.through)]
    if (
        len(events) != 1
        or events[0]["kind"] != "reduction"
        or events[0]["halt_start"] <= signal
        or target >= events[0]["date"]
        or any(signal[:7] <= m <= events[0]["date"][:7] for m in scope["errors"])
    ):
        raise ValueError("reduction_window_event_scope")
    event = events[0]
    vendor = {r["date"]: r for r in history["rows"]}
    active = [r for d, r in vendor.items() if signal <= d <= event["halt_start"]]
    if history.get("end", "") < event["halt_start"] or any(
        r.get("price_source") != "yfinance"
        or r.get("price_basis") != "unadjusted_price"
        or r.get("corporate_actions_checked") is not True
        or r.get("ohlc_conflict_fields")
        for r in active
    ):
        raise ValueError("reduction_window_unverified_vendor_basis")
    if len(vendor) != len(history["rows"]):
        raise ValueError("reduction_window_duplicate_vendor_quote")
    reduction_witness(symbol, event, vendor, chart)
    quotes = scope["stock"]
    all_days = [signal, *days]
    if any(d not in scope["index"] for d in all_days) or any(
        r.get("stock_splits") not in (0, 1) or r.get("dividends") != 0
        for day, r in vendor.items()
        if signal <= day <= target and day != event["halt_start"]
    ):
        raise ValueError("reduction_window_unexplained_marker")
    verify_benchmark(benchmark, all_days, scope["index"])
    if target >= event["halt_start"]:
        proof = scope.get("suspensions", {}).get(target)
        if (
            not trading_status.matches(proof, symbol=symbol, target=date.fromisoformat(target))
            or proof.get("event_type") != "cash_reduction"
            or proof["halt_start"] != event["halt_start"]
            or proof["resume_date"] != event["date"]
            or any(d in quotes for d in days if d >= event["halt_start"])
        ):
            raise ValueError("reduction_window_unverified_halt")
        return bind_validation_input(
            candidate,
            before
            | {
                "status": "skipped",
                "skip_reason": trading_status.REASON,
                "target_date": target,
                "evaluation_as_of_date": str(calendar.through),
                "outcome": {
                    "return_basis": "next_open",
                    "price_basis": "unadjusted_price",
                    "expected_target_date": target,
                    trading_status.KEY: proof,
                },
            },
        )
    if before["skip_reason"] != "conflicting_price_records":
        raise ValueError("reduction_window_unexplained_prior_split")
    factor = 1 / Decimal(str(event["share_ratio"]))
    if any(
        d not in vendor or d not in quotes or not _matches(vendor[d], quotes[d], factor) for d in all_days
    ):
        raise ValueError("reduction_window_unexplained_quote")
    stock = rights._unadjusted_rows([quotes[d] for d in all_days], "official_reduction_outside_window")
    index = rights._unadjusted_rows([scope["index"][d] for d in all_days], "index_not_applicable")
    result = evaluate_research_window(
        candidate,
        price_series=stock,
        benchmark_prices=index,
        window_days=before["window_days"],
        as_of_date=calendar.through,
        benchmark_symbol="TAIEX",
        trading_calendar=calendar.sessions,
        validate_signal_session=True,
    )
    if result["status"] != "validated" or result["target_date"] != target:
        raise ValueError("reduction_window_incomplete")
    return before | {k: result[k] for k in hist.MUTABLE_FIELDS}


def apply(session, plan, *, expected_sha256, calendar, capture, archive, chart, benchmark, rollback=False):
    from ai_stock_sentinel.daily_radar.next_open_history_repair import _calendar_context
    from ai_stock_sentinel.daily_radar.publication_close_reports import replay

    scope = replay(capture)
    digest = hist.fingerprint([capture, archive["sha256"], benchmark["sha256"], chart])
    if (
        not plan["results"]
        or plan["runs"]
        or plan["calendar"] != calendar.payload()
        or plan["prepared"] != _calendar_context(session, calendar)
    ):
        raise ValueError("reduction_window_manifest_scope")
    entries = {(e["before"]["candidate_id"], e["before"]["window_days"]): e for e in plan["results"]}

    def verify(candidate, after):
        entry = entries[after["candidate_id"], after["window_days"]]
        if entry["operation"] != "recompute" or entry[
            "validation_candidate_revision"
        ] != validation_candidate_revision(candidate):
            raise ValueError("reduction_window_manifest_inputs")
        replayed = evaluate(candidate, entry["before"], calendar, scope, archive, chart, benchmark)
        if hist.fingerprint(mark(replayed, entry, plan["repair_id"], calendar, digest)) != hist.fingerprint(
            after
        ):
            raise ValueError("reduction_window_manifest_replay")

    return hist._apply_result_manifest(
        session,
        plan,
        expected_sha256=expected_sha256,
        rollback=rollback,
        manifest_version=VERSION,
        result_version=RESEARCH_VALIDATION_VERSION,
        verify_after=verify,
        verify_context=lambda: _calendar_context(session, calendar),
    )


def mark(row, entry, repair_id, calendar, digest):
    marker = {
        "version": VERSION,
        "repair_id": repair_id,
        "operation": "recompute",
        "before_sha256": hist.fingerprint(entry["before"]),
        "calendar_sha256": hist.fingerprint(calendar.payload()),
        "price_evidence_sha256": digest,
        "as_of_date": str(calendar.through),
        "observation_origin": entry["observation_origin"],
        "prior_evidence": entry["before"]["outcome"],
    }
    return row | {"outcome": row["outcome"] | {"historical_repair": marker}}
