"""Shared signal-close execution with a read-only collection seam for durable work."""
from collections import Counter, defaultdict
from datetime import date
import logging
from fastapi import HTTPException
from ai_stock_sentinel.calibration.forward_validation_planning import prepare_due_forward_validation
from ai_stock_sentinel.calibration.price_provider import ForwardPriceUnavailableError
from ai_stock_sentinel.calibration.runtime_metrics import ForwardValidationMetrics
from ai_stock_sentinel.daily_radar.presenter import parse_date
from ai_stock_sentinel.daily_radar.forward_validation import (
    DAILY_RADAR_FORWARD_ADAPTER, DEFAULT_FORWARD_WINDOWS, build_forward_validation_report,
    build_forward_validation_report_from_outcomes, default_due_start_date, due_windows_by_candidate,
    exclude_persisted_daily_radar_windows, forward_validation_candidates_from_runs,
    load_benchmark_prices_from_prepared_market_context, load_price_series_from_raw_data,
    persisted_forward_validation_outcomes, validate_forward_validation_benchmark,
    FORWARD_VALIDATION_VERSION,
)
from ai_stock_sentinel.daily_radar.trading_calendar import require_calendar
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunResponse
from ai_stock_sentinel.daily_radar.price_acceptance import AcceptedPrices, accept_signal_close_prices, refused_prices
from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
from ai_stock_sentinel.daily_radar.validation_preparation import (
    ValidationPreparation, accept_validation_results, capture_validation_acceptance,
)

logger = logging.getLogger("ai_stock_sentinel.daily_radar.maintenance_router")


def _collect_locked_signal_prices(candidates, windows_by_candidate, *, calendar, request,
                                  collect_official, fetch_prices, collect_targets=None, fetch_cached=None, recover_basis=None, load_snapshot=None):
    from ai_stock_sentinel.calibration.forward_validation import evaluate_forward_window
    from ai_stock_sentinel.daily_radar.forward_validation import _with_market_lifecycle_diagnostics
    from ai_stock_sentinel.daily_radar.observation_validation import evaluate_observation
    from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
    from ai_stock_sentinel.daily_radar.window_identity import bind_window
    from ai_stock_sentinel.daily_radar.trading_status import proven_suspension, suspended_outcome
    from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    from yfinance.exceptions import YFRateLimitError

    by_symbol = defaultdict(list)
    for candidate in candidates:
        signal = date.fromisoformat(candidate["record_date"])
        days = [day for day in calendar.sessions if day > signal]
        windows = (request.windows if windows_by_candidate is None else
                   windows_by_candidate.get(f'id:{candidate["candidate_id"]}', []))
        for window in windows:
            if len(days) >= window:
                by_symbol[candidate["symbol"]].append((candidate, [signal, *days[:window]]))

    outcomes = []
    for symbol, windows in by_symbol.items():
        start = min(days[0] for _, days in windows)
        end = max(days[-1] for _, days in windows)
        targets = sorted({days[-1] for _, days in windows})
        official_by_target = (collect_targets(symbol, start, targets) if collect_targets else
                              {target: collect_official(symbol, start, target) for target in targets})
        tradable_targets = [t for t in targets if not proven_suspension(official_by_target[t], symbol, t)]
        adjusted, price_unavailable = [], False
        if tradable_targets:
            try:
                adjusted = fetch_prices([symbol], start_date=start, end_date=max(tradable_targets)).get(symbol, [])
            except (SourceDependencyBusy, OSError, YFRateLimitError):
                price_unavailable = True
        for candidate, days in windows:
            official = official_by_target[days[-1]]
            session_dates = set(map(str, days))
            window_prices = [row for row in adjusted if row.get("date") in session_dates]
            missing_adjusted = not window_prices and days[-1] in tradable_targets
            if missing_adjusted and fetch_cached and (days[0], days[-1]) != (start, max(tradable_targets)):
                window_prices = fetch_cached([symbol], start_date=days[0], end_date=days[-1]).get(symbol, [])
            accepted = accept_signal_close_prices(candidate, days, window_prices, official)
            if accepted.reason and recover_basis is not None:
                try:
                    accepted = recover_basis(accepted.reason, candidate, days, window_prices, official, load_snapshot=load_snapshot) or accepted
                except (LostLease, SourceDependencyBusy):
                    raise
                except ResearchSourceError as exc:
                    if exc.retryable:
                        accepted = AcceptedPrices(reason="validation_price_source_unavailable")
                except OSError:
                    accepted = AcceptedPrices(reason="validation_price_source_unavailable")
                except ValueError as exc:
                    logger.info("Signal basis evidence unavailable for %s on %s: %s", symbol, days[-1], type(exc).__name__)
            row = evaluate_forward_window(
                candidate, price_series=accepted.stock, benchmark_prices=accepted.benchmark,
                adapter=DAILY_RADAR_FORWARD_ADAPTER, window_days=len(days) - 1,
                as_of_date=calendar.through, benchmark_symbol=request.benchmark_symbol,
                validation_version=FORWARD_VALIDATION_VERSION, hit_threshold_pct=0,
                trading_calendar=calendar.sessions,
            )
            if suspended := suspended_outcome(row, candidate, days, official):
                outcomes.append(suspended)
                continue
            if (accepted.reason == "official_corporate_action_in_window" and row["skip_reason"] in
                    {"missing_future_price", "missing_benchmark"}):
                from ai_stock_sentinel.daily_radar import signal_action_evidence
                action = signal_action_evidence.classify_action(candidate, days, official)
                if action is not None:
                    reason, evidence = action
                    row = row | {"skip_reason": reason, "target_date": str(days[-1]), "outcome": {
                        "expected_target_date": str(days[-1]), signal_action_evidence.KEY: evidence}}
                    outcomes.append(bind_validation_input(candidate, row))
                    continue
            if (accepted.reason == "official_validation_price_missing"
                    and row["skip_reason"] in {"missing_future_price", "missing_benchmark"}):
                lifecycle = _with_market_lifecycle_diagnostics(
                    [row | {"skip_reason": "missing_future_price"}],
                    price_series_by_symbol={symbol: list(official.stock.values())},
                    benchmark_prices=list(official.benchmark.values()), trading_calendar=calendar.sessions,
                    as_of_date=calendar.through,
                )[0]
                if lifecycle["skip_reason"] == "trading_terminated_in_window":
                    outcomes.append(bind_validation_input(candidate, lifecycle))
                    continue
            if accepted.reason and row["skip_reason"] in {"missing_future_price", "missing_benchmark"}:
                reason = accepted.reason
                if (reason == "official_corporate_action_in_window"
                        and official.retryable_price_dates.intersection(session_dates)
                        and any(official.rights_terms.get(str(day), {}).get("cash_per_share", 0) > 0 for day in days[1:])):
                    reason = "validation_price_source_unavailable"
                if not window_prices:
                    rights_need_quotes = reason == "official_corporate_action_in_window" and any(
                        official.rights_terms.get(str(day), {}).get("kind") == "cash_rights_issue"
                        for day in days[1:]
                    )
                    if rights_need_quotes or (price_unavailable and reason in
                            {"adjustment_factor_unverified", "official_validation_price_missing"}):
                        reason = "validation_price_source_unavailable"
                row = refused_prices(row, reason, official, sessions=days, event_requires_prices=False)
            elif row["status"] == "validated":
                row["outcome"]["observation_diagnostic"] = evaluate_observation(
                    candidate, price_series=accepted.stock, benchmark_prices=accepted.benchmark,
                    window_days=len(days) - 1, as_of_date=calendar.through,
                )
                row = accepted.with_proof(bind_window(row, days[1:], "signal_close"))
            outcomes.append(bind_validation_input(candidate, row))
    return outcomes


def run_signal_close_validation(db, request, *, as_of_date, price_provider, calendar_provider,
                                candidate_ids=None, prepare_only=False, raw_price_loader=None,
                                official_price_collector=None):
    raw_price_loader = raw_price_loader or load_price_series_from_raw_data
    start_date = request.start_date
    if request.mode == "due" and start_date is None:
        start_date = default_due_start_date(as_of_date, max(request.windows or list(DEFAULT_FORWARD_WINDOWS)))
    metrics = ForwardValidationMetrics(logger, as_of_date=as_of_date)
    with metrics.stage("load_candidates"):
        candidates = forward_validation_candidates_from_runs(
            db,
            market=request.market,
            start_date=start_date,
            end_date=request.end_date or as_of_date, candidate_ids=candidate_ids,
        )
        if candidate_ids is not None:
            candidates = [c for c in candidates if c["candidate_id"] in candidate_ids]
            if {c["candidate_id"] for c in candidates} != set(candidate_ids):
                raise ValueError("validation_candidate_scope_unavailable")
        metrics.counts["candidate_count"] = len(candidates)
    try:
        validate_forward_validation_benchmark(
            candidates,
            benchmark_symbol=request.benchmark_symbol,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    acceptance = None if prepare_only else capture_validation_acceptance(db, candidates,
        market=request.market, windows=request.windows, validation_version=FORWARD_VALIDATION_VERSION)
    symbols = {str(candidate["symbol"]) for candidate in candidates}
    metrics.counts["symbol_count"] = len(symbols)
    candidate_record_dates = [
        parsed_date
        for candidate in candidates
        if (parsed_date := parse_date(candidate.get("record_date"))) is not None
    ]
    price_start_date = min(
        candidate_record_dates,
        default=start_date or as_of_date,
    )
    price_series = {}
    if official_price_collector is None:
        with metrics.stage("load_raw_prices"):
            price_series = raw_price_loader(
                db,
                symbols=sorted(symbols | {request.benchmark_symbol}),
                start_date=price_start_date,
                end_date=as_of_date,
            )
            metrics.counts["price_rows"] = sum(len(rows) for rows in price_series.values())
    benchmark_prices = price_series.get(request.benchmark_symbol, [])
    if not benchmark_prices and official_price_collector is None:
        with metrics.stage("load_benchmark"):
            benchmark_prices = load_benchmark_prices_from_prepared_market_context(
                db,
                market=request.market,
                benchmark_symbol=request.benchmark_symbol,
                as_of_date=as_of_date,
            )

    # All loaded inputs are plain dictionaries. End the read transaction before
    # official/provider HTTP, then start a short write transaction at persistence.
    db.rollback()
    calendar = None
    if candidates and request.market == "TW" and request.benchmark_symbol == "TAIEX":
        try:
            calendar = require_calendar(calendar_provider,start_date=price_start_date,end_date=as_of_date)
        except Exception as exc:
            raise HTTPException(status_code=503, detail={"code": "official_trading_calendar_unavailable"}) from exc

    source_errors = []

    def fetch_prices(symbols, *, start_date, end_date):
        db.rollback()
        remaining = sorted(set(symbols))
        with metrics.stage("fetch_prices", batch_symbol_count=len(symbols)):
            while remaining:
                try:
                    return price_provider.fetch(remaining, start_date=start_date, end_date=end_date)
                except ForwardPriceUnavailableError as exc:
                    unavailable = set(exc.symbols)
                    if (not unavailable or not unavailable.issubset(remaining)
                            or exc.start_date != start_date or exc.end_date != end_date):
                        raise
                    source_errors.append({"stage": "fetch_prices", "symbols": sorted(unavailable),
                        "start_date": str(start_date), "end_date": str(end_date),
                        "error_type": type(exc).__name__, "reason": str(exc)})
                    logger.warning("Forward prices unavailable symbols=%s start_date=%s end_date=%s reason=%s",
                        sorted(unavailable), start_date, end_date, exc)
                    # A cached provider may already have captured earlier symbols.
                    # Re-read those snapshots and collect the rest, excluding each
                    # unavailable symbol once. Never retry the same empty input.
                    remaining = [symbol for symbol in remaining if symbol not in unavailable]
            return {}

    windows_by_candidate = None
    if request.mode == "due":
        with metrics.stage("discover_due_windows"):
            windows_by_candidate = due_windows_by_candidate(
                candidates,
                as_of_date=as_of_date,
                windows=request.windows,
                price_series_by_symbol={symbol: price_series.get(symbol, []) for symbol in symbols},
                benchmark_prices=benchmark_prices,
            )
        if calendar is not None:
            windows_by_candidate = {f"id:{c['candidate_id']}": [w for w in request.windows
                if sum(date.fromisoformat(c["record_date"]) < d <= as_of_date for d in calendar.sessions) >= w]
                for c in candidates}
        with metrics.stage("exclude_persisted_windows"):
            windows_by_candidate = exclude_persisted_daily_radar_windows(
                db,
                windows_by_candidate,
                benchmark_symbol=request.benchmark_symbol,
                as_of_date=as_of_date,
                calendar=calendar,
            )
    if request.mode == "due" and official_price_collector is None:
        with metrics.stage("prepare_prices"):
            preparation = prepare_due_forward_validation(
                candidates,
                adapter=DAILY_RADAR_FORWARD_ADAPTER,
                pending_windows_by_candidate=windows_by_candidate,
                price_series_by_symbol=price_series,
                benchmark_prices=benchmark_prices,
                benchmark_symbol=request.benchmark_symbol,
                as_of_date=as_of_date,
                price_start_date=price_start_date,
                fetch_prices=fetch_prices,
                trading_calendar=calendar.sessions if calendar else None,
            )
        metrics.counts["price_rows"] = sum(len(rows) for rows in preparation.price_series_by_symbol.values())
        price_series = preparation.price_series_by_symbol
        benchmark_prices = preparation.benchmark_prices
        if calendar is None:
            windows_by_candidate = preparation.evaluation_windows_by_candidate
    with metrics.stage("evaluate"):
        if official_price_collector is not None and calendar is not None:
            from sqlalchemy import select
            from sqlalchemy.orm import Session
            from ai_stock_sentinel.db.models import DailyRadarCandidate
            def load_snapshot(candidate_id):
                with Session(db.get_bind()) as reader:
                    return reader.scalar(select(DailyRadarCandidate.input_snapshot).where(
                        DailyRadarCandidate.id == candidate_id))
            db.rollback()
            outcomes = _collect_locked_signal_prices(candidates, windows_by_candidate, calendar=calendar,
                request=request, collect_official=official_price_collector.collect, fetch_prices=fetch_prices,
                collect_targets=official_price_collector.collect_targets,
                load_snapshot=load_snapshot, recover_basis=getattr(official_price_collector, "recover_signal_basis", None),
                fetch_cached=getattr(price_provider, "fetch_cached", None))
        else:
            outcomes = build_forward_validation_report(
                candidates,
                price_series_by_symbol={symbol: price_series.get(symbol, []) for symbol in symbols},
                benchmark_prices=benchmark_prices,
                market=request.market,
                sample_source="production_db",
                as_of_date=as_of_date,
                windows=request.windows,
                benchmark_symbol=request.benchmark_symbol,
                windows_by_candidate=windows_by_candidate,
                trading_calendar=calendar.sessions if calendar else None,
            ).outcomes
    if prepare_only:
        return ValidationPreparation(candidates=candidates, outcomes=outcomes, calendar=calendar,
            as_of_date=as_of_date, return_basis="signal_close", benchmark_prices=benchmark_prices,
            diagnostics=source_errors)
    with metrics.stage("upsert"):
        write_summary = accept_validation_results(db, outcomes, calendar=calendar, acceptance=acceptance)
        metrics.counts["records_written"] = write_summary["records_written"]
    with metrics.stage("read_persisted"):
        persisted_outcomes = persisted_forward_validation_outcomes(
            db,
            candidates,
            windows=request.windows,
            as_of_date=as_of_date,
        )
    with metrics.stage("report"):
        report = build_forward_validation_report_from_outcomes(
            candidates,
            persisted_outcomes,
            market=request.market,
            sample_source="production_db_persisted_cohort",
            as_of_date=as_of_date,
            windows=request.windows,
            benchmark_symbol=request.benchmark_symbol,
            aggregation_scope="persisted_fixed_date_cohort",
            benchmark_prices=benchmark_prices,
            trading_calendar=calendar.sessions if calendar else None,
        )
        from ai_stock_sentinel.daily_radar.forward_validation import current_run_validation_diagnostics
        report.update(current_run_validation_diagnostics(outcomes))
        if write_summary.get("preserved_conflicts"):
            report["preserved_conflicts"] = write_summary["preserved_conflicts"]
        if source_errors:
            report.update({"source_recovery": source_errors[:64], "source_recovery_summary": {
                "total_count": len(source_errors), "reported_count": min(len(source_errors), 64),
                "truncated": len(source_errors) > 64, "error_count": len(source_errors),
                "error_reasons": dict(Counter(row["reason"] for row in source_errors))}})
    with metrics.stage("commit"):
        db.commit()
    return DailyRadarForwardValidationRunResponse(
        status="completed",
        mode=request.mode,
        market=request.market,
        as_of_date=as_of_date,
        candidate_count=len(candidates),
        records_written=write_summary["records_written"],
        validated_count=write_summary["validated_count"],
        skipped_count=write_summary["skipped_count"],
        retryable_skipped_count=write_summary["retryable_skipped_count"],
        terminal_skipped_count=write_summary["terminal_skipped_count"],
        report=report,
    )
