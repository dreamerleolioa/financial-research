"""Candidate-pool diagnostics derived from a fixed scoring run, without rescoring."""
from collections import Counter, defaultdict
from typing import Any

DATA_REASONS = frozenset({"data_gap", "stale_core_data"})
ELIGIBILITY_REASONS = frozenset({"low_liquidity", "min_price", "unsupported_daily_radar_symbol"})
SIGNAL_REASONS = frozenset({"overextended", "weak_structure", "margin_crowding"})


def research_status(snapshot: dict[str, Any], data_dates: dict[str, Any], record_date: str) -> str:
    trend = snapshot.get("medium_term_context") or {}
    if (str(trend.get("as_of_date")) != record_date
            or str(data_dates.get("ohlcv")) != record_date):
        return "data_pending"
    if trend.get("trend_status") == "constructive":
        return ("waiting_for_consolidation" if snapshot.get("timing_status") == "wait_for_consolidation"
                else "trend_forming")
    return "structure_watch" if trend.get("trend_status") == "weak" else "data_pending"


def freeze_discovery_summary(run: Any, audit: dict[str, Any] | None) -> None:
    """Store a small immutable audit on one candidate, without a schema migration."""
    if (not isinstance(audit, dict) or audit.get("run_date") != str(run.run_date)
            or audit.get("status") == "unavailable" or audit.get("missing_market_dates")):
        return
    fields = ("scanned_symbol_count", "eligible_symbol_count", "discovered_symbol_count")
    if not all(type(audit.get(field)) is int and audit[field] >= 0 for field in fields):
        return
    if not run.candidates:
        return
    tracks = audit.get("track_counts")
    exclusions = audit.get("excluded_symbol_reasons")
    summary = {
        "version": "candidate-pool-discovery-v1", "run_date": str(run.run_date),
        **{field: audit[field] for field in fields},
        "track_counts": {key: value for key, value in (tracks if isinstance(tracks, dict) else {}).items()
                         if key in {"market_trend", "market_price_volume"} and type(value) is int and value >= 0},
        "excluded_reason_counts": dict(sorted(Counter(value for value in
            (exclusions if isinstance(exclusions, dict) else {}).values() if isinstance(value, str)).items())),
    }
    owner = min(run.candidates, key=lambda row: row.symbol)
    owner.input_snapshot = dict(owner.input_snapshot or {}) | {"pool_discovery_summary": summary}


def pool_summary(run: Any) -> dict[str, Any]:
    reasons_by_symbol = defaultdict(set)
    failed_symbols = set()
    duplicate_count = 0
    for error in run.errors or []:
        if error.get("code") == "duplicate_universe_symbol":
            duplicate_count += 1
            continue
        symbol = error.get("symbol")
        if not symbol:
            continue
        reasons_by_symbol[symbol].update(error.get("reasons") or [])
        if error.get("code") == "candidate_processing_error":
            failed_symbols.add(symbol)
    candidates = {row.symbol: row for row in run.candidates}
    for symbol, row in candidates.items():
        reasons_by_symbol[symbol].update(
            reason["code"] for reason in row.prefilter_reasons or [] if reason.get("code")
        )
    states = Counter({key: 0 for key in (
        "selected", "data_pending", "eligibility_excluded", "signal_filtered",
        "limit_deferred", "processing_error",
    )})
    for symbol in candidates.keys() | reasons_by_symbol.keys() | failed_symbols:
        row = candidates.get(symbol)
        reasons = reasons_by_symbol[symbol]
        if row is not None and row.selection_status == "selected":
            states["selected"] += 1
        elif reasons & DATA_REASONS:
            states["data_pending"] += 1
        elif reasons & ELIGIBILITY_REASONS:
            states["eligibility_excluded"] += 1
        elif reasons & SIGNAL_REASONS:
            states["signal_filtered"] += 1
        elif row is not None and (row.input_snapshot or {}).get("selection_reason") == "candidate_limit":
            states["limit_deferred"] += 1
        elif symbol in failed_symbols:
            states["processing_error"] += 1
    return {
        "version": "candidate-pool-summary-v1", "population_scope": "scored_raw_records",
        "input_record_count": run.universe_count,
        "state_counts": dict(states), "duplicate_record_count": duplicate_count,
        "unclassified_record_count": max(0, run.universe_count - duplicate_count - sum(states.values())),
        "comparable_shadow_count": sum(row.shadow_cohort == "comparable" for row in candidates.values()),
        "eligibility_audit_shadow_count": sum(row.shadow_cohort == "eligibility_audit" for row in candidates.values()),
        "reason_counts": dict(sorted(Counter(reason for reasons in reasons_by_symbol.values() for reason in reasons).items())),
        "discovery_summary": next((row.input_snapshot["pool_discovery_summary"] for row in candidates.values()
                                   if (row.input_snapshot or {}).get("pool_discovery_summary", {}).get("version") == "candidate-pool-discovery-v1"
                                   and row.input_snapshot["pool_discovery_summary"].get("run_date") == str(run.run_date)), None),
    }
