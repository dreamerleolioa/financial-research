"""Shared signal-close execution with a read-only collection seam for durable work."""
from datetime import date
import logging
from fastapi import HTTPException
from ai_stock_sentinel.calibration.forward_validation_planning import prepare_due_forward_validation
from ai_stock_sentinel.calibration.runtime_metrics import ForwardValidationMetrics
from ai_stock_sentinel.daily_radar.presenter import parse_date
from ai_stock_sentinel.daily_radar.forward_validation import (
    DAILY_RADAR_FORWARD_ADAPTER, DEFAULT_FORWARD_WINDOWS, build_forward_validation_report,
    build_forward_validation_report_from_outcomes, default_due_start_date, due_windows_by_candidate,
    exclude_persisted_daily_radar_windows, forward_validation_candidates_from_runs,
    load_benchmark_prices_from_prepared_market_context, load_price_series_from_raw_data,
    persisted_forward_validation_outcomes, upsert_forward_validation_results, validate_forward_validation_benchmark,
)
from ai_stock_sentinel.daily_radar.trading_calendar import require_calendar, save_calendar
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunResponse
from ai_stock_sentinel.daily_radar.validation_preparation import ValidationPreparation, reserve_validation_commit

logger = logging.getLogger("ai_stock_sentinel.daily_radar.maintenance_router")


def run_signal_close_validation(db, request, *, as_of_date, price_provider, calendar_provider,
                                candidate_ids=None, prepare_only=False, raw_price_loader=None):
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
            end_date=request.end_date or as_of_date,
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
    with metrics.stage("load_raw_prices"):
        price_series = raw_price_loader(
            db,
            symbols=sorted(symbols | {request.benchmark_symbol}),
            start_date=price_start_date,
            end_date=as_of_date,
        )
        metrics.counts["price_rows"] = sum(len(rows) for rows in price_series.values())
    benchmark_prices = price_series.get(request.benchmark_symbol, [])
    if not benchmark_prices:
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

    def fetch_prices(symbols, *, start_date, end_date):
        db.rollback()
        with metrics.stage("fetch_prices", batch_symbol_count=len(symbols)):
            return price_provider.fetch(symbols, start_date=start_date, end_date=end_date)

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
        evaluation = build_forward_validation_report(
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
        )
    if prepare_only:
        return ValidationPreparation(candidates=candidates, outcomes=evaluation.outcomes, calendar=calendar,
            as_of_date=as_of_date, return_basis="signal_close", benchmark_prices=benchmark_prices)
    reserve_validation_commit(db)
    with metrics.stage("upsert"):
        write_summary = upsert_forward_validation_results(db, evaluation.outcomes)
        if calendar is not None:
            save_calendar(db, calendar)
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
        report.update({key: evaluation.report[key] for key in ("current_run_skip_reasons", "current_run_gaps")})
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
