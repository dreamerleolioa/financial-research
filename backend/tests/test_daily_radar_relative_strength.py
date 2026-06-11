from __future__ import annotations

from datetime import date, timedelta

from ai_stock_sentinel.daily_radar.relative_strength import calculate_relative_strength


def _history(
    start: date,
    closes: list[float],
    *,
    skip_offsets: set[int] | None = None,
) -> list[dict[str, object]]:
    skipped = skip_offsets or set()
    return [
        {"date": (start + timedelta(days=index)).isoformat(), "close": close}
        for index, close in enumerate(closes)
        if index not in skipped
    ]


def test_relative_strength_calculates_return_spread_over_lookback_window() -> None:
    candidate = _history(date(2026, 5, 1), [100.0 + index for index in range(21)])
    benchmark = _history(date(2026, 5, 1), [100.0 + index * 0.5 for index in range(21)])

    result = calculate_relative_strength(
        symbol="2330.TW",
        candidate_price_history=candidate,
        benchmark_price_history=benchmark,
        benchmark_symbol="TAIEX",
        run_date=date(2026, 5, 21),
        lookback_days=20,
    )

    assert result["freshness"] == "fresh"
    assert result["candidate_return"] == 0.2
    assert result["benchmark_return"] == 0.1
    assert result["relative_value"] == 0.1
    assert result["score"] == 6
    assert result["window_start"] == "2026-05-01"
    assert result["window_end"] == "2026-05-21"
    assert result["replay_key"] == "relative_strength:2330.TW:TAIEX:2026-05-21:L20"


def test_relative_strength_aligns_only_shared_trading_dates() -> None:
    candidate = _history(date(2026, 5, 1), [100.0, 103.0, 106.0, 109.0, 112.0], skip_offsets={2})
    benchmark = _history(date(2026, 5, 1), [100.0, 101.0, 102.0, 103.0, 104.0], skip_offsets={1})

    result = calculate_relative_strength(
        symbol="2454.TW",
        candidate_price_history=candidate,
        benchmark_price_history=benchmark,
        benchmark_symbol="TAIEX",
        run_date=date(2026, 5, 5),
        lookback_days=2,
    )

    assert result["freshness"] == "fresh"
    assert result["aligned_dates"] == ["2026-05-01", "2026-05-04", "2026-05-05"]
    assert result["candidate_return"] == 0.12
    assert result["benchmark_return"] == 0.04
    assert result["relative_value"] == 0.08


def test_relative_strength_records_missing_reason_when_aligned_history_is_insufficient() -> None:
    result = calculate_relative_strength(
        symbol="2303.TW",
        candidate_price_history=_history(date(2026, 5, 1), [100.0, 101.0]),
        benchmark_price_history=_history(date(2026, 5, 1), [100.0, 101.0]),
        benchmark_symbol="TAIEX",
        run_date=date(2026, 5, 2),
        lookback_days=5,
    )

    assert result["freshness"] == "missing"
    assert result["missing_reason"] == "insufficient_aligned_history"
    assert result["relative_value"] is None
    assert result["score"] == 0


def test_relative_strength_records_stale_benchmark_without_faking_neutral_value() -> None:
    result = calculate_relative_strength(
        symbol="3034.TW",
        candidate_price_history=_history(date(2026, 5, 1), [100.0 + index for index in range(21)]),
        benchmark_price_history=_history(date(2026, 5, 1), [100.0 + index for index in range(21)]),
        benchmark_symbol="TAIEX",
        run_date=date(2026, 5, 25),
        lookback_days=20,
        benchmark_data_date=date(2026, 5, 21),
    )

    assert result["freshness"] == "stale"
    assert result["missing_reason"] == "benchmark_stale"
    assert result["relative_value"] is None
    assert result["score"] == 0
