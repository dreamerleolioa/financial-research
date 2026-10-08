"""Daily pool comparisons; repeated signals are observations, not independent trades."""
from collections import defaultdict
from datetime import date
from statistics import median
from typing import Any

from ai_stock_sentinel.calibration.forward_validation import candidate_key, number, parse_date
from ai_stock_sentinel.daily_radar.observation_validation import strategy_cohort


def ranked_pool_groups(candidates):
    by_day = defaultdict(list)
    cohorts = defaultdict(lambda: defaultdict(list))
    for candidate in candidates:
        if candidate.get("selection_status") == "selected":
            by_day[candidate["record_date"]].append(candidate)
        elif candidate.get("shadow_cohort") == "comparable":
            cohorts[strategy_cohort(candidate)]["comparable_shadow"].append(candidate)
    for rows in by_day.values():
        rows.sort(key=lambda c: (-int(c.get("observation_score") or 0), str(c["symbol"])))
        expected = {c["daily_selected_pool_count"] for c in rows if "daily_selected_pool_count" in c}
        complete = not expected or expected == {len(rows)}
        for rank, c in enumerate(rows, 1):
            row = c | {"ranking_pool_complete": complete}
            groups = cohorts[strategy_cohort(c)]
            groups["selected"].append(row)
            if rank <= 3:
                groups["top_3"].append(row)
            if rank <= 5:
                groups["top_5"].append(row)
    return cohorts


def pool_comparisons(candidates, outcomes, windows, *, calendar: list[date] | None = None):
    cohorts = ranked_pool_groups(candidates)
    by_candidate = {(candidate_key(row | {"record_date": row.get("signal_date")}), row["window_days"]): row
                    for row in outcomes}
    reports = {}
    for cohort, groups in cohorts.items():
        reports[cohort] = {}
        for window in windows:
            report = {name: _quality(groups[name], by_candidate, window, calendar) for name in (
                "selected", "top_3", "top_5", "comparable_shadow",
            )}
            selected, shadow = report["selected"], report["comparable_shadow"]
            total_positive = selected["positive_excess_count"] + shadow["positive_excess_count"]
            comparable = (selected["coverage_complete"] and shadow["coverage_complete"]
                          and selected["evaluated_count"] > 0 and shadow["evaluated_count"] > 0
                          and selected["benchmark_symbols"] == shadow["benchmark_symbols"])
            report["observed_positive_capture_share"] = (
                selected["positive_excess_count"] / total_positive if comparable and total_positive else None
            )
            report["population_scope"] = "observed_daily_comparable_pool"
            reports[cohort][str(window)] = report
    return reports


def _quality(candidates, outcomes, window, calendar) -> dict[str, Any]:
    excess, evaluated = [], []
    benchmarks = set()
    missing = skipped = immature = missing_metrics = 0
    for c in candidates:
        row = outcomes.get((candidate_key(c), window))
        if row is None:
            signal = parse_date(c["record_date"])
            if calendar is not None and signal in calendar and sum(day > signal for day in calendar) < window:
                immature += 1
            else:
                missing += 1
            continue
        if row["status"] != "validated":
            skipped += 1
            continue
        value = number((row.get("outcome") or {}).get("excess_return_vs_benchmark_pct"))
        benchmark = row.get("benchmark_symbol")
        if value is None or not benchmark:
            missing_metrics += 1
            continue
        excess.append(value)
        evaluated.append(c)
        benchmarks.add(benchmark)
    complete = not (missing or skipped or missing_metrics) and len(benchmarks) <= 1
    complete = complete and all(c.get("ranking_pool_complete", True) for c in candidates)
    n = len(excess)
    positives = sum(value > 0 for value in excess)
    return {
        "sample_count": len(candidates), "evaluated_count": n,
        "signal_date_count": len({c["record_date"] for c in evaluated}),
        "distinct_symbol_count": len({c["symbol"] for c in evaluated}),
        "missing_outcome_count": missing, "skipped_count": skipped,
        "immature_count": immature, "missing_metric_count": missing_metrics,
        "benchmark_symbols": sorted(benchmarks), "coverage_complete": complete,
        "positive_excess_count": positives,
        "positive_excess_rate": positives / n if n and complete else None,
        "median_excess_return_pct": round(median(excess), 4) if n and complete else None,
    }
