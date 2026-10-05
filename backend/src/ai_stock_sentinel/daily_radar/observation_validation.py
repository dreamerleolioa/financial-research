"""Versioned, diagnostic-only observation events; original return outcomes stay intact."""
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select

from ai_stock_sentinel.calibration.forward_validation import candidate_key, number, parse_date
from ai_stock_sentinel.daily_radar.repository import PUBLIC_RUN_STATUSES
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun

OBSERVATION_DIAGNOSTIC_VERSION = "daily-radar-observation-v1"
STRATEGY_FIELDS = ("scoring_version", "rule_version", "config_version", "selection_version")
EVENT_STATUSES = frozenset({"confirmed", "invalidated", "unconfirmed"})


def strategy_cohort(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    snapshot = candidate.get("input_snapshot") or {}
    versions = snapshot.get("versions") or {}
    return tuple(str(versions.get(field) or "unknown") for field in STRATEGY_FIELDS[:3]) + (
        str(snapshot.get("selection_version") or "legacy"),
    )


def canonical_radar_run_ids(*, market: str, start_date: date | None = None,
                            end_date: date | None = None, statuses: tuple[str, ...] = PUBLIC_RUN_STATUSES):
    query = select(
        DailyRadarRun.id.label("run_id"),
        func.row_number().over(partition_by=DailyRadarRun.run_date,
                               order_by=(DailyRadarRun.created_at.desc(), DailyRadarRun.id.desc())).label("revision"),
    ).where(DailyRadarRun.market == market, DailyRadarRun.status.in_(statuses))
    if start_date is not None:
        query = query.where(DailyRadarRun.run_date >= start_date)
    if end_date is not None:
        query = query.where(DailyRadarRun.run_date <= end_date)
    runs = query.subquery()
    return select(runs.c.run_id).where(runs.c.revision == 1)


def load_observation_origins(session: Any, candidates: Sequence[Mapping[str, Any]], *,
                             market: str, through_date: date) -> dict[tuple[str, tuple[str, ...]], dict[str, Any]]:
    """Find first selected signals outside the requested evaluation range, without full snapshots.

    Select the canonical public run BEFORE filtering symbols or selected rows, so
    a removed symbol cannot resurrect an older revision of the same trading day.
    """
    symbols = {str(c.get("symbol")) for c in candidates if c.get("selection_status") == "selected"}
    if not symbols:
        return {}
    rows = session.execute(select(
        DailyRadarCandidate.id, DailyRadarCandidate.symbol, DailyRadarRun.run_date,
        DailyRadarCandidate.input_snapshot["versions"].as_json(),
        DailyRadarCandidate.input_snapshot["selection_version"].as_string(),
    ).join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
      .where(DailyRadarRun.id.in_(canonical_radar_run_ids(market=market, end_date=through_date)),
             DailyRadarCandidate.selection_status == "selected",
             DailyRadarCandidate.symbol.in_(symbols))
      .order_by(DailyRadarRun.run_date, DailyRadarCandidate.id))
    origins = {}
    for candidate_id, symbol, day, versions, selection_version in rows:
        cohort = strategy_cohort({"input_snapshot": {"versions": versions,
                                                     "selection_version": selection_version}})
        origins.setdefault((symbol, cohort), {"first_seen_date": day.isoformat(),
                                             "candidate_id": candidate_id,
                                             "scope": "available_public_history"})
    return origins


def evaluate_observation(candidate: Mapping[str, Any], *, price_series: Sequence[Mapping[str, Any]],
                         benchmark_prices: Sequence[Mapping[str, Any]], window_days: int,
                         as_of_date: date | None) -> dict[str, Any]:
    origin = candidate.get("observation_origin") or {}
    signal = parse_date(candidate.get("record_date"))
    result = {
        "version": OBSERVATION_DIAGNOSTIC_VERSION, "status": "insufficient_data", "missing_reason": None,
        "first_seen_date": origin.get("first_seen_date"), "origin_scope": origin.get("scope"),
        "support_reference": None, "resistance_reference": None, "confirmation_date": None,
        "invalidation_date": None, "lead_trading_days": None,
        "waiting_max_adverse_excursion_pct": None, "waiting_end_date": None,
        "definition": "two_consecutive_closes_above_fixed_resistance_close_below_fixed_support",
    }
    if not origin or origin.get("scope") != "available_public_history":
        return result | {"missing_reason": "origin_unknown"}
    first = parse_date(origin.get("first_seen_date"))
    if signal is None or first is None or first > signal or (as_of_date and signal > as_of_date):
        return result | {"missing_reason": "origin_date_invalid"}
    if first != signal or origin.get("candidate_id") != candidate.get("candidate_id"):
        return result | {"status": "not_first_observation"}
    snapshot = candidate.get("input_snapshot") or {}
    indicators = snapshot.get("indicators") or {}
    support, resistance = number(indicators.get("support_level")), number(indicators.get("resistance_level"))
    entry = number((snapshot.get("ohlcv") or {}).get("close"))
    if (support is None or resistance is None or entry is None or
            not 0 < support < resistance or entry <= 0):
        return result | {"missing_reason": "reference_missing_or_invalid"}
    result.update(support_reference=support, resistance_reference=resistance)
    dates = candidate.get("data_dates") or {}
    if (parse_date(dates.get("ohlcv")) != signal or
            ("technical_indicators" in dates and parse_date(dates["technical_indicators"]) != signal)):
        return result | {"missing_reason": "reference_date_unknown_or_stale"}
    if entry > resistance:
        return result | {"status": "already_broken_out"}
    if entry < support:
        return result | {"status": "already_invalidated"}

    days = sorted({day for row in benchmark_prices
                   if (day := parse_date(row.get("date"))) is not None and signal < day
                   and (as_of_date is None or day <= as_of_date)
                   and (close := number(row.get("close"))) is not None and close > 0})[:window_days]
    if len(days) != window_days:
        return result | {"missing_reason": "window_not_mature"}
    rows, conflicting = {}, set()
    for row in price_series:
        day = parse_date(row.get("date"))
        if day not in days:
            continue
        close, high, low = (number(row.get(key)) for key in ("close", "high", "low"))
        if close is None or high is None or low is None or not 0 < low <= close <= high:
            conflicting.add(day)
            continue
        values = (close, high, low)
        if day in rows and rows[day] != values:
            conflicting.add(day)
        rows[day] = values
    if conflicting or any(day not in rows for day in days):
        return result | {"missing_reason": "candidate_history_gap_or_invalid_ohlc"}
    minimum, above_count = entry, 0
    result["status"] = "unconfirmed"
    for elapsed, day in enumerate(days, 1):
        close, _high, low = rows[day]
        minimum = min(minimum, low)
        result.update(waiting_end_date=day.isoformat(),
                      waiting_max_adverse_excursion_pct=round((minimum / entry - 1) * 100, 4))
        if close < support:
            return result | {"status": "invalidated", "invalidation_date": day.isoformat()}
        above_count = above_count + 1 if close > resistance else 0
        if above_count == 2:
            return result | {"status": "confirmed", "confirmation_date": day.isoformat(),
                             "lead_trading_days": elapsed}
    return result


def observation_report(candidates: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]],
                       windows: Sequence[int], *, as_of_date: date | None,
                       benchmark_prices: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Fix daily ranks before joining outcomes; never backfill an absent top-ranked result."""
    by_day = defaultdict(list)
    for candidate in candidates:
        if candidate.get("selection_status", "selected") == "selected":
            by_day[str(candidate.get("record_date"))].append(candidate)
    cohorts = defaultdict(list)
    for _day, rows in sorted(by_day.items()):
        rows.sort(key=lambda c: (-int(c.get("observation_score") or 0), str(c.get("symbol"))))
        expected_counts = {c["daily_selected_pool_count"] for c in rows if "daily_selected_pool_count" in c}
        pool_complete = not expected_counts or expected_counts == {len(rows)}
        for rank, candidate in enumerate(rows, 1):
            cohorts[strategy_cohort(candidate)].append((candidate, rank, pool_complete))
    calendar = None if benchmark_prices is None else sorted({day for row in benchmark_prices
        if (day := parse_date(row.get("date"))) is not None
        and (as_of_date is None or day <= as_of_date)
        and (close := number(row.get("close"))) is not None and close > 0})
    by_candidate = {(candidate_key(dict(row) | {"record_date": row.get("signal_date")}),
                     int(row["window_days"])): row for row in outcomes}
    result = []
    for cohort, ranked in sorted(cohorts.items()):
        groups = {"all_selected": ranked, "top_3": [row for row in ranked if row[1] <= 3],
                  "remaining_after_3": [row for row in ranked if row[1] > 3],
                  "top_5": [row for row in ranked if row[1] <= 5],
                  "remaining_after_5": [row for row in ranked if row[1] > 5]}
        result.append({"strategy": dict(zip(STRATEGY_FIELDS, cohort)), "windows": {
            str(window): {name: _summarize(rows, by_candidate, window, calendar) for name, rows in groups.items()}
            for window in windows}})
    return {"version": OBSERVATION_DIAGNOSTIC_VERSION,
            "population_scope": "first_available_selected_signal_per_symbol_and_strategy",
            "ranking_scope": "complete_supplied_daily_selected_pool_before_outcomes",
            "waiting_risk_basis": "signal_close_to_lowest_future_low_through_event_day_inclusive",
            "diagnostic_only": True, "cohorts": result}


def _summarize(ranked, outcomes, window, calendar):
    statuses, missing = Counter(), Counter()
    diagnostics, evaluated_candidates = [], []
    missing_outcomes = missing_diagnostics = skipped = immature = repeats = 0
    for c, _rank, _pool_complete in ranked:
        origin = c.get("observation_origin") or {}
        signal = parse_date(c.get("record_date"))
        first = parse_date(origin.get("first_seen_date"))
        if (origin.get("scope") == "available_public_history" and signal is not None and first is not None
                and first <= signal and (first != signal or origin.get("candidate_id") != c.get("candidate_id"))):
            repeats += 1
            continue
        if calendar is not None and signal is not None and sum(day > signal for day in calendar) < window:
            immature += 1
            continue
        row = outcomes.get((candidate_key(c), window))
        if row is None:
            missing_outcomes += 1
            continue
        if row.get("status") != "validated":
            skipped += 1
            missing[str(row.get("skip_reason") or "validation_not_available")] += 1
            continue
        diagnostic = (row.get("outcome") or {}).get("observation_diagnostic") or {}
        if diagnostic.get("version") != OBSERVATION_DIAGNOSTIC_VERSION:
            missing_diagnostics += 1
            continue
        statuses[str(diagnostic.get("status"))] += 1
        if diagnostic.get("missing_reason"):
            missing[str(diagnostic["missing_reason"])] += 1
        if diagnostic.get("status") in EVENT_STATUSES:
            diagnostics.append(diagnostic)
            evaluated_candidates.append(c)
    n = len(diagnostics)
    pool_complete = all(complete for _c, _rank, complete in ranked)
    complete = pool_complete and not (missing_outcomes or missing_diagnostics or skipped or statuses["insufficient_data"])
    confirmed = [d for d in diagnostics if d["status"] == "confirmed"]
    return {
        "candidate_ids": [c.get("candidate_id") for c, _rank, _complete in ranked],
        "signal_date_count": len({c.get("record_date") for c, _rank, _complete in ranked}),
        "selected_count": len(ranked), "evaluated_observation_count": n,
        "evaluated_signal_date_count": len({c.get("record_date") for c in evaluated_candidates}),
        "evaluated_distinct_symbol_count": len({c.get("symbol") for c in evaluated_candidates}),
        "missing_outcome_count": missing_outcomes, "missing_diagnostic_count": missing_diagnostics,
        "immature_observation_count": immature, "excluded_repeat_count": repeats,
        "ranking_pool_complete": pool_complete,
        "skipped_validation_count": skipped, "status_counts": dict(sorted(statuses.items())),
        "missing_reasons": dict(sorted(missing.items())), "coverage_complete": complete,
        "confirmation_rate": statuses["confirmed"] / n if n and complete else None,
        "invalidation_rate": statuses["invalidated"] / n if n and complete else None,
        "mean_lead_trading_days": _mean([d["lead_trading_days"] for d in confirmed]),
        "mean_waiting_max_adverse_excursion_pct": _mean([d["waiting_max_adverse_excursion_pct"] for d in diagnostics]),
        "means_scope": "evaluated_first_observations_only",
    }


def _mean(values):
    return round(sum(values) / len(values), 4) if values else None
