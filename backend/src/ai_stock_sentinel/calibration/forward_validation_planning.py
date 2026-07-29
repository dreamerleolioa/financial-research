from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from ai_stock_sentinel.calibration.forward_validation import (
    ForwardValidationAdapter,
    benchmark_requires_forward_price_refresh,
    candidate_key,
    close_on,
    future_price_rows,
    merge_price_series,
    normalize_price_series,
    ordered_positive_values,
    parse_date,
    symbols_requiring_forward_price_refresh,
)


class ForwardPriceFetcher(Protocol):
    def __call__(
        self,
        symbols: Sequence[str],
        *,
        start_date: date,
        end_date: date,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]: ...


@dataclass(frozen=True)
class PreparedForwardValidation:
    price_series_by_symbol: dict[str, list[dict[str, Any]]]
    benchmark_prices: list[dict[str, Any]]
    evaluation_windows_by_candidate: dict[str, list[int]]


def prepare_due_forward_validation(
    candidates: Iterable[Mapping[str, Any]],
    *,
    adapter: ForwardValidationAdapter,
    pending_windows_by_candidate: Mapping[str, Sequence[int]],
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    benchmark_symbol: str,
    as_of_date: date,
    price_start_date: date,
    fetch_prices: ForwardPriceFetcher,
) -> PreparedForwardValidation:
    candidate_list = list(candidates)
    seeded_prices = dict(price_series_by_symbol)
    if benchmark_prices and not seeded_prices.get(benchmark_symbol):
        seeded_prices[benchmark_symbol] = benchmark_prices
    price_series = merge_price_series(seeded_prices, {})
    current_benchmark = price_series.get(benchmark_symbol, [])

    if benchmark_requires_forward_price_refresh(
        candidate_list,
        windows_by_candidate=pending_windows_by_candidate,
        benchmark_prices=current_benchmark,
        as_of_date=as_of_date,
    ):
        fetched_prices = fetch_prices(
            [benchmark_symbol],
            start_date=price_start_date,
            end_date=as_of_date,
        )
        price_series = merge_price_series(price_series, fetched_prices)
        current_benchmark = price_series.get(benchmark_symbol, current_benchmark)

    refresh_symbols = symbols_requiring_forward_price_refresh(
        candidate_list,
        windows_by_candidate=pending_windows_by_candidate,
        price_series_by_symbol=price_series,
        benchmark_prices=current_benchmark,
        as_of_date=as_of_date,
    )
    if refresh_symbols:
        fetched_prices = fetch_prices(
            refresh_symbols,
            start_date=price_start_date,
            end_date=as_of_date,
        )
        price_series = merge_price_series(price_series, fetched_prices)
        current_benchmark = price_series.get(benchmark_symbol, current_benchmark)

    evaluation_windows = evaluation_ready_windows_by_candidate(
        candidate_list,
        adapter=adapter,
        as_of_date=as_of_date,
        pending_windows_by_candidate=pending_windows_by_candidate,
        price_series_by_symbol=price_series,
        benchmark_prices=current_benchmark,
    )
    return PreparedForwardValidation(
        price_series_by_symbol=price_series,
        benchmark_prices=current_benchmark,
        evaluation_windows_by_candidate=evaluation_windows,
    )


def evaluation_ready_windows_by_candidate(
    candidates: Iterable[Mapping[str, Any]],
    *,
    adapter: ForwardValidationAdapter,
    as_of_date: date,
    pending_windows_by_candidate: Mapping[str, Sequence[int]],
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_prices: Sequence[Mapping[str, Any]],
) -> dict[str, list[int]]:
    benchmark_by_date = normalize_price_series(benchmark_prices)
    ready_by_candidate: dict[str, list[int]] = {}
    for candidate in candidates:
        key = candidate_key(candidate)
        pending_windows = ordered_positive_values(
            pending_windows_by_candidate.get(key, [])
        )
        if not pending_windows:
            continue
        signal_date = parse_date(candidate.get("record_date"))
        if (
            signal_date is None
            or signal_date > as_of_date
            or adapter.freshness_status(candidate) == "stale"
        ):
            ready_by_candidate[key] = pending_windows
            continue

        symbol = str(candidate.get("symbol") or "")
        candidate_by_date = normalize_price_series(
            price_series_by_symbol.get(symbol, [])
        )
        benchmark_future_rows = future_price_rows(
            benchmark_by_date,
            signal_date,
            as_of_date,
        )
        benchmark_entry_available = close_on(benchmark_by_date, signal_date) is not None
        calendar_days_elapsed = (as_of_date - signal_date).days
        ready_windows: list[int] = []
        for window in pending_windows:
            benchmark_window_dates = [
                row_date
                for row_date, _row in benchmark_future_rows[:window]
            ]
            complete_on_benchmark_calendar = (
                benchmark_entry_available
                and len(benchmark_window_dates) == window
                and all(
                    row_date in candidate_by_date
                    for row_date in benchmark_window_dates
                )
            )
            if complete_on_benchmark_calendar or calendar_days_elapsed >= window * 2:
                ready_windows.append(window)
        if ready_windows:
            ready_by_candidate[key] = ready_windows
    return ready_by_candidate


__all__ = [
    "ForwardPriceFetcher",
    "PreparedForwardValidation",
    "evaluation_ready_windows_by_candidate",
    "prepare_due_forward_validation",
]
