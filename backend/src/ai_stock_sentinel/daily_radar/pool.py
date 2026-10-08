"""Candidate-pool diagnostics derived from a fixed scoring run, without rescoring."""
from collections import Counter, defaultdict
from typing import Any

DATA_REASONS = frozenset({"data_gap", "stale_core_data"})
ELIGIBILITY_REASONS = frozenset({"low_liquidity", "min_price", "unsupported_daily_radar_symbol"})
SIGNAL_REASONS = frozenset({"overextended", "weak_structure", "margin_crowding"})


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
    }
