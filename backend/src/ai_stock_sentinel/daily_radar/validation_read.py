"""Public observation summaries from persisted data only; no evaluation or refresh."""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.forward_validation import parse_date
from ai_stock_sentinel.daily_radar.forward_validation import (
    persisted_forward_validation_outcomes, forward_validation_candidates_from_runs,
)
from ai_stock_sentinel.daily_radar.observation_validation import (
    OBSERVATION_DIAGNOSTIC_VERSION, STRATEGY_FIELDS,
    observation_report, strategy_cohort,
)
from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar, check_outcome_calendar
from ai_stock_sentinel.daily_radar.pool_quality import pool_comparisons
from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_VALIDATION_WINDOWS
from ai_stock_sentinel.daily_radar.research_quality import research_pool_comparisons
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarObservationStats, DailyRadarValidationCohort, DailyRadarValidationResponse,
)


def read_observation_validation(session: Session, *, market: str, as_of_date: date,
                                lookback_days: int) -> DailyRadarValidationResponse:
    start = as_of_date - timedelta(days=lookback_days - 1)
    # Project only ranking/identity/version columns. Full snapshots contain large
    # price histories and private context that this read surface does not need.
    # Use the same bounded SQL projection as evaluation and work identities;
    # report-only projections cannot establish which inputs produced a result.
    candidates = forward_validation_candidates_from_runs(
        session, market=market, start_date=start, end_date=as_of_date, comparable_pool_only=True)
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
    # Price availability cannot certify missing outcomes as immature, including
    # when old or conflicting calendar evidence was rejected by load_calendar.
    calendar_days = research_calendar_days = set()
    calendar_through = None
    calendar_usable = research_calendar_usable = False
    official = load_calendar(session, start_date=min(
        (date.fromisoformat(c["record_date"]) for c in candidates), default=as_of_date), as_of_date=as_of_date)
    calendar_invalid = []
    required_through = max([date.fromisoformat(c["record_date"]) for c in candidates] + [
        parse_date(r["target_date"]) for r in outcomes + research_outcomes
        if r["status"] == "validated" and r.get("target_date")], default=start)
    if official is not None and official.through < required_through:
        official = None
    if official is not None:
        calendar_days = research_calendar_days = set(official.sessions)
        calendar_through = max(calendar_days, default=None)
        # A complete proof through yesterday can establish saved targets, but
        # cannot establish that a missing result is still immature today.
        calendar_usable = research_calendar_usable = official.through >= as_of_date
        outcomes, close_invalid = check_outcome_calendar(outcomes,official.sessions)
        research_outcomes, research_invalid = check_outcome_calendar(research_outcomes,official.sessions)
        calendar_invalid = close_invalid + research_invalid
        # Only observation maturity consumes these synthetic date markers;
        # performance continues to use persisted, independently checked prices.
        benchmark = [{"date": str(d), "close": 1} for d in official.sessions]
    report = observation_report(candidates, outcomes, windows, as_of_date=as_of_date,
                                benchmark_prices=benchmark if calendar_usable else None)
    comparisons = pool_comparisons(candidates, outcomes, windows,
                                   calendar=sorted(calendar_days) if calendar_usable else None)
    research_comparisons = research_pool_comparisons(candidates, research_outcomes, windows,
        calendar=sorted(research_calendar_days) if research_calendar_usable else None)
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
        calendar_through_date=calendar_through if calendar_usable or official is not None else None,
        last_evaluated_date=max(evaluated_dates, default=None), default_cohort_id=default_id, cohorts=cohorts,
        calendar_invalid_windows=calendar_invalid,
    )
