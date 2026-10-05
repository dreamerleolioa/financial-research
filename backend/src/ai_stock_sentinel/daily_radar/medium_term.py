"""Point-in-time trend evidence; weights and forward horizons remain unchanged."""
from collections.abc import Mapping
from datetime import date
from math import isfinite
from typing import Any

MEDIUM_TERM_VERSION = "medium-term-v1"
SELECTION_VERSION = "quality-selection-v1"


def build_medium_term_context(record: Mapping[str, Any], market_context: Mapping[str, Any]) -> dict[str, Any]:
    as_of = str(record.get("record_date") or "")
    result = {"version": MEDIUM_TERM_VERSION, "as_of_date": as_of,
              "trend_status": "unknown", "missing_reason": None, "aligned_dates": [],
              "relative_strength_20d": None, "relative_strength_60d": None,
              "ma20": None, "ma60": None, "ma60_slope_5d_pct": None}
    benchmark = market_context.get("benchmark") or {}
    candidate = _prices(record.get("price_history"), as_of)
    prices = _prices(benchmark.get("price_history"), as_of)
    if not prices or max(prices) != as_of:
        return result | {"missing_reason": "benchmark_not_current"}
    days = sorted(prices)[-65:]
    if len(days) < 65:
        return result | {"missing_reason": "insufficient_benchmark_history"}
    if any(day not in candidate for day in days):
        return result | {"missing_reason": "candidate_history_gap"}
    values = [candidate[day] for day in days]
    ma20, ma60 = sum(values[-20:]) / 20, sum(values[-60:]) / 60
    prior_ma60 = sum(values[-65:-5]) / 60
    rs20 = values[-1] / values[-21] - prices[days[-1]] / prices[days[-21]]
    rs60 = values[-1] / values[-61] - prices[days[-1]] / prices[days[-61]]
    constructive = values[-1] >= ma20 >= ma60 and ma60 > prior_ma60 and rs60 > 0
    return result | {"trend_status": "constructive" if constructive else "weak",
                     "aligned_dates": days, "relative_strength_20d": round(rs20, 6),
                     "relative_strength_60d": round(rs60, 6), "ma20": round(ma20, 4),
                     "ma60": round(ma60, 4), "ma60_slope_5d_pct": round((ma60 / prior_ma60 - 1) * 100, 4)}


def _prices(history: Any, as_of: str) -> dict[str, float]:
    result = {}
    for row in history or []:
        if not isinstance(row, Mapping):
            continue
        day = str(row.get("date") or "")
        try:
            date.fromisoformat(day)
            value = float(row.get("close"))
        except (ValueError, TypeError):
            continue
        if day <= as_of and isfinite(value) and value > 0:
            result[day] = value
    return result
