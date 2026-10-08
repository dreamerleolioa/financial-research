"""Versioned next-open price research, isolated from legacy close-based results."""
from collections import Counter
from datetime import date

from ai_stock_sentinel.calibration.forward_validation import (
    candidate_key, merge_price_series, number, ordered_positive_values, parse_date,
    pct_return, TERMINAL_FORWARD_VALIDATION_SKIP_REASONS,
)
from ai_stock_sentinel.calibration.forward_validation_planning import FORWARD_PRICE_FETCH_BATCH_SIZE
from ai_stock_sentinel.daily_radar.forward_validation import (
    default_due_start_date, exclude_persisted_daily_radar_windows,
    forward_validation_candidates_from_runs, persisted_forward_validation_outcomes,
    load_benchmark_prices_from_prepared_market_context,
    upsert_forward_validation_results, validate_forward_validation_benchmark,
)


RESEARCH_VALIDATION_VERSION = "daily-radar-next-open-price-v1"
RESEARCH_PRICE_BASIS = "unadjusted_price"
RESEARCH_COSTS = (0.0, 0.5, 1.0)
RESEARCH_TERMINAL_REASONS = TERMINAL_FORWARD_VALIDATION_SKIP_REASONS | {"stock_split_in_window"}


def _index(rows):
    # Use the existing conflict-preserving merger, without its open-price fallback.
    merged = merge_price_series({"rows": rows}, {})["rows"]
    return {parse_date(row["date"]): row for row in merged}


def evaluate_research_window(candidate, *, price_series, benchmark_prices,
                             window_days, as_of_date, benchmark_symbol, trading_calendar=None):
    signal = parse_date(candidate.get("record_date"))
    base = {"candidate_id": candidate.get("candidate_id"), "symbol": candidate.get("symbol"),
            "signal_date": signal.isoformat() if signal else None, "window_days": window_days,
            "validation_version": RESEARCH_VALIDATION_VERSION, "benchmark_symbol": benchmark_symbol,
            "evaluation_as_of_date": as_of_date.isoformat()}

    def skip(reason, *, pending=False):
        return base | {"status": "pending" if pending else "skipped", "target_date": None,
                       "skip_reason": reason, "outcome": {}}

    if signal is None:
        return skip("signal_date_missing")
    if signal > as_of_date:
        return skip("future_signal_date")
    benchmark = _index(benchmark_prices)
    calendar = sorted(day for day, row in benchmark.items()
                      if signal < day <= as_of_date and (number(row.get("close")) or 0) > 0)
    if trading_calendar is not None:
        calendar = sorted({day for day in trading_calendar if signal < day <= as_of_date})
    if signal not in benchmark:
        return skip("missing_benchmark")
    if len(calendar) < window_days:
        # Beyond a conservative calendar bound, an incomplete index history is
        # missing evidence, not proof that the observation is still immature.
        if (as_of_date - signal).days >= window_days * 2:
            return skip("missing_benchmark")
        return skip("window_not_mature", pending=True)
    days = calendar[:window_days]
    if any(day not in benchmark for day in days):
        return skip("missing_benchmark")
    prices = _index(price_series)
    if any(day not in prices for day in days):
        return skip("missing_future_price")
    entry = number(prices[days[0]].get("open"))
    benchmark_entry = number(benchmark[days[0]].get("open"))
    if entry is None or entry <= 0:
        return skip("missing_entry_open")
    if benchmark_entry is None or benchmark_entry <= 0:
        return skip("missing_benchmark_entry_open")
    for series in (prices, benchmark):
        for day in days:
            row = series[day]
            if row.get("price_basis") != RESEARCH_PRICE_BASIS:
                return skip("price_basis_mismatch")
            if not row.get("price_source"):
                return skip("price_provenance_missing")
            if row.get("corporate_actions_checked") is not True:
                return skip("corporate_action_provenance_missing")
            if row.get("ohlc_conflict_fields"):
                return skip("conflicting_price_records")
            split = number(row.get("stock_splits"))
            if split is None or number(row.get("dividends")) is None:
                return skip("corporate_action_provenance_missing")
            if split not in (0, 1):
                return skip("stock_split_in_window")
            high, low, close = (number(row.get(key)) for key in ("high", "low", "close"))
            open_ = number(row.get("open"))
            if (high is None or low is None or close is None or min(high, low, close) <= 0
                    or low > close or high < close or low > high
                    or (open_ is not None and (open_ <= 0 or not low <= open_ <= high))):
                return skip("invalid_research_ohlc")
    target = prices[days[-1]]["close"]
    gross = pct_return(entry, target)
    benchmark_return = pct_return(benchmark_entry, benchmark[days[-1]]["close"])
    payload = {
        "return_basis": "next_open", "price_basis": RESEARCH_PRICE_BASIS,
        "entry_date": days[0].isoformat(), "entry_price": entry,
        "target_date": days[-1].isoformat(), "target_price": target,
        "forward_return_pct": gross, "benchmark_return_pct": benchmark_return,
        "excess_return_vs_benchmark_pct": round(gross - benchmark_return, 4),
        "max_adverse_excursion_pct": min(0.0, pct_return(entry, min(prices[d]["low"] for d in days))),
        "max_favorable_excursion_pct": max(0.0, pct_return(entry, max(prices[d]["high"] for d in days))),
        "dividends_included": False,
        "cash_dividends_excluded": sum(prices[d]["dividends"] for d in days),
        "price_sources": sorted({prices[d]["price_source"] for d in days}),
        "benchmark_price_sources": sorted({benchmark[d]["price_source"] for d in days}),
        "cost_model": "assumed_total_cost_percentage_points",
        "cost_scenarios": {format(cost, "g"): {
            "assumed_cost_pct": cost, "net_return_pct": round(gross - cost, 4),
            "net_excess_return_pct": round(gross - cost - benchmark_return, 4),
        } for cost in RESEARCH_COSTS},
    }
    return base | {"status": "validated", "target_date": days[-1].isoformat(),
                   "skip_reason": None, "outcome": payload}


def run_research_validation(session, request, *, as_of_date: date, price_provider):
    """Fetch only pending symbols in bounded batches; commit belongs to the router."""
    windows = ordered_positive_values(request.windows)
    start = request.start_date
    if request.mode == "due" and start is None:
        start = default_due_start_date(as_of_date, max(windows))
    candidates = forward_validation_candidates_from_runs(
        session, market=request.market, start_date=start, end_date=request.end_date or as_of_date)
    candidates = [c for c in candidates if c["selection_status"] == "selected"
                  or (c["selection_status"] == "shadow" and c["shadow_cohort"] == "comparable")]
    validate_forward_validation_benchmark(candidates, benchmark_symbol=request.benchmark_symbol)
    pending = {candidate_key(c): windows for c in candidates}
    if request.mode == "due":
        pending = exclude_persisted_daily_radar_windows(
            session, pending, validation_version=RESEARCH_VALIDATION_VERSION,
            benchmark_symbol=request.benchmark_symbol, terminal_skip_reasons=RESEARCH_TERMINAL_REASONS)
    active = [c for c in candidates if pending.get(candidate_key(c))]
    benchmark = []
    outcomes = []
    if active:
        price_start = min(parse_date(c["record_date"]) for c in active)
        benchmark = list(price_provider.fetch(
            [request.benchmark_symbol], start_date=price_start, end_date=as_of_date
        ).get(request.benchmark_symbol, []))
        reference = load_benchmark_prices_from_prepared_market_context(
            session, market=request.market, benchmark_symbol=request.benchmark_symbol,
            as_of_date=as_of_date, required_dates=[parse_date(c["record_date"]) for c in active])
        reference_days = sorted(day for day in _index(reference) if day <= as_of_date)
        calendar = sorted(day for day in _index(benchmark) if day <= as_of_date)
        by_symbol = {}
        for candidate in active:
            signal = parse_date(candidate["record_date"])
            reference_usable = signal in reference_days and reference_days[-1] >= max(calendar, default=signal)
            candidate_calendar = reference_days if reference_usable else calendar
            due = [w for w in pending[candidate_key(candidate)]
                   if sum(day > signal for day in candidate_calendar) >= w
                   or (as_of_date - signal).days >= w * 2]
            if due:
                by_symbol.setdefault(candidate["symbol"], []).append((candidate, due, reference_days if reference_usable else None))
        symbols = sorted(by_symbol)
        for offset in range(0, len(symbols), FORWARD_PRICE_FETCH_BATCH_SIZE):
            batch = symbols[offset:offset + FORWARD_PRICE_FETCH_BATCH_SIZE]
            batch_start = min(parse_date(c["record_date"]) for s in batch for c, _, _ in by_symbol[s])
            prices = price_provider.fetch(batch, start_date=batch_start, end_date=as_of_date)
            for symbol in batch:
                for candidate, due, reference_calendar in by_symbol[symbol]:
                    for window in due:
                        row = evaluate_research_window(
                            candidate, price_series=prices.get(symbol, []), benchmark_prices=benchmark,
                            window_days=window, as_of_date=as_of_date, benchmark_symbol=request.benchmark_symbol,
                            trading_calendar=reference_calendar)
                        if row["status"] != "pending":
                            outcomes.append(row)
    summary = upsert_forward_validation_results(session, outcomes)
    terminal = sum(r["status"] == "skipped" and r["skip_reason"] in RESEARCH_TERMINAL_REASONS for r in outcomes)
    summary.update(terminal_skipped_count=terminal, retryable_skipped_count=summary["skipped_count"] - terminal)
    persisted = persisted_forward_validation_outcomes(
        session, candidates, windows=windows, as_of_date=as_of_date,
        validation_version=RESEARCH_VALIDATION_VERSION)
    report = {"validation_version": RESEARCH_VALIDATION_VERSION, "return_basis": "next_open",
              "price_basis": RESEARCH_PRICE_BASIS, "dividends_included": False,
              "windows": windows, "as_of_date": as_of_date.isoformat(),
              "aggregation_scope": "persisted_fixed_date_cohort",
              "persisted_count": len(persisted),
              "skip_reasons": dict(Counter(r["skip_reason"] for r in persisted if r["status"] == "skipped"))}
    return {"status": "completed", "mode": request.mode, "market": request.market,
            "as_of_date": as_of_date, "candidate_count": len(candidates), **summary, "report": report}
