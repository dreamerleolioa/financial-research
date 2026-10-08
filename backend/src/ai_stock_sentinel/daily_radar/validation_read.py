"""Public observation summaries from persisted data only; no evaluation or refresh."""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import or_, select
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
from ai_stock_sentinel.daily_radar.pool_quality import pool_comparisons
from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_VALIDATION_WINDOWS
from ai_stock_sentinel.daily_radar.research_quality import research_pool_comparisons
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
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
        DailyRadarCandidate.selection_status, DailyRadarCandidate.shadow_cohort,
        DailyRadarCandidate.input_snapshot["versions"].as_json(),
        DailyRadarCandidate.input_snapshot["selection_version"].as_string(),
        DailyRadarCandidate.input_snapshot["replay_input"]["market_context"]["benchmark"]["symbol"].as_string(),
    ).join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
      .where(DailyRadarRun.id.in_(canonical_radar_run_ids(
          market=market, start_date=start, end_date=as_of_date)),
          or_(DailyRadarCandidate.selection_status == "selected",
              (DailyRadarCandidate.selection_status == "shadow") & (DailyRadarCandidate.shadow_cohort == "comparable")))
      .order_by(DailyRadarRun.run_date, DailyRadarCandidate.id)).all()
    candidates = [{
        "candidate_id": cid, "symbol": symbol, "observation_score": score,
        "record_date": day.isoformat(), "daily_selected_pool_count": count,
        "selection_status": status, "shadow_cohort": shadow,
        "input_snapshot": {"versions": versions, "selection_version": selection,
                           "replay_input": {"market_context": {"benchmark": {"symbol": benchmark}}}},
    } for cid, symbol, score, day, count, status, shadow, versions, selection, benchmark in rows]
    origins = load_observation_origins(session, candidates, market=market, through_date=as_of_date)
    for candidate in candidates:
        candidate["observation_origin"] = origins.get((candidate["symbol"], strategy_cohort(candidate)))
    windows = DAILY_RADAR_VALIDATION_WINDOWS
    outcomes = persisted_forward_validation_outcomes(
        session, candidates, windows=windows, as_of_date=as_of_date)
    outcomes = [row for row in outcomes
                if (evaluated := parse_date(row.get("evaluation_as_of_date"))) is None or evaluated <= as_of_date]
    research_outcomes = persisted_forward_validation_outcomes(
        session, candidates, windows=windows, as_of_date=as_of_date,
        validation_version=RESEARCH_VALIDATION_VERSION)
    research_outcomes = [row for row in research_outcomes
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
    # A partial/older calendar must not hide an already saved mature result as
    # immature. Without usable dates, absent outcomes stay missing, not pending.
    def usable_calendar(saved):
        return bool(calendar_days) and all(
            date.fromisoformat(c["record_date"]) in calendar_days for c in candidates
        ) and all(
            parse_date(row.get("target_date")) in calendar_days and
            sum(date.fromisoformat(row["signal_date"]) < day <= date.fromisoformat(row["target_date"])
                for day in calendar_days) == row["window_days"]
            for row in saved if row["status"] == "validated"
        )
    # Each validation basis owns its calendar checks; a malformed new result
    # must never change legacy observation maturity or hide its saved results.
    calendar_usable = usable_calendar(outcomes)
    research_calendar_usable = usable_calendar(research_outcomes)
    report = observation_report(candidates, outcomes, windows, as_of_date=as_of_date,
                                benchmark_prices=benchmark if calendar_usable else None)
    comparisons = pool_comparisons(candidates, outcomes, windows,
                                   calendar=sorted(calendar_days) if calendar_usable else None)
    research_comparisons = research_pool_comparisons(candidates, research_outcomes, windows,
        calendar=sorted(calendar_days) if research_calendar_usable else None)
    dates_by_strategy = {}
    for candidate in candidates:
        if candidate["selection_status"] == "selected":
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
            pool_comparison=comparisons.get(key, {}),
            research_pool_comparison=research_comparisons.get(key, {}),
        ))
    # Date/id ordering reflects the latest published selected signal, not a
    # lexicographic version sort or the cohort with the best-looking results.
    latest_selected = next((c for c in reversed(candidates) if c["selection_status"] == "selected"), None)
    latest_key = strategy_cohort(latest_selected) if latest_selected else None
    default_id = json.dumps(latest_key, ensure_ascii=True, separators=(",", ":")) if latest_key else None
    evaluated_dates = [day for row in outcomes + research_outcomes if (day := parse_date(row.get("evaluation_as_of_date"))) is not None]
    return DailyRadarValidationResponse(
        diagnostic_version=OBSERVATION_DIAGNOSTIC_VERSION, as_of_date=as_of_date,
        sample_start_date=start, sample_end_date=as_of_date, lookback_days=lookback_days,
        calendar_through_date=calendar_through if calendar_usable else None,
        last_evaluated_date=max(evaluated_dates, default=None), default_cohort_id=default_id, cohorts=cohorts,
    )
