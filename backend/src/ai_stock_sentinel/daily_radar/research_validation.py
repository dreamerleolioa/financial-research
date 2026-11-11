"""Versioned next-open price research, isolated from legacy close-based results."""
from collections import Counter
from datetime import date
from collections import defaultdict
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased
from ai_stock_sentinel.calibration import twse_rights_recovery as rights
from ai_stock_sentinel.calibration import official_corporate_actions as corporate_actions
from ai_stock_sentinel.calibration.repository import load_benchmark_calendar_from_prepared_market_context
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult, DailyRadarCandidate, DailyRadarRun
from ai_stock_sentinel.taiwan_symbols import taiwan_stock_symbol_predicate
from ai_stock_sentinel.daily_radar.observation_validation import canonical_radar_run_ids

from ai_stock_sentinel.calibration.forward_validation import (
    candidate_key, merge_price_series, number, ordered_positive_values, parse_date,
    pct_return, TERMINAL_FORWARD_VALIDATION_SKIP_REASONS,
)
from ai_stock_sentinel.calibration.forward_validation_planning import FORWARD_PRICE_FETCH_BATCH_SIZE
from ai_stock_sentinel.daily_radar.forward_validation import (
    default_due_start_date, exclude_persisted_daily_radar_windows,
    forward_validation_candidates_from_runs, persisted_forward_validation_outcomes,
    load_benchmark_prices_from_prepared_market_context,
    validate_forward_validation_benchmark,
)


RESEARCH_VALIDATION_VERSION = rights.RESEARCH_VERSION
RESEARCH_PRICE_BASIS = "unadjusted_price"
RESEARCH_COSTS = (0.0, 0.5, 1.0)
RESEARCH_TERMINAL_REASONS = TERMINAL_FORWARD_VALIDATION_SKIP_REASONS | {
    "stock_split_in_window", "candidate_price_date_missing", "future_candidate_price",
} | corporate_actions.TERMINAL_REASONS
RESEARCH_RESUME_BATCH_SIZE = 512
RESEARCH_RESUME_VERSIONS = ("daily-radar-next-open-price-v1", RESEARCH_VALIDATION_VERSION)


def _candidate_price_date_reason(candidate, signal):
    observed = parse_date((candidate.get("data_dates") or {}).get("ohlcv"))
    if observed is None:
        return "candidate_price_date_missing"
    if observed < signal:
        return "stale_candidate_price"
    if observed > signal:
        return "future_candidate_price"
    return None


def _retry_calendar(session, request, as_of_date):
    if request.market != "TW" or request.benchmark_symbol != "TAIEX":
        return None
    from ai_stock_sentinel.daily_radar.trading_calendar import CalendarEvidenceIndex, stored_calendars
    return CalendarEvidenceIndex(stored_calendars(session, as_of_date=as_of_date)).latest()


def _retryable_windows_query(request, as_of_date, *, calendar=None):
    """Retire a legacy hint once v2 owns the same candidate/window identity."""
    end_date = min(request.end_date or as_of_date, as_of_date)
    current = aliased(DailyRadarForwardValidationResult)
    current_exists = select(current.id).where(
        current.candidate_id == DailyRadarForwardValidationResult.candidate_id,
        current.window_days == DailyRadarForwardValidationResult.window_days,
        current.validation_version == RESEARCH_VALIDATION_VERSION,
        current.signal_date == DailyRadarForwardValidationResult.signal_date,
        current.benchmark_symbol == DailyRadarForwardValidationResult.benchmark_symbol,
        or_(and_(current.status == "validated", current.target_date <= as_of_date),
            and_(current.status == "skipped", or_(current.evaluation_as_of_date.is_(None),
                                                 current.evaluation_as_of_date <= as_of_date))),
    ).correlate(DailyRadarForwardValidationResult).exists()
    query = select(DailyRadarForwardValidationResult.candidate_id,
                   DailyRadarForwardValidationResult.window_days).join(
        DailyRadarCandidate, DailyRadarCandidate.id == DailyRadarForwardValidationResult.candidate_id
    ).join(DailyRadarRun, DailyRadarRun.id == DailyRadarCandidate.run_id).where(
        DailyRadarForwardValidationResult.validation_version.in_(RESEARCH_RESUME_VERSIONS),
        or_(DailyRadarForwardValidationResult.validation_version == RESEARCH_VALIDATION_VERSION,
            ~current_exists),
        DailyRadarForwardValidationResult.status == "skipped",
        DailyRadarForwardValidationResult.skip_reason.not_in(RESEARCH_TERMINAL_REASONS),
        DailyRadarForwardValidationResult.window_days.in_(ordered_positive_values(request.windows)),
        DailyRadarForwardValidationResult.signal_date <= end_date,
        DailyRadarRun.market == request.market,
        DailyRadarRun.id.in_(canonical_radar_run_ids(market=request.market, end_date=end_date)),
        DailyRadarForwardValidationResult.benchmark_symbol == request.benchmark_symbol,
        DailyRadarForwardValidationResult.signal_date == DailyRadarRun.run_date,
        or_(DailyRadarCandidate.selection_status == "selected",
            (DailyRadarCandidate.selection_status == "shadow") & (DailyRadarCandidate.shadow_cohort == "comparable")),
        taiwan_stock_symbol_predicate(DailyRadarCandidate.symbol) if request.market == "TW" else True,
    )
    if request.start_date is not None:
        query = query.where(DailyRadarForwardValidationResult.signal_date >= request.start_date)
    if request.conflict_recovery_symbols is not None:
        # A later retry of the same conflict does not change the historical price window.
        return query.where(
            DailyRadarCandidate.symbol.in_(request.conflict_recovery_symbols),
            DailyRadarForwardValidationResult.signal_date == request.start_date,
            DailyRadarForwardValidationResult.validation_version == RESEARCH_VALIDATION_VERSION,
            DailyRadarForwardValidationResult.skip_reason == rights.CONFLICT_REASON,
            DailyRadarForwardValidationResult.evaluation_as_of_date >= as_of_date,
            DailyRadarForwardValidationResult.outcome["expected_target_date"].as_string()
            == as_of_date.isoformat(),
        )
    query = query.where(or_(DailyRadarForwardValidationResult.evaluation_as_of_date.is_(None),
                           DailyRadarForwardValidationResult.evaluation_as_of_date <= as_of_date))
    if (calendar is not None and calendar.through >= as_of_date
            and request.market == "TW" and request.benchmark_symbol == "TAIEX"):
        sessions = [day for day in calendar.sessions if day <= as_of_date]
        # A signal on one of the last N sessions has fewer than N future
        # sessions. Keep the signal in the proof and filter before LIMIT so
        # pending observations cannot occupy the bounded recovery queue.
        immature = [and_(DailyRadarForwardValidationResult.window_days == window,
            DailyRadarForwardValidationResult.signal_date.in_(sessions[-window:]))
            for window in ordered_positive_values(request.windows)]
        if immature:
            query = query.where(~and_(DailyRadarForwardValidationResult.skip_reason == "window_not_mature",
                                      or_(*immature)))
    return query


def _index(rows):
    # Use the existing conflict-preserving merger, without its open-price fallback.
    merged = merge_price_series({"rows": rows}, {})["rows"]
    return {parse_date(row["date"]): row for row in merged}


def evaluate_research_window(candidate, *, price_series, benchmark_prices,
                             window_days, as_of_date, benchmark_symbol, trading_calendar=None, validate_signal_session=False):
    from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
    signal = parse_date(candidate.get("record_date"))
    base = {"candidate_id": candidate.get("candidate_id"), "symbol": candidate.get("symbol"),
            "signal_date": signal.isoformat() if signal else None, "window_days": window_days,
            "validation_version": RESEARCH_VALIDATION_VERSION, "benchmark_symbol": benchmark_symbol,
            "evaluation_as_of_date": as_of_date.isoformat()}

    def skip(reason, *, pending=False, missing_dates=(), target_date=None):
        return bind_validation_input(candidate, base | {"status": "pending" if pending else "skipped", "target_date": None,
                       "skip_reason": reason, "outcome": {"return_basis": "next_open", "price_basis": RESEARCH_PRICE_BASIS,
                       "missing_dates": [str(d) for d in missing_dates],
                       "expected_target_date": str(target_date) if target_date else None}})

    if signal is None:
        return skip("signal_date_missing")
    if signal > as_of_date:
        return skip("future_signal_date")
    if reason := _candidate_price_date_reason(candidate, signal):
        return skip(reason)
    benchmark = _index(benchmark_prices)
    calendar = sorted(day for day, row in benchmark.items()
                      if signal < day <= as_of_date and (number(row.get("close")) or 0) > 0)
    if trading_calendar is not None:
        if validate_signal_session and signal not in trading_calendar:
            return skip("non_trading_signal_date")
        calendar = sorted({day for day in trading_calendar if signal < day <= as_of_date})
    if len(calendar) < window_days:
        if signal not in benchmark:
            return skip("missing_benchmark", missing_dates=[signal])
        # Beyond a conservative calendar bound, an incomplete index history is
        # missing evidence, not proof that the observation is still immature.
        if (as_of_date - signal).days >= window_days * 2:
            return skip("missing_benchmark")
        return skip("window_not_mature", pending=True)
    days = calendar[:window_days]
    prices = _index(price_series)
    missing_dates = [day for day in days if day not in prices]
    missing_benchmark_dates = [day for day in days if day not in benchmark]
    if any(series.get(day, {}).get("ohlc_conflict_fields") for series in (prices, benchmark) for day in days):
        unavailable = set(missing_dates + missing_benchmark_dates)
        if signal not in benchmark:
            unavailable.add(signal)
        return skip("conflicting_price_records", missing_dates=sorted(unavailable), target_date=days[-1])
    if signal not in benchmark:
        return skip("missing_benchmark", missing_dates=[signal])
    if missing_benchmark_dates:
        return skip("missing_benchmark", missing_dates=missing_benchmark_dates, target_date=days[-1])
    if missing_dates:
        return skip("missing_future_price", missing_dates=missing_dates, target_date=days[-1])
    entry = number(prices[days[0]].get("open"))
    benchmark_entry = number(benchmark[days[0]].get("open"))
    if entry is None or entry <= 0:
        return skip("missing_entry_open")
    if benchmark_entry is None or benchmark_entry <= 0:
        return skip("missing_benchmark_entry_open")
    split_event = None
    for series in (prices, benchmark):
        for day in days:
            row = series[day]
            if row.get("price_basis") != RESEARCH_PRICE_BASIS:
                return skip("price_basis_mismatch")
            if not row.get("price_source"):
                return skip("price_provenance_missing")
            if row.get("corporate_actions_checked") is not True:
                return skip("corporate_action_provenance_missing")
            split = number(row.get("stock_splits"))
            if split is None or number(row.get("dividends")) is None:
                return skip("corporate_action_provenance_missing")
            if split not in (0, 1):
                split_event = {"date": str(day), "ratio": split,
                    "symbol": candidate["symbol"] if series is prices else benchmark_symbol,
                    "price_source": row["price_source"], "corporate_actions_checked": True,
                    "corporate_action_source": row.get("corporate_action_source"),
                    "corporate_action_coverage": row.get("corporate_action_coverage")}
            high, low, close = (number(row.get(key)) for key in ("high", "low", "close"))
            open_ = number(row.get("open"))
            if (high is None or low is None or close is None or min(high, low, close) <= 0
                    or low > close or high < close or low > high
                    or (open_ is not None and (open_ <= 0 or not low <= open_ <= high))):
                return skip("invalid_research_ohlc")
    if split_event is not None:
        row = skip("stock_split_in_window", target_date=days[-1])
        row["outcome"].update(session_dates=[str(d) for d in days], split_event=split_event)
        return row
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
        "corporate_action_sources": sorted({prices[d].get("corporate_action_source", prices[d]["price_source"]) for d in days}),
        "recovery_provenance": [{"date": str(d), "price_source": prices[d]["price_source"],
            "corporate_action_source": prices[d]["corporate_action_source"],
            "corporate_action_coverage": {k: v for k, v in (prices[d].get("corporate_action_coverage") or {}).items()
                                          if k not in {"events", "covered_dates"}}}
            for d in days if prices[d].get("corporate_action_source")],
        "cost_model": "assumed_total_cost_percentage_points",
        "cost_scenarios": {format(cost, "g"): {
            "assumed_cost_pct": cost, "net_return_pct": round(gross - cost, 4),
            "net_excess_return_pct": round(gross - cost - benchmark_return, 4),
        } for cost in RESEARCH_COSTS},
    }
    from ai_stock_sentinel.daily_radar.window_identity import bind_window
    return bind_window(bind_validation_input(candidate, base | {"status": "validated", "target_date": days[-1].isoformat(),
                   "skip_reason": None, "outcome": payload}), days, 'next_open')


def _recover_rights_window(candidate, row, yahoo, marked, benchmark, calendar, *, as_of_date, scoped, provider,
                           reviewed=False):
    symbol = candidate["symbol"]
    signal = parse_date(candidate["record_date"])
    window = row["window_days"]
    sessions = [day for day in calendar if signal < day <= as_of_date][:window]
    target = as_of_date if scoped else parse_date(row["outcome"].get("expected_target_date"))
    capture = getattr(provider, "capture_twse_report", None)
    load_history = getattr(provider, "official_research_history", None)
    if len(sessions) != window or sessions[-1] != target or not callable(capture) or not callable(load_history):
        return None
    dates = {day.isoformat() for day in sessions}
    flags = {r["date"] for r in marked if r["date"] in dates and r.get("ohlc_conflict_fields")}
    if not scoped and not flags:
        return None
    try:
        verifier = rights.verify_reviewed_window if reviewed else rights.recover_window
        prices, index, evidence = verifier(
            symbol, signal, sessions, yahoo_rows=yahoo, benchmark_rows=benchmark,
            flagged_dates=flags, capture=capture, load_history=load_history,
        )
        result = evaluate_research_window(
            candidate, price_series=prices, benchmark_prices=index, window_days=window,
            as_of_date=as_of_date, benchmark_symbol="TAIEX", trading_calendar=sessions,
        )
        if result["status"] != "validated":
            raise ValueError("twse_rights_window_evaluation_failed")
    except Exception as exc:
        reason = str(exc)
        provider.diagnostics.append({
            "symbol": symbol, "stage": rights.PROVENANCE_KEY, "target_date": target.isoformat(),
            "error_type": type(exc).__name__,
            "reason": reason if reason.startswith(("twse_rights_", "research_", "official_research_"))
            else type(exc).__name__,
        })
        return None
    result["outcome"][rights.PROVENANCE_KEY] = evidence
    return result


def _classify_corporate_action(row, yahoo, marked, benchmark, *, as_of_date, provider):
    target = parse_date(row["outcome"].get("expected_target_date"))
    signal = parse_date(row["signal_date"])
    if target is None or not yahoo:
        return None
    symbol = row["symbol"]
    try:
        proof = corporate_actions.prove_window(
            symbol, signal, row["window_days"], as_of_date, yahoo_rows=yahoo, benchmark_rows=benchmark,
            marked_rows=marked, provider=provider,
        )
    except Exception as exc:
        # An unavailable, incomplete or contradictory report preserves the original gap.
        reason = str(exc)
        getattr(provider, "diagnostics", []).append({
            "symbol": symbol, "stage": corporate_actions.PROVENANCE_KEY, "target_date": str(target),
            "error_type": type(exc).__name__,
            "reason": reason if reason.startswith(("official_", "twse_rights_", "research_"))
            else type(exc).__name__,
        })
        return None
    if proof is None:
        return None
    reason, evidence = proof
    verified_target = evidence["session_dates"][-1]
    if verified_target != str(target):
        evidence["prior_expected_target_date"] = str(target)
    if row["outcome"].get("missing_dates"):
        evidence["prior_missing_dates"] = row["outcome"]["missing_dates"]
    return row | {"skip_reason": reason, "target_date": verified_target, "outcome": row["outcome"] | {
        "expected_target_date": verified_target,
        "missing_dates": [],
        corporate_actions.PROVENANCE_KEY: evidence,
    }}


def run_research_validation(session, request, *, as_of_date: date, price_provider, calendar_provider=None,
                            candidate_ids=None, prepare_only=False):
    """Bounded, resumable research. The router owns the single transaction."""
    windows = ordered_positive_values(request.windows)
    scoped = request.conflict_recovery_symbols is not None
    start = request.start_date
    resume = defaultdict(set)
    retryable_query = _retryable_windows_query(request, as_of_date,
        calendar=_retry_calendar(session, request, as_of_date))
    if request.mode == "due":
        if start is None:
            start = default_due_start_date(as_of_date, max(windows))
        # Do not lose unresolved observations as the default date window moves.
        # Explicit caller date bounds remain authoritative.
        query = retryable_query.order_by(DailyRadarForwardValidationResult.updated_at,
                   DailyRadarForwardValidationResult.signal_date,
                   DailyRadarForwardValidationResult.candidate_id,
                   DailyRadarForwardValidationResult.window_days).limit(RESEARCH_RESUME_BATCH_SIZE)
        for cid, window in session.execute(query):
            resume[cid].add(window)
    candidates = forward_validation_candidates_from_runs(
        session, market=request.market, start_date=start, end_date=request.end_date or as_of_date,
        include_candidate_ids=list(resume), symbols=request.conflict_recovery_symbols,
        candidate_ids=candidate_ids)
    candidates = [c for c in candidates if c["selection_status"] == "selected"
                  or (c["selection_status"] == "shadow" and c["shadow_cohort"] == "comparable")]
    if candidate_ids is not None:
        candidates = [c for c in candidates if c["candidate_id"] in candidate_ids]
        if {c["candidate_id"] for c in candidates} != set(candidate_ids):
            raise ValueError("validation_candidate_scope_unavailable")
    validate_forward_validation_benchmark(candidates, benchmark_symbol=request.benchmark_symbol)
    from ai_stock_sentinel.daily_radar.validation_preparation import capture_validation_acceptance
    acceptance = None if prepare_only else capture_validation_acceptance(session, candidates,
        market=request.market, windows=windows, validation_version=RESEARCH_VALIDATION_VERSION)
    official_calendar = None
    if candidates and request.market == 'TW' and request.benchmark_symbol == 'TAIEX':
        from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar, require_calendar
        first_signal = min(parse_date(c['record_date']) for c in candidates)
        official_calendar = load_calendar(session, start_date=first_signal, as_of_date=as_of_date)
        missing_coverage = official_calendar is None or official_calendar.through < as_of_date
        if calendar_provider is not None and (missing_coverage or request.mode != 'due' or scoped):
            session.rollback()  # The router-owned collection transaction ends before source I/O.
            official_calendar = require_calendar(calendar_provider, start_date=first_signal, end_date=as_of_date)
        elif missing_coverage:
            official_calendar = None
    pending = {candidate_key(c): (sorted(resume[c["candidate_id"]])
               if scoped or (start and parse_date(c["record_date"]) < start) else windows) for c in candidates}
    if request.mode == "due":
        pending = exclude_persisted_daily_radar_windows(
            session, pending, validation_version=RESEARCH_VALIDATION_VERSION,
            benchmark_symbol=request.benchmark_symbol, terminal_skip_reasons=RESEARCH_TERMINAL_REASONS,
            calendar=official_calendar)
    active = [c for c in candidates if pending.get(candidate_key(c))]
    outcomes = []
    recovery_checks = []
    can_prove_actions = all(callable(getattr(price_provider, name, None)) for name in
                            ("official_research_history", "capture_twse_report", "capture_tpex_report"))
    fresh = []
    for candidate in active:
        signal = parse_date(candidate["record_date"])
        non_trading_signal = official_calendar is not None and signal not in official_calendar.sessions
        if _candidate_price_date_reason(candidate, signal) or non_trading_signal:
            if not scoped:
                outcomes.extend(evaluate_research_window(candidate, price_series=[], benchmark_prices=[],
                    window_days=w, as_of_date=as_of_date, benchmark_symbol=request.benchmark_symbol,
                    trading_calendar=official_calendar.sessions if official_calendar else None,
                    validate_signal_session=official_calendar is not None)
                    for w in pending[candidate_key(candidate)])
        else:
            fresh.append(candidate)
    if official_calendar is not None:
        due_candidates = []
        for candidate in fresh:
            signal = parse_date(candidate["record_date"])
            future_count = sum(signal < day <= as_of_date for day in official_calendar.sessions)
            if future_count >= min(pending[candidate_key(candidate)]):
                due_candidates.append(candidate)
        fresh = due_candidates
    if fresh:
        price_start = min(parse_date(c["record_date"]) for c in fresh)
        reference = load_benchmark_prices_from_prepared_market_context(
            session, market=request.market, benchmark_symbol=request.benchmark_symbol,
            as_of_date=as_of_date, required_dates=[parse_date(c["record_date"]) for c in fresh])
        reference_days = sorted(set(load_benchmark_calendar_from_prepared_market_context(
            session, market=request.market, benchmark_symbol=request.benchmark_symbol,
            start_date=price_start, as_of_date=as_of_date)) |
            {day for day in _index(reference) if price_start <= day <= as_of_date})
        if official_calendar is not None:
            reference_days = [day for day in official_calendar.sessions if price_start <= day <= as_of_date]
        session.rollback()  # Pending-window/reference reads may have opened a new collection transaction.
        benchmark = list(price_provider.fetch(
            [request.benchmark_symbol], start_date=price_start, end_date=as_of_date
        ).get(request.benchmark_symbol, []))
        repair = getattr(price_provider, "repair", None)
        if callable(repair):
            required = sorted(set(reference_days) | {price_start, as_of_date})
            benchmark = repair({request.benchmark_symbol: benchmark},
                required_dates_by_symbol={request.benchmark_symbol: required},
                as_of_date=as_of_date).get(request.benchmark_symbol, [])
        calendar = sorted(day for day in _index(benchmark) if day <= as_of_date)
        # Both sources can prove a session. Never drop a real provider session
        # merely because an older prepared snapshot has an internal hole.
        complete_calendar = (list(official_calendar.sessions) if official_calendar is not None
                             else sorted(set(reference_days) | set(calendar)))
        by_symbol = defaultdict(list)
        for candidate in fresh:
            signal = parse_date(candidate["record_date"])
            candidate_calendar = complete_calendar
            due = [w for w in pending[candidate_key(candidate)]
                   if sum(day > signal for day in candidate_calendar) >= w
                   or (official_calendar is None and (as_of_date - signal).days >= w * 2)]
            if due:
                by_symbol[candidate["symbol"]].append((candidate, due, candidate_calendar))
        symbols = sorted(by_symbol)
        for offset in range(0, len(symbols), FORWARD_PRICE_FETCH_BATCH_SIZE):
            batch = symbols[offset:offset + FORWARD_PRICE_FETCH_BATCH_SIZE]
            batch_start = min(parse_date(c["record_date"]) for s in batch for c, _, _ in by_symbol[s])
            yahoo = price_provider.fetch(batch, start_date=batch_start, end_date=as_of_date)
            prices = yahoo
            required_by_symbol = {}
            for symbol in batch:
                required = set()
                for candidate, due, candidate_calendar in by_symbol[symbol]:
                    future = [d for d in candidate_calendar if d > parse_date(candidate["record_date"])]
                    required.update(future[:max(due)])
                required_by_symbol[symbol] = sorted(required)
            if callable(repair):
                prices = repair(prices, required_dates_by_symbol=required_by_symbol, as_of_date=as_of_date)
            for symbol in batch:
                for candidate, due, candidate_calendar in by_symbol[symbol]:
                    for window in due:
                        row = evaluate_research_window(
                            candidate, price_series=prices.get(symbol, []), benchmark_prices=benchmark,
                            window_days=window, as_of_date=as_of_date, benchmark_symbol=request.benchmark_symbol,
                            trading_calendar=candidate_calendar, validate_signal_session=official_calendar is not None)
                        if scoped or (request.market == "TW" and request.benchmark_symbol == "TAIEX" and (
                                (symbol.endswith(".TW") and row["skip_reason"] == rights.CONFLICT_REASON)
                                or (can_prove_actions and row["skip_reason"] in
                                    {rights.CONFLICT_REASON, "missing_future_price"}))):
                            recovery_checks.append((candidate, row, yahoo.get(symbol, []), prices.get(symbol, []),
                                                    benchmark, candidate_calendar))
                        elif row["status"] != "pending":
                            outcomes.append(row)
    # Ordinary price recovery and the existing cash-rights path keep priority in the shared budget.
    unclassified = []
    for check in recovery_checks:
        _, row, yahoo, marked, benchmark, _ = check
        result = None
        if scoped or (row["symbol"].endswith(".TW") and row["skip_reason"] == rights.CONFLICT_REASON):
            result = _recover_rights_window(*check, as_of_date=as_of_date, scoped=scoped, provider=price_provider)
        if result is not None:
            outcomes.append(result)
        elif not scoped:
            unclassified.append((row, yahoo, marked, benchmark))
    for row, yahoo, marked, benchmark in unclassified:
        resolved = _classify_corporate_action(row, yahoo, marked, benchmark, as_of_date=as_of_date,
                                              provider=price_provider) if can_prove_actions else None
        outcomes.append(resolved if resolved is not None else row)
    from ai_stock_sentinel.daily_radar.validation_preparation import ValidationPreparation, accept_validation_results
    if prepare_only:
        return ValidationPreparation(candidates=candidates, outcomes=outcomes,
            calendar=official_calendar,
            as_of_date=as_of_date, return_basis="next_open", diagnostics=list(getattr(price_provider, "diagnostics", [])))
    summary = accept_validation_results(session, outcomes, calendar=official_calendar, acceptance=acceptance,
        terminal_skip_reasons=RESEARCH_TERMINAL_REASONS, require_existing_conflict=scoped)
    # Acceptance may have persisted the first current calendar for this cutoff.
    # Re-read proof eligibility and use the same query as bounded discovery.
    remaining_query = _retryable_windows_query(request, as_of_date,
        calendar=_retry_calendar(session, request, as_of_date))
    remaining_retryable = session.scalar(select(func.count()).select_from(remaining_query.subquery())) or 0
    persisted = persisted_forward_validation_outcomes(
        session, candidates, windows=windows, as_of_date=as_of_date,
        validation_version=RESEARCH_VALIDATION_VERSION)
    recovery = list(getattr(price_provider, "diagnostics", []))
    recovery_errors = [row for row in recovery if row.get("error_type")]
    recovery_successes = [row for row in recovery if not row.get("error_type")]
    reported_recovery = (recovery_errors + recovery_successes)[:64]
    report = {"validation_version": RESEARCH_VALIDATION_VERSION, "return_basis": "next_open",
              "price_basis": RESEARCH_PRICE_BASIS, "dividends_included": False,
              "windows": windows, "as_of_date": as_of_date.isoformat(),
              "aggregation_scope": "persisted_fixed_date_cohort", "instrument_scope": "individual_stocks",
              "persisted_count": len(persisted),
              "remaining_retryable_window_count": remaining_retryable,
              "resumed_candidate_count": sum(parse_date(c["record_date"]) < start for c in candidates) if start else 0,
              "current_run_skip_reasons": dict(Counter(r["skip_reason"] for r in outcomes if r["status"] == "skipped")),
              "current_run_gaps": [{"symbol": r["symbol"], "signal_date": r["signal_date"],
                  "window_days": r["window_days"], "reason": r["skip_reason"], **r["outcome"]}
                  for r in outcomes if r["status"] == "skipped" and r["skip_reason"] not in RESEARCH_TERMINAL_REASONS][:50],
              "source_recovery": reported_recovery,
              "source_recovery_summary": {
                  "total_count": len(recovery), "reported_count": len(reported_recovery),
                  "truncated": len(recovery) > len(reported_recovery),
                  "error_count": len(recovery_errors),
                  "error_reasons": dict(Counter(row.get("reason") or row["error_type"] for row in recovery_errors)),
                  "request_count": getattr(price_provider, "request_count", None),
                  "max_requests": getattr(price_provider, "max_requests", None),
                  "total_timeout_seconds": getattr(price_provider, "total_timeout", None)},
              "skip_reasons": dict(Counter(r["skip_reason"] for r in persisted if r["status"] == "skipped"))}
    if scoped:
        report["aggregation_scope"] = "explicit_conflict_windows"
        verified = [
            {"symbol": row["symbol"], "target_date": row["target_date"],
             "evidence": row["outcome"][rights.PROVENANCE_KEY]}
            for row in persisted if row["status"] == "validated"
            and row["target_date"] == as_of_date.isoformat()
            and rights.PROVENANCE_KEY in row["outcome"]
        ]
        unverified = sorted(set(request.conflict_recovery_symbols) - {row["symbol"] for row in verified})
        report["conflict_recovery"] = {
            "ready": not unverified, "verified_windows": verified, "unverified_symbols": unverified,
        }
    return {"status": "completed", "mode": request.mode, "market": request.market,
            "as_of_date": as_of_date, "candidate_count": len(candidates), **summary, "report": report}
