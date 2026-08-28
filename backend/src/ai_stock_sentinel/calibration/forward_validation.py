from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


DEFAULT_FORWARD_WINDOWS = (5, 10, 20)
DEFAULT_BENCHMARK_SYMBOL = "TAIEX"
DEFAULT_HIT_THRESHOLD_PCT = 0.0
DEFAULT_DUE_LOOKBACK_MULTIPLIER = 10
TERMINAL_FORWARD_VALIDATION_SKIP_REASONS = frozenset({"stale_candidate_price"})


def is_terminal_forward_validation_skip_reason(reason: Any) -> bool:
    return str(reason or "") in TERMINAL_FORWARD_VALIDATION_SKIP_REASONS


@dataclass(frozen=True)
class ForwardValidationAdapter:
    candidate_snapshot: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    entry_price: Callable[[Mapping[str, Any], Mapping[date, Mapping[str, float]], date], float | None]
    defense_reference: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    freshness_status: Callable[[Mapping[str, Any]], str]


@dataclass(frozen=True)
class ForwardValidationEvaluation:
    outcomes: list[dict[str, Any]]
    skipped_reasons: dict[str, int]
    active_windows: list[int]
    candidate_count: int


def evaluate_forward_validation(
    candidates: Iterable[Mapping[str, Any]],
    *,
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    adapter: ForwardValidationAdapter,
    as_of_date: date | None = None,
    windows: Sequence[int] = DEFAULT_FORWARD_WINDOWS,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
    validation_version: str,
    hit_threshold_pct: float = DEFAULT_HIT_THRESHOLD_PCT,
    windows_by_candidate: Mapping[str, Sequence[int]] | None = None,
) -> ForwardValidationEvaluation:
    active_windows = ordered_positive_values(windows)
    candidate_list = [dict(candidate) for candidate in candidates]
    outcomes: list[dict[str, Any]] = []
    skipped_reasons: dict[str, int] = {}
    for candidate in candidate_list:
        candidate_windows = active_windows
        if windows_by_candidate is not None:
            candidate_windows = ordered_positive_values(
                windows_by_candidate.get(candidate_key(candidate), [])
            )
        for window_days in candidate_windows:
            outcome = evaluate_forward_window(
                candidate,
                price_series=price_series_by_symbol.get(str(candidate.get("symbol"))) or [],
                benchmark_prices=benchmark_prices,
                adapter=adapter,
                window_days=window_days,
                as_of_date=as_of_date,
                benchmark_symbol=benchmark_symbol,
                validation_version=validation_version,
                hit_threshold_pct=hit_threshold_pct,
            )
            outcomes.append(outcome)
            if outcome["status"] == "skipped":
                reason = str(outcome["skip_reason"])
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
    return ForwardValidationEvaluation(
        outcomes=outcomes,
        skipped_reasons=dict(sorted(skipped_reasons.items())),
        active_windows=active_windows,
        candidate_count=len(candidate_list),
    )


def evaluate_forward_window(
    candidate: Mapping[str, Any],
    *,
    price_series: Sequence[Mapping[str, Any]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    adapter: ForwardValidationAdapter,
    window_days: int,
    as_of_date: date | None,
    benchmark_symbol: str,
    validation_version: str,
    hit_threshold_pct: float,
) -> dict[str, Any]:
    signal_date = parse_date(candidate.get("record_date"))
    symbol = str(candidate.get("symbol") or "")
    base = {
        "candidate_id": candidate.get("candidate_id"),
        "symbol": symbol,
        "signal_date": signal_date.isoformat() if signal_date is not None else None,
        "window_days": int(window_days),
        "validation_version": validation_version,
        "benchmark_symbol": benchmark_symbol,
        "evaluation_as_of_date": as_of_date.isoformat() if as_of_date is not None else None,
        "candidate_snapshot": dict(adapter.candidate_snapshot(candidate)),
    }
    if signal_date is None:
        return _skip(base, "signal_date_missing")
    if as_of_date is not None and signal_date > as_of_date:
        return _skip(base, "future_signal_date")
    if adapter.freshness_status(candidate) == "stale":
        return _skip(base, "stale_candidate_price")

    candidate_prices = normalize_price_series(price_series)
    benchmark_by_date = normalize_price_series(benchmark_prices)
    entry_price = adapter.entry_price(candidate, candidate_prices, signal_date)
    if entry_price is None:
        return _skip(base, "missing_candidate_entry_price")

    candidate_future_rows = future_price_rows(candidate_prices, signal_date, as_of_date)
    if len(candidate_future_rows) < window_days:
        return _skip(base, "missing_future_price")

    benchmark_entry = close_on(benchmark_by_date, signal_date)
    benchmark_future_rows = future_price_rows(benchmark_by_date, signal_date, as_of_date)
    if benchmark_entry is None or len(benchmark_future_rows) < window_days:
        return _skip(base, "missing_benchmark")
    benchmark_window_rows = benchmark_future_rows[:window_days]
    window_rows = [
        (row_date, candidate_prices[row_date])
        for row_date, _benchmark_row in benchmark_window_rows
        if row_date in candidate_prices
    ]
    if len(window_rows) < window_days:
        return _skip(base, "missing_future_price")
    target_date, target_price = window_rows[-1][0], window_rows[-1][1]["close"]
    benchmark_target = benchmark_window_rows[-1][1]["close"]

    if entry_price <= 0 or target_price <= 0 or benchmark_entry <= 0 or benchmark_target <= 0:
        return _skip(base, "invalid_price")

    highs = [row["high"] for _row_date, row in window_rows]
    lows = [row["low"] for _row_date, row in window_rows]
    forward_return_pct = pct_return(entry_price, target_price)
    benchmark_return_pct = pct_return(benchmark_entry, benchmark_target)
    defense_reference = dict(adapter.defense_reference(candidate))
    defense_value = number(defense_reference.get("value"))
    outcome = {
        "forward_return_pct": forward_return_pct,
        "benchmark_return_pct": benchmark_return_pct,
        "excess_return_vs_benchmark_pct": rounded(forward_return_pct - benchmark_return_pct),
        "max_favorable_excursion_pct": pct_return(entry_price, max(highs)),
        "max_adverse_excursion_pct": pct_return(entry_price, min(lows)),
        "close_below_defense_reference": (
            target_price < defense_value if defense_value is not None else None
        ),
        "defense_reference": defense_reference,
        "hit_above_threshold": forward_return_pct > hit_threshold_pct,
        "entry_price": rounded(entry_price),
        "target_price": rounded(target_price),
        "target_date": target_date.isoformat(),
    }
    return base | {
        "status": "validated",
        "target_date": target_date.isoformat(),
        "skip_reason": None,
        "outcome": outcome,
    }


def default_due_start_date(
    as_of_date: date,
    max_window: int = max(DEFAULT_FORWARD_WINDOWS),
) -> date:
    return as_of_date - timedelta(
        days=int(max_window) * DEFAULT_DUE_LOOKBACK_MULTIPLIER
    )


def discover_due_windows_by_candidate(
    candidates: Iterable[Mapping[str, Any]],
    *,
    adapter: ForwardValidationAdapter,
    as_of_date: date,
    windows: Sequence[int],
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    benchmark_prices: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, list[int]]:
    active_windows = ordered_positive_values(windows)
    price_series = price_series_by_symbol or {}
    benchmark_by_date = normalize_price_series(benchmark_prices or [])
    due_by_candidate: dict[str, list[int]] = {}
    for candidate in candidates:
        signal_date = parse_date(candidate.get("record_date"))
        key = candidate_key(candidate)
        if (
            signal_date is None
            or signal_date > as_of_date
            or adapter.freshness_status(candidate) == "stale"
        ):
            due_by_candidate[key] = active_windows
            continue
        symbol = str(candidate.get("symbol") or "")
        candidate_by_date = normalize_price_series(price_series.get(symbol, []))
        available_trading_rows = max(
            len(future_price_rows(candidate_by_date, signal_date, as_of_date)),
            len(future_price_rows(benchmark_by_date, signal_date, as_of_date)),
        )
        if available_trading_rows == 0 and (not candidate_by_date or not benchmark_by_date):
            calendar_days_elapsed = (as_of_date - signal_date).days
            conservatively_due = [
                window
                for window in active_windows
                if calendar_days_elapsed >= window * 2
            ]
            if conservatively_due:
                due_by_candidate[key] = conservatively_due
            continue
        due_windows = [
            window
            for window in active_windows
            if available_trading_rows >= window
        ]
        if due_windows:
            due_by_candidate[key] = due_windows
    return due_by_candidate


def due_windows_by_candidate(
    candidates: Iterable[Mapping[str, Any]],
    *,
    adapter: ForwardValidationAdapter,
    as_of_date: date,
    windows: Sequence[int],
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    benchmark_prices: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, list[int]]:
    return discover_due_windows_by_candidate(
        candidates,
        adapter=adapter,
        as_of_date=as_of_date,
        windows=windows,
        price_series_by_symbol=price_series_by_symbol,
        benchmark_prices=benchmark_prices,
    )


def symbols_requiring_forward_price_refresh(
    candidates: Iterable[Mapping[str, Any]],
    *,
    windows_by_candidate: Mapping[str, Sequence[int]],
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_prices: Sequence[Mapping[str, Any]] | None = None,
    as_of_date: date,
) -> list[str]:
    required: set[str] = set()
    benchmark_by_date = normalize_price_series(benchmark_prices or [])
    for candidate in candidates:
        pending_windows = windows_by_candidate.get(candidate_key(candidate), [])
        signal_date = parse_date(candidate.get("record_date"))
        symbol = str(candidate.get("symbol") or "")
        if not pending_windows or signal_date is None or not symbol:
            continue
        candidate_by_date = normalize_price_series(
            price_series_by_symbol.get(symbol, [])
        )
        available_rows = len(
            future_price_rows(candidate_by_date, signal_date, as_of_date)
        )
        max_window = max(int(window) for window in pending_windows)
        benchmark_dates = [
            row_date
            for row_date, _row in future_price_rows(
                benchmark_by_date,
                signal_date,
                as_of_date,
            )[:max_window]
        ]
        if available_rows < max_window or any(
            row_date not in candidate_by_date
            for row_date in benchmark_dates
        ):
            required.add(symbol)
    return sorted(required)


def benchmark_requires_forward_price_refresh(
    candidates: Iterable[Mapping[str, Any]],
    *,
    windows_by_candidate: Mapping[str, Sequence[int]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    as_of_date: date,
) -> bool:
    benchmark_by_date = normalize_price_series(benchmark_prices)
    for candidate in candidates:
        pending_windows = windows_by_candidate.get(candidate_key(candidate), [])
        signal_date = parse_date(candidate.get("record_date"))
        if not pending_windows or signal_date is None:
            continue
        if close_on(benchmark_by_date, signal_date) is None:
            return True
        available_rows = len(
            future_price_rows(benchmark_by_date, signal_date, as_of_date)
        )
        if available_rows < max(int(window) for window in pending_windows):
            return True
    return False


def merge_price_series(
    existing: Mapping[str, Sequence[Mapping[str, Any]]],
    fetched: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    symbols = set(existing) | set(fetched)
    merged: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        rows_by_date: dict[str, dict[str, Any]] = {}
        for row in [*existing.get(symbol, []), *fetched.get(symbol, [])]:
            row_date = parse_date(row.get("date"))
            close = number(row.get("close"))
            if row_date is None or close is None or close <= 0:
                continue
            rows_by_date[row_date.isoformat()] = dict(row)
        merged[symbol] = [rows_by_date[key] for key in sorted(rows_by_date)]
    return merged


def normalize_price_series(
    price_series: Sequence[Mapping[str, Any]],
) -> dict[date, dict[str, float]]:
    prices: dict[date, dict[str, float]] = {}
    for row in price_series:
        row_date = parse_date(row.get("date"))
        close = number(row.get("close"))
        if row_date is None or close is None or close <= 0:
            continue
        prices[row_date] = {
            "open": number_or_default(row.get("open"), close),
            "high": number_or_default(row.get("high"), close),
            "low": number_or_default(row.get("low"), close),
            "close": close,
        }
    return prices


def candidate_key(candidate: Mapping[str, Any]) -> str:
    candidate_id = candidate.get("candidate_id")
    if candidate_id is not None:
        return f"id:{candidate_id}"
    return f"{candidate.get('symbol') or ''}:{candidate.get('record_date') or ''}"


def close_on(
    prices: Mapping[date, Mapping[str, float]],
    row_date: date,
) -> float | None:
    row = prices.get(row_date)
    return row.get("close") if row is not None else None


def future_price_rows(
    prices: Mapping[date, Mapping[str, float]],
    signal_date: date,
    as_of_date: date | None,
) -> list[tuple[date, Mapping[str, float]]]:
    return [
        (row_date, row)
        for row_date, row in sorted(prices.items())
        if row_date > signal_date
        and (as_of_date is None or row_date <= as_of_date)
    ]


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def number_or_default(value: Any, default: float) -> float:
    parsed = number(value)
    return parsed if parsed is not None else default


def pct_return(start: float, end: float) -> float:
    return rounded(((end / start) - 1) * 100)


def rounded(value: float) -> float:
    return round(float(value), 4)


def ordered_positive_values(values: Sequence[int]) -> list[int]:
    return sorted({int(value) for value in values if int(value) > 0})


def _skip(base: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return dict(base) | {
        "status": "skipped",
        "target_date": None,
        "skip_reason": reason,
        "outcome": {},
    }


__all__ = [
    "DEFAULT_BENCHMARK_SYMBOL",
    "DEFAULT_FORWARD_WINDOWS",
    "ForwardValidationAdapter",
    "ForwardValidationEvaluation",
    "TERMINAL_FORWARD_VALIDATION_SKIP_REASONS",
    "benchmark_requires_forward_price_refresh",
    "candidate_key",
    "close_on",
    "default_due_start_date",
    "discover_due_windows_by_candidate",
    "due_windows_by_candidate",
    "evaluate_forward_validation",
    "evaluate_forward_window",
    "future_price_rows",
    "is_terminal_forward_validation_skip_reason",
    "merge_price_series",
    "normalize_price_series",
    "number",
    "number_or_default",
    "parse_date",
    "symbols_requiring_forward_price_refresh",
]
