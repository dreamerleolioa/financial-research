"""Descriptive downside metrics and paired daily, horizon-block uncertainty."""
from collections import Counter, defaultdict
from math import ceil
from random import Random
from statistics import mean, median

from ai_stock_sentinel.calibration.forward_validation import candidate_key, number, parse_date
from ai_stock_sentinel.daily_radar.pool_quality import pool_comparisons, ranked_pool_groups
from ai_stock_sentinel.daily_radar.research_validation import (
    RESEARCH_COSTS, RESEARCH_PRICE_BASIS, RESEARCH_VALIDATION_VERSION,
)


MIN_CONFIDENCE_BLOCKS = 10
BOOTSTRAP_ITERATIONS = 500


def research_pool_comparisons(candidates, outcomes, windows, *, calendar=None):
    saved = []
    for row in outcomes:
        if row.get("validation_version") != RESEARCH_VALIDATION_VERSION:
            continue
        data = row.get("outcome") or {}
        if row["status"] == "validated" and (
            data.get("return_basis") != "next_open" or data.get("price_basis") != RESEARCH_PRICE_BASIS
        ):
            row = row | {"status": "skipped", "skip_reason": "research_basis_mismatch", "outcome": {}}
        saved.append(row)
    grouped = ranked_pool_groups(candidates)
    member_keys = {cohort: {candidate_key(c) for members in groups.values() for c in members}
                   for cohort, groups in grouped.items()}
    by_candidate = {(candidate_key(row), row["window_days"]): row for row in saved}
    comparisons = {}
    for cost in RESEARCH_COSTS:
        adjusted = []
        for row in saved:
            data = row.get("outcome") or {}
            excess = number(data.get("excess_return_vs_benchmark_pct"))
            adjusted.append(row | {"outcome": data | {
                "excess_return_vs_benchmark_pct": excess - cost
                    if excess is not None and number(data.get("forward_return_pct")) is not None else None}})
        base = pool_comparisons(candidates, adjusted, windows, calendar=calendar)
        for cohort, windows_report in base.items():
            comparisons.setdefault(cohort, {})
            for window in windows:
                report = windows_report[str(window)]
                for group in ("selected", "top_3", "top_5", "comparable_shadow"):
                    report[group].update(_risk_stats(
                        grouped[cohort][group], by_candidate, window, cost, report[group], calendar))
                result = comparisons[cohort].setdefault(str(window), {
                    "validation_version": RESEARCH_VALIDATION_VERSION, "return_basis": "next_open",
                    "price_basis": RESEARCH_PRICE_BASIS, "dividends_included": False,
                    "cost_model": "assumed_total_cost_percentage_points", "cost_scenarios": {},
                    "calendar_through_date": max(calendar).isoformat() if calendar else None,
                })
                if cost == 0:
                    confidence = _confidence(grouped[cohort], by_candidate, window, report, calendar, strategy=cohort)
                    result["last_evaluated_date"] = max((r["evaluation_as_of_date"] for r in saved
                        if r["window_days"] == window and r.get("evaluation_as_of_date")
                        and candidate_key(r) in member_keys[cohort]),
                        default=None)
                else:
                    # Equal assumed costs in the two groups cancel in the paired difference.
                    confidence = result["cost_scenarios"]["0"]["confidence"]
                report["confidence"] = confidence
                result["cost_scenarios"][format(cost, "g")] = report
    return comparisons


def _risk_stats(candidates, outcomes, window, cost, base, calendar):
    returns, adverse = [], []
    missing_risk = 0
    reasons = Counter()
    for candidate in candidates:
        row = outcomes.get((candidate_key(candidate), window))
        if row is None:
            signal = parse_date(candidate["record_date"])
            immature = calendar is not None and signal in calendar and sum(day > signal for day in calendar) < window
            reasons["window_not_mature" if immature else "missing_outcome"] += 1
            continue
        if row["status"] != "validated":
            reasons[row.get("skip_reason") or "validation_skipped"] += 1
            continue
        data = row.get("outcome") or {}
        gross = number(data.get("forward_return_pct"))
        excess = number(data.get("excess_return_vs_benchmark_pct"))
        if gross is None or excess is None or not row.get("benchmark_symbol"):
            reasons["missing_return_metric"] += 1
            continue
        returns.append(gross - cost)
        risk = number(data.get("max_adverse_excursion_pct"))
        if risk is None or not -100 <= risk <= 0:
            missing_risk += 1
            reasons["missing_risk_metric"] += 1
        else:
            adverse.append(risk)
    complete = base["coverage_complete"] and len(returns) == base["evaluated_count"]
    risk_complete = complete and not missing_risk and len(adverse) == len(returns)
    tail_size = ceil(len(returns) * .1)
    return {
        "median_return_pct": round(median(returns), 4) if complete and returns else None,
        "worst_decile_mean_return_pct": round(mean(sorted(returns)[:tail_size]), 4)
            if complete and len(returns) >= 10 else None,
        "worst_adverse_excursion_pct": min(adverse) if risk_complete and adverse else None,
        "median_adverse_excursion_pct": round(median(adverse), 4) if risk_complete and adverse else None,
        "risk_sample_count": len(adverse), "missing_risk_count": missing_risk,
        "missing_reasons": dict(reasons),
    }


def _confidence(groups, outcomes, window, report, calendar, *, strategy):
    result = {"status": "insufficient_blocks", "method": "paired_daily_moving_block_bootstrap",
              "level": .95, "block_trading_days": window, "minimum_blocks": MIN_CONFIDENCE_BLOCKS,
              "paired_date_count": 0, "effective_block_count": 0,
              "mean_difference_pct": None, "lower_pct": None, "upper_pct": None}
    if any(value in ("unknown", "legacy") for value in strategy):
        return result | {"status": "strategy_unknown", "effective_block_count": None}
    selected, shadow = report["selected"], report["comparable_shadow"]
    if (not selected["coverage_complete"] or not shadow["coverage_complete"]
            or (selected["benchmark_symbols"] and shadow["benchmark_symbols"]
                and selected["benchmark_symbols"] != shadow["benchmark_symbols"])):
        return result | {"status": "incomplete_coverage"}
    daily = {}
    for group in ("selected", "comparable_shadow"):
        by_day = defaultdict(lambda: defaultdict(list))
        for candidate in groups[group]:
            row = outcomes.get((candidate_key(candidate), window))
            if row is None or row["status"] != "validated":
                continue
            value = number((row.get("outcome") or {}).get("excess_return_vs_benchmark_pct"))
            if value is not None:
                by_day[parse_date(candidate["record_date"])][candidate["symbol"]].append(value)
        daily[group] = {day: mean(mean(values) for values in symbols.values()) for day, symbols in by_day.items()}
    paired = sorted(set(daily["selected"]) & set(daily["comparable_shadow"]))
    result["paired_date_count"] = len(paired)
    if calendar is None:
        return result | {"status": "calendar_missing", "effective_block_count": None}
    if not paired:
        return result
    deltas = {day: daily["selected"][day] - daily["comparable_shadow"][day] for day in paired}
    result["mean_difference_pct"] = round(mean(deltas.values()), 4)
    dates = sorted({day for day in calendar if paired[0] <= day <= paired[-1]})
    if any(day not in dates for day in paired):
        return result | {"status": "calendar_missing"}
    run = 0
    for day in dates:
        run = run + 1 if day in deltas else 0
        if run == window:
            result["effective_block_count"] += 1
            run = 0
    if result["effective_block_count"] < MIN_CONFIDENCE_BLOCKS:
        return result
    if len(paired) / len(dates) < .8:
        return result | {"status": "sparse_comparable_dates"}
    # Resample contiguous trading-date blocks together across stocks/groups.
    # Adjacent overlapping return windows are not individual bootstrap draws.
    timeline = [deltas.get(day) for day in dates]
    rng = Random(1729)
    estimates = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        sampled = []
        for _ in range(ceil(len(timeline) / window)):
            start = rng.randrange(len(timeline) - window + 1)
            sampled.extend(timeline[start:start + window])
        values = [value for value in sampled[:len(timeline)] if value is not None]
        if values:
            estimates.append(mean(values))
    estimates.sort()
    return result | {"status": "estimated", "lower_pct": round(_percentile(estimates, .025), 4),
                     "upper_pct": round(_percentile(estimates, .975), 4)}


def _percentile(values, probability):
    position = (len(values) - 1) * probability
    index = int(position)
    return values[index] + (values[min(index + 1, len(values) - 1)] - values[index]) * (position - index)
