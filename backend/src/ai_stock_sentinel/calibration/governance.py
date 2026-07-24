from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any


DEFAULT_BOOTSTRAP_SEED = 20260724
DEFAULT_BOOTSTRAP_ITERATIONS = 500
DEFAULT_MIN_VALIDATED_COVERAGE = 0.9


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end_exclusive = date(year + 1, 1, 1)
    else:
        end_exclusive = date(year, month + 1, 1)
    return start, end_exclusive - timedelta(days=1)


def month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def select_training_and_holdout_months(
    watermarks: Sequence[Mapping[str, Any]],
    *,
    count: int = 6,
) -> dict[str, Any]:
    mature = sorted(
        (
            str(row["month"])
            for row in watermarks
            if row.get("maturity_complete") is True
        )
    )
    selected = mature[-count:]
    return {
        "required_month_count": count,
        "selected_months": selected,
        "training_months": selected[:-1] if len(selected) == count else [],
        "holdout_month": selected[-1] if len(selected) == count else None,
        "cohort_complete": len(selected) == count,
    }


def outcome_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    score_key: str | None = None,
    selection: Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    selected = [row for row in rows if selection is None or selection(row)]
    forward = [_number(_outcome(row).get("forward_return_pct")) for row in selected]
    excess = [_number(_outcome(row).get("excess_return_vs_benchmark_pct")) for row in selected]
    downside = [_number(_outcome(row).get("max_adverse_excursion_pct")) for row in selected]
    valid_forward = [value for value in forward if value is not None]
    valid_excess = [value for value in excess if value is not None]
    valid_downside = [value for value in downside if value is not None]
    metrics = {
        "sample_count": len(rows),
        "selected_sample_count": len(selected),
        "average_forward_return_pct": _average(valid_forward),
        "average_excess_return_vs_benchmark_pct": _average(valid_excess),
        "hit_rate": _ratio(
            sum(1 for row in selected if _outcome(row).get("hit_above_threshold") is True),
            len(selected),
        ),
        "average_downside_pct": _average(valid_downside),
    }
    if score_key is not None:
        pairs = [
            (_number(row.get(score_key)), _number(_outcome(row).get("excess_return_vs_benchmark_pct")))
            for row in rows
        ]
        metrics["confidence_outcome_correlation"] = _pearson(
            [(score, value) for score, value in pairs if score is not None and value is not None]
        )
    return metrics


def metrics_by_window(
    rows: Sequence[Mapping[str, Any]],
    *,
    score_key: str | None = None,
    selection: Callable[[Mapping[str, Any]], bool] | None = None,
    windows: Sequence[int] = (5, 10, 20),
) -> dict[str, dict[str, Any]]:
    return {
        str(window): outcome_metrics(
            [row for row in rows if int(row.get("window_days") or 0) == window],
            score_key=score_key,
            selection=selection,
        )
        for window in windows
    }


def block_bootstrap_delta(
    before_rows: Sequence[Mapping[str, Any]],
    after_rows: Sequence[Mapping[str, Any]],
    *,
    metric: Callable[[Sequence[Mapping[str, Any]]], float | None],
    block_key: str,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    before_by_block = _rows_by_block(before_rows, block_key)
    after_by_block = _rows_by_block(after_rows, block_key)
    blocks = sorted(set(before_by_block) & set(after_by_block))
    if not blocks:
        return {
            "seed": seed,
            "iterations": iterations,
            "block_count": 0,
            "delta": None,
            "ci_95": [None, None],
        }
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(iterations):
        sampled = [rng.choice(blocks) for _ in blocks]
        before_sample = [
            row
            for block in sampled
            for row in before_by_block[block]
        ]
        after_sample = [
            row
            for block in sampled
            for row in after_by_block[block]
        ]
        before_value = metric(before_sample)
        after_value = metric(after_sample)
        if before_value is not None and after_value is not None:
            deltas.append(after_value - before_value)
    before_value = metric(before_rows)
    after_value = metric(after_rows)
    return {
        "seed": seed,
        "iterations": iterations,
        "block_count": len(blocks),
        "delta": _rounded_delta(after_value, before_value),
        "ci_95": [
            _percentile(deltas, 0.025),
            _percentile(deltas, 0.975),
        ],
    }


def confidence_excess_correlation(rows: Sequence[Mapping[str, Any]]) -> float | None:
    pairs = [
        (_number(row.get("replayed_score")), _number(_outcome(row).get("excess_return_vs_benchmark_pct")))
        for row in rows
    ]
    return _pearson([(score, value) for score, value in pairs if score is not None and value is not None])


def validated_coverage(validated: int, expected: int) -> float | None:
    return _ratio(validated, expected)


def _rows_by_block(
    rows: Iterable[Mapping[str, Any]],
    block_key: str,
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(block_key) or "unknown")].append(row)
    return grouped


def _outcome(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("outcome")
    return value if isinstance(value, Mapping) else {}


def _average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _pearson(pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    denominator = math.sqrt(variance_x * variance_y)
    if denominator == 0:
        return None
    return round(covariance / denominator, 4)


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rounded_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


__all__ = [
    "DEFAULT_BOOTSTRAP_ITERATIONS",
    "DEFAULT_BOOTSTRAP_SEED",
    "DEFAULT_MIN_VALIDATED_COVERAGE",
    "block_bootstrap_delta",
    "confidence_excess_correlation",
    "metrics_by_window",
    "month_bounds",
    "month_key",
    "outcome_metrics",
    "select_training_and_holdout_months",
    "validated_coverage",
]
