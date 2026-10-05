"""Public observation summaries from persisted data only; no evaluation or refresh."""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.forward_validation import DEFAULT_BENCHMARK_SYMBOL, number, parse_date
from ai_stock_sentinel.daily_radar.forward_validation import (
    load_benchmark_prices_from_prepared_market_context,
    load_price_series_from_raw_data,
    merge_price_series,
    persisted_forward_validation_outcomes,
)
from ai_stock_sentinel.daily_radar.observation_validation import (
    OBSERVATION_DIAGNOSTIC_VERSION, STRATEGY_FIELDS, canonical_radar_run_ids,
    load_observation_origins, observation_report, strategy_cohort,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarObservationStats, DailyRadarValidationCohort, DailyRadarValidationResponse,
)
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun


def read_observation_validation(session: Session, *, market: str, as_of_date: date,
                                lookback_days: int) -> DailyRadarValidationResponse:
    start = as_of_date - timedelta(days=lookback_days - 1)
    # Project only ranking/identity/version columns. Full snapshots contain large
    # price histories and private context that this read surface does not need.
    rows = session.execute(select(
        DailyRadarCandidate.id, DailyRadarCandidate.symbol, DailyRadarCandidate.observation_score,
        DailyRadarRun.run_date, DailyRadarRun.candidate_count,
        DailyRadarCandidate.input_snapshot["versions"].as_json(),
        DailyRadarCandidate.input_snapshot["selection_version"].as_string(),
        DailyRadarCandidate.input_snapshot["replay_input"]["market_context"]["benchmark"]["symbol"].as_string(),
    ).join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
      .where(DailyRadarRun.id.in_(canonical_radar_run_ids(
          market=market, start_date=start, end_date=as_of_date)),
          DailyRadarCandidate.selection_status == "selected")
      .order_by(DailyRadarRun.run_date, DailyRadarCandidate.id)).all()
    candidates = [{
        "candidate_id": cid, "symbol": symbol, "observation_score": score,
        "record_date": day.isoformat(), "daily_selected_pool_count": count, "selection_status": "selected",
        "input_snapshot": {"versions": versions, "selection_version": selection,
                           "replay_input": {"market_context": {"benchmark": {"symbol": benchmark}}}},
    } for cid, symbol, score, day, count, versions, selection, benchmark in rows]
    origins = load_observation_origins(session, candidates, market=market, through_date=as_of_date)
    for candidate in candidates:
        candidate["observation_origin"] = origins.get((candidate["symbol"], strategy_cohort(candidate)))
    windows = (5, 10, 20)
    outcomes = persisted_forward_validation_outcomes(
        session, candidates, windows=windows, as_of_date=as_of_date)
    outcomes = [row for row in outcomes
                if (evaluated := parse_date(row.get("evaluation_as_of_date"))) is None or evaluated <= as_of_date]
    benchmark = []
    if candidates:
        cached = load_price_series_from_raw_data(session, symbols=[DEFAULT_BENCHMARK_SYMBOL],
                                                start_date=start, end_date=as_of_date)
        fallback = load_benchmark_prices_from_prepared_market_context(
            session, market=market, benchmark_symbol=DEFAULT_BENCHMARK_SYMBOL,
            as_of_date=as_of_date, required_dates=[date.fromisoformat(c["record_date"]) for c in candidates])
        benchmark = merge_price_series(cached, {DEFAULT_BENCHMARK_SYMBOL: fallback}).get(DEFAULT_BENCHMARK_SYMBOL, [])
    calendar_days = {date.fromisoformat(row["date"]) for row in benchmark
                     if (close := number(row.get("close"))) is not None and close > 0}
    calendar_through = max(calendar_days, default=None)
    saved_targets = [day for row in outcomes if (day := parse_date(row.get("target_date"))) is not None]
    # A partial/older calendar must not hide an already saved mature result as
    # immature. Without usable dates, absent outcomes stay missing, not pending.
    calendar_usable = bool(calendar_days) and all(
        date.fromisoformat(c["record_date"]) in calendar_days for c in candidates)
    calendar_usable = calendar_usable and all(day in calendar_days for day in saved_targets)
    calendar_usable = calendar_usable and all(
        sum(date.fromisoformat(row["signal_date"]) < day <= date.fromisoformat(row["target_date"])
            for day in calendar_days) == row["window_days"]
        for row in outcomes if row["status"] == "validated"
    )
    report = observation_report(candidates, outcomes, windows, as_of_date=as_of_date,
                                benchmark_prices=benchmark if calendar_usable else None)
    dates_by_strategy = {}
    for candidate in candidates:
        dates_by_strategy.setdefault(strategy_cohort(candidate), []).append(date.fromisoformat(candidate["record_date"]))
    cohorts = []
    for cohort in report["cohorts"]:
        key = tuple(cohort["strategy"][field] for field in STRATEGY_FIELDS)
        dates = dates_by_strategy[key]
        cohorts.append(DailyRadarValidationCohort(
            id=json.dumps(key, ensure_ascii=True, separators=(",", ":")), strategy=cohort["strategy"],
            signal_start_date=min(dates), signal_end_date=max(dates),
            windows={window: {group: DailyRadarObservationStats.model_validate(stats)
                              for group, stats in groups.items()} for window, groups in cohort["windows"].items()},
        ))
    # Date/id ordering reflects the latest published selected signal, not a
    # lexicographic version sort or the cohort with the best-looking results.
    latest_key = strategy_cohort(candidates[-1]) if candidates else None
    default_id = json.dumps(latest_key, ensure_ascii=True, separators=(",", ":")) if latest_key else None
    evaluated_dates = [day for row in outcomes if (day := parse_date(row.get("evaluation_as_of_date"))) is not None]
    return DailyRadarValidationResponse(
        diagnostic_version=OBSERVATION_DIAGNOSTIC_VERSION, as_of_date=as_of_date,
        sample_start_date=start, sample_end_date=as_of_date, lookback_days=lookback_days,
        calendar_through_date=calendar_through if calendar_usable else None,
        last_evaluated_date=max(evaluated_dates, default=None), default_cohort_id=default_id, cohorts=cohorts,
    )
