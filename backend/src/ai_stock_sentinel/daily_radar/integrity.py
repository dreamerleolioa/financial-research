"""Verify the background data that will actually be passed to prepared scoring."""
from ai_stock_sentinel.daily_radar.background_context import background_context_gap_reason
from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_cache_records
from ai_stock_sentinel.daily_radar.raw_data import _project_margin_context
from ai_stock_sentinel.daily_radar.work_queue import durable_enabled


def background_blockers(prepared, contexts):
    required = ["full_margin"]
    if durable_enabled():
        required.append("lending")
    blockers = []
    for context_type in required:
        reasons = {}
        for symbol in prepared.selected_symbols:
            trace = next((t for t in contexts.get(symbol, []) if t["context_type"] == context_type), {})
            reason = background_context_gap_reason(
                symbol=symbol, run_date=prepared.run_date, context_type=context_type,
                freshness=trace.get("freshness"), as_of_date=trace.get("as_of_date"),
                payload=trace.get("payload") or {}, missing_reason=trace.get("missing_reason"),
            )
            if reason:
                reasons[symbol] = reason
        if reasons:
            blockers.append({"step": "refresh-" + context_type.replace("_", "-"),
                "reason": "same_day_context_incomplete", "missing_symbols": list(reasons),
                "missing_symbol_reasons": reasons, "next_action": "resume"})
    return blockers


def prepared_scoring_records(cache_rows, contexts):
    # OHLCV can have been refreshed before the final margin check. Freeze the
    # verified margin trace into the record itself as well as its provenance.
    records = load_daily_radar_cache_records(cache_rows)
    for record in records:
        margin = next(t for t in contexts[record["symbol"]] if t["context_type"] == "full_margin")
        record["margin"] = _project_margin_context(margin, technical=record)
        record["data_dates"]["margin"] = margin["as_of_date"]
    return records
