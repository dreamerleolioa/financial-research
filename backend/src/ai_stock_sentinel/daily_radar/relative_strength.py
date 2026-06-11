from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any


DEFAULT_RELATIVE_STRENGTH_LOOKBACK_DAYS = 20
MAX_BENCHMARK_LAG_DAYS = 2


def calculate_relative_strength(
    *,
    symbol: str,
    candidate_price_history: Iterable[Mapping[str, Any]] | None,
    benchmark_price_history: Iterable[Mapping[str, Any]] | None,
    benchmark_symbol: str,
    run_date: date,
    lookback_days: int = DEFAULT_RELATIVE_STRENGTH_LOOKBACK_DAYS,
    benchmark_data_date: date | str | None = None,
) -> dict[str, Any]:
    candidate_prices = _price_by_date(candidate_price_history)
    benchmark_prices = _price_by_date(benchmark_price_history)
    benchmark_as_of = _parse_date(benchmark_data_date) or (max(benchmark_prices) if benchmark_prices else None)

    base_trace = {
        "benchmark_symbol": benchmark_symbol,
        "lookback_days": lookback_days,
        "candidate_return": None,
        "benchmark_return": None,
        "relative_value": None,
        "score": 0,
        "weight": 1.0,
        "freshness": "missing",
        "missing_reason": None,
        "data_dates": {},
        "aligned_dates": [],
    }
    if not candidate_prices:
        return base_trace | {"missing_reason": "candidate_price_history_missing"}
    if not benchmark_prices:
        return base_trace | {"missing_reason": "benchmark_price_history_missing"}
    if benchmark_as_of is None:
        return base_trace | {"missing_reason": "benchmark_data_date_missing"}
    if (run_date - benchmark_as_of).days > MAX_BENCHMARK_LAG_DAYS:
        return base_trace | {
            "freshness": "stale",
            "missing_reason": "benchmark_stale",
            "data_dates": {"relative_strength_benchmark": benchmark_as_of.isoformat()},
        }

    common_dates = sorted(date_value for date_value in candidate_prices if date_value in benchmark_prices and date_value <= run_date)
    required_points = lookback_days + 1
    if len(common_dates) < required_points:
        return base_trace | {
            "missing_reason": "insufficient_aligned_history",
            "data_dates": _data_dates(candidate_prices, benchmark_prices, common_dates, benchmark_as_of),
            "aligned_dates": [value.isoformat() for value in common_dates],
        }

    window_dates = common_dates[-required_points:]
    start_date = window_dates[0]
    end_date = window_dates[-1]
    candidate_return = _return(candidate_prices[start_date], candidate_prices[end_date])
    benchmark_return = _return(benchmark_prices[start_date], benchmark_prices[end_date])
    if candidate_return is None or benchmark_return is None:
        return base_trace | {
            "missing_reason": "invalid_aligned_price_history",
            "data_dates": _data_dates(candidate_prices, benchmark_prices, window_dates, benchmark_as_of),
            "aligned_dates": [value.isoformat() for value in window_dates],
        }

    relative_value = candidate_return - benchmark_return
    return {
        **base_trace,
        "candidate_return": round(candidate_return, 6),
        "benchmark_return": round(benchmark_return, 6),
        "relative_value": round(relative_value, 6),
        "score": _score(relative_value),
        "freshness": "fresh",
        "data_dates": _data_dates(candidate_prices, benchmark_prices, window_dates, benchmark_as_of),
        "aligned_dates": [value.isoformat() for value in window_dates],
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "replay_key": _replay_key(symbol, benchmark_symbol, end_date, lookback_days),
    }


def _price_by_date(price_history: Iterable[Mapping[str, Any]] | None) -> dict[date, float]:
    prices: dict[date, float] = {}
    for item in price_history or []:
        item_date = _parse_date(item.get("date"))
        close = _float(item.get("close"))
        if item_date is None or close is None or close <= 0:
            continue
        prices[item_date] = close
    return prices


def _data_dates(
    candidate_prices: Mapping[date, float],
    benchmark_prices: Mapping[date, float],
    aligned_dates: list[date],
    benchmark_as_of: date,
) -> dict[str, str]:
    data_dates: dict[str, str] = {"relative_strength_benchmark": benchmark_as_of.isoformat()}
    if candidate_prices:
        data_dates["relative_strength_candidate"] = max(candidate_prices).isoformat()
    if benchmark_prices:
        data_dates["relative_strength_benchmark"] = max(benchmark_prices).isoformat()
    if aligned_dates:
        data_dates["relative_strength"] = aligned_dates[-1].isoformat()
    return data_dates


def _return(start_price: float, end_price: float) -> float | None:
    if start_price <= 0:
        return None
    return (end_price / start_price) - 1


def _score(relative_value: float) -> int:
    if relative_value >= 0.05:
        return 6
    if relative_value >= 0.02:
        return 3
    if relative_value <= -0.05:
        return -6
    if relative_value <= -0.02:
        return -3
    return 0


def _replay_key(symbol: str, benchmark_symbol: str, end_date: date, lookback_days: int) -> str:
    return f"relative_strength:{symbol}:{benchmark_symbol}:{end_date.isoformat()}:L{lookback_days}"


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_RELATIVE_STRENGTH_LOOKBACK_DAYS",
    "MAX_BENCHMARK_LAG_DAYS",
    "calculate_relative_strength",
]
