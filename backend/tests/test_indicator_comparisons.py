from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from ai_stock_sentinel.analysis.application.response_builder import (
    _hydrate_cached_technical_payload,
    compute_technical_indicators,
    extract_indicators,
)
from ai_stock_sentinel.analysis.schemas import AnalyzeResponse
from ai_stock_sentinel.technical.profile import (
    TECHNICAL_LAYER_VERSION,
    build_technical_profile_from_snapshot,
)


def snapshot(
    closes: list[float],
    volumes: list[float] | None = None,
    *,
    start: date = date(2026, 1, 1),
) -> dict[str, Any]:
    dates = [(start + timedelta(days=i)).isoformat() for i in range(len(closes))]
    return {
        "recent_closes": closes,
        "recent_volumes": volumes if volumes is not None else [100] * len(closes),
        "recent_close_dates": dates,
        "recent_volume_dates": dates,
        "fetched_at": dates[-1] + "T08:00:00+00:00",
        "current_price": closes[-1],
    }


def test_macd_daily_recovery_is_independent_of_three_bar_decline() -> None:
    result = compute_technical_indicators(snapshot([100.0] * 60 + [110, 90, 85, 100]))
    assert result.macd_hist_change_1d > 0
    assert result.macd_hist_slope_pct_3d < 0
    assert result.macd_hist_trend == "accelerating_bearish"
    assert result.macd_hist - result.macd_hist_previous == pytest.approx(result.macd_hist_change_1d)


def test_obv_daily_change_uses_same_start_even_when_window_rolls() -> None:
    previous = compute_technical_indicators(snapshot([100, 101, 102], [1, 100, 10]))
    current = compute_technical_indicators(
        snapshot([101, 102, 103], [100, 10, 20], start=date(2026, 1, 2))
    )
    assert previous.obv == 110
    assert current.obv == 30
    assert current.obv_previous == 10
    assert current.obv_change_1d == 20
    assert current.obv_start_date == "2026-01-02"
    assert current.indicator_previous_date == "2026-01-03"
    assert current.indicator_data_date == "2026-01-04"


@pytest.mark.parametrize("last, expected", [(103, 20), (101, -20), (102, 0)])
def test_obv_daily_change_sign(last: float, expected: float) -> None:
    result = compute_technical_indicators(snapshot([100, 102, last], [1, 10, 20]))
    assert result.obv_change_1d == expected


def test_missing_history_does_not_fabricate_comparisons() -> None:
    result = compute_technical_indicators(snapshot([100]))
    assert result.macd_hist_previous is None
    assert result.macd_hist_change_1d is None
    assert result.obv_previous is None
    assert result.obv_change_1d is None
    assert result.indicator_previous_date is None


def test_comparison_fields_survive_persistence_and_cached_response_hydration() -> None:
    source = snapshot([100.0] * 60 + [110, 90, 85, 100])
    current = compute_technical_indicators(source)
    persisted = extract_indicators({"snapshot": source}, is_final=True)
    assert persisted["macd_hist_change_1d"] == current.macd_hist_change_1d
    assert persisted["obv_change_1d"] == current.obv_change_1d
    cached = AnalyzeResponse(
        technical_indicators={"obv": -999, "macd_hist": -999},
        technical_profile={"version": TECHNICAL_LAYER_VERSION},
    )
    _hydrate_cached_technical_payload(cached, snapshot=source, is_final=True)
    assert cached.technical_indicators.obv == current.obv
    assert cached.technical_indicators.obv_previous == current.obv_previous
    assert cached.technical_indicators.macd_hist == current.macd_hist
    assert cached.technical_indicators.macd_hist_previous == current.macd_hist_previous


def test_intraday_comparison_and_completed_trend_have_separate_dates() -> None:
    source = snapshot([100.0] * 60 + [110, 90, 85, 100])
    raw = build_technical_profile_from_snapshot(source, is_final=False)["technical_indicators"]
    assert raw["indicator_data_date"] == source["recent_close_dates"][-1]
    assert raw["macd_trend_data_date"] == source["recent_close_dates"][-2]
    assert raw["macd_hist_change_1d"] > 0
    assert raw["macd_hist_slope_pct_3d"] < 0


def test_obv_comparison_rejects_mismatched_volume_dates() -> None:
    source = snapshot([100, 102, 103], [1, 10, 20])
    source["recent_volume_dates"] = ["2025-12-31", "2026-01-01", "2026-01-02"]
    result = compute_technical_indicators(source)
    assert result.obv_change_1d is None
    assert result.obv_previous is None
    assert result.obv_start_date is None


def test_missing_dates_remain_unknown_instead_of_using_quote_date() -> None:
    source = snapshot([100, 102, 103], [1, 10, 20])
    source.pop("recent_close_dates")
    source.pop("recent_volume_dates")
    result = compute_technical_indicators(source)
    assert result.obv_change_1d == 20
    assert result.indicator_data_date is None
    assert result.indicator_previous_date is None
    assert result.obv_start_date is None


def test_short_macd_history_does_not_fabricate_previous_histogram() -> None:
    result = compute_technical_indicators(snapshot([100.0] * 35))
    assert result.macd_hist == 0
    assert result.macd_hist_previous is None
    assert result.macd_hist_change_1d is None
