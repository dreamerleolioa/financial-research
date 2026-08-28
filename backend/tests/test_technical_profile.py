from __future__ import annotations

import pytest

from ai_stock_sentinel.analysis import metrics as compatibility_metrics
from ai_stock_sentinel.technical import metrics as canonical_metrics
from ai_stock_sentinel.technical.profile import (
    TECHNICAL_LAYER_VERSION,
    TECHNICAL_METRICS_VERSION,
    build_technical_profile_from_snapshot,
    build_technical_profile_payload,
    project_technical_profile_without_composite_judgments,
)


def _series(length: int = 130) -> tuple[list[float], list[float], list[float], list[float]]:
    closes = [100.0 + index for index in range(length)]
    highs = [close + 2.0 for close in closes]
    lows = [close - 2.0 for close in closes]
    volumes = [1000.0 + index * 10 for index in range(length)]
    return closes, highs, lows, volumes


def test_analysis_metrics_reexports_canonical_technical_metrics() -> None:
    assert compatibility_metrics.ma is canonical_metrics.ma
    assert compatibility_metrics.calc_rsi is canonical_metrics.calc_rsi
    assert compatibility_metrics.macd is canonical_metrics.macd
    assert compatibility_metrics.stochastic_kd is canonical_metrics.stochastic_kd
    assert compatibility_metrics.atr is canonical_metrics.atr
    assert compatibility_metrics.obv is canonical_metrics.obv


def test_public_technical_profile_projection_removes_composite_judgments_without_mutation() -> None:
    original = {
        "version": "technical-layer-v2",
        "signal_conflicts": [{"message": "cached composite judgment"}],
        "temporal_evidence": {
            "ma20_slope": {"state": "rising"},
            "volatility_regime": {"state": "expansion"},
        },
    }

    projected = project_technical_profile_without_composite_judgments(original)

    assert projected is not None
    assert "signal_conflicts" not in projected
    assert "volatility_regime" not in projected["temporal_evidence"]
    assert "signal_conflicts" in original
    assert "volatility_regime" in original["temporal_evidence"]


def test_profile_builder_returns_raw_indicators_and_layered_profile() -> None:
    closes, highs, lows, volumes = _series()

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        data_date="2026-06-23",
        is_final=True,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    profile = payload["technical_profile"]

    assert raw["ma20"] == canonical_metrics.ma(closes, 20)
    assert raw["rsi14"] == canonical_metrics.calc_rsi(closes, period=14)
    assert raw["bias20"] is not None
    assert raw["volume_ratio"] is not None
    assert raw["avg_volume_20"] == pytest.approx(sum(volumes[-20:]) / 20)
    assert raw["avg_volume_60"] == pytest.approx(sum(volumes[-60:]) / 60)
    assert raw["macd_hist"] is not None
    assert raw["macd_hist_pct"] == pytest.approx(raw["macd_hist"] / closes[-1] * 100)
    assert raw["prior_high_20d"] == max(highs[-21:-1])
    assert raw["prior_low_20d"] == min(lows[-21:-1])
    assert profile["version"] == TECHNICAL_LAYER_VERSION
    assert profile["formula_versions"] == {
        "metrics": TECHNICAL_METRICS_VERSION,
        "layering": TECHNICAL_LAYER_VERSION,
    }
    assert set(profile["primary_score_inputs"]) == {
        "ma_structure",
        "support_resistance",
        "volume_ratio",
        "atr_risk",
        "macd_momentum",
        "obv_trend",
    }
    assert set(profile["risk_overheat_filters"]) == {
        "rsi_state",
        "bias_state",
        "bollinger_state",
        "atr_state",
    }
    assert set(profile["secondary_evidence"]) == {"adx", "donchian", "mfi", "kd"}
    assert set(profile["temporal_evidence"]) == {
        "ma20_slope",
        "ma60_slope",
        "macd_hist_trend",
    }
    assert all(signal["impact"] == 0 for signal in profile["temporal_evidence"].values())
    assert raw["ma20_slope_pct_5d"] is not None
    assert raw["ma60_slope_pct_10d"] is not None
    assert raw["macd_hist_slope_pct_3d"] is not None
    assert raw["atr_pct_percentile_60d"] is not None
    assert raw["bollinger_bandwidth_percentile_60d"] is not None
    assert "volatility_regime" not in raw
    assert "technical_conflicts" not in raw
    assert "signal_conflicts" not in profile


def test_prior_price_levels_exclude_the_signal_bar() -> None:
    closes = [100.0 + index for index in range(21)]
    highs = [close + 2.0 for close in closes]
    lows = [close - 2.0 for close in closes]
    highs[-1] = 999.0
    lows[-1] = 1.0

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=[1000.0] * len(closes),
        is_final=True,
    )

    assert payload is not None
    indicators = payload["technical_indicators"]
    assert indicators["high_20d"] == 999.0
    assert indicators["low_20d"] == 1.0
    assert indicators["prior_high_20d"] == max(highs[-21:-1])
    assert indicators["prior_low_20d"] == min(lows[-21:-1])


def test_intraday_prior_price_levels_keep_the_latest_completed_bar() -> None:
    closes = [100.0 + index for index in range(21)]
    highs = [close + 2.0 for close in closes]
    lows = [close - 2.0 for close in closes]
    highs[-1] = 999.0
    lows[-1] = 1.0
    close_dates = [f"2026-07-{index + 12:02d}" for index in range(20)] + ["2026-08-01"]

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=[1000.0] * len(closes),
        close_dates=close_dates,
        high_dates=close_dates,
        low_dates=close_dates,
        data_date="2026-08-01",
        observation_date="2026-08-02",
        is_final=False,
    )

    assert payload is not None
    indicators = payload["technical_indicators"]
    assert indicators["prior_high_20d"] == 999.0
    assert indicators["prior_low_20d"] == 1.0
    assert payload["technical_profile"]["data_quality"]["price_level_data_date"] == "2026-08-01"


def test_snapshot_price_levels_fail_closed_when_equal_length_ohlc_dates_do_not_align() -> None:
    closes, highs, lows, volumes = _series(length=61)
    close_dates = [f"2026-06-{index + 1:02d}" for index in range(30)] + [
        f"2026-07-{index + 1:02d}" for index in range(31)
    ]
    snapshot = {
        "recent_closes": closes,
        "recent_highs": highs,
        "recent_lows": lows,
        "recent_volumes": volumes,
        "recent_close_dates": close_dates,
        "recent_high_dates": [*close_dates[1:], "2026-08-01"],
        "recent_low_dates": close_dates,
        "recent_volume_dates": close_dates,
        "current_price": closes[-1],
        "data_dates": {"ohlcv": close_dates[-1]},
        "fetched_at": "2026-08-01T10:00:00+08:00",
    }

    payload = build_technical_profile_from_snapshot(snapshot, is_final=False)

    assert payload is not None
    assert payload["technical_indicators"]["prior_high_20d"] is None
    assert payload["technical_indicators"]["prior_low_20d"] is None
    data_quality = payload["technical_profile"]["data_quality"]
    assert data_quality["ohlcv_aligned"] is False
    assert data_quality["price_level_missing_reason"] == "ohlc_not_aligned"


@pytest.mark.parametrize(
    ("value", "close"),
    [
        (float("nan"), 100.0),
        (float("inf"), 100.0),
        (1.0, float("nan")),
        (1.0, float("inf")),
        (1.0, 0.0),
        (1.0, -100.0),
    ],
)
def test_normalized_price_value_rejects_non_finite_or_non_positive_inputs(
    value: float,
    close: float,
) -> None:
    assert canonical_metrics.normalize_price_value_pct(value, close) is None


def test_temporal_evidence_does_not_change_score_buckets() -> None:
    closes, highs, lows, volumes = _series()

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    profile = payload["technical_profile"]
    expected_total = max(
        -5,
        min(
            5,
            profile["score_summary"]["primary_score"]
            + profile["score_summary"]["risk_filter_score"]
            + profile["score_summary"]["secondary_score"],
        ),
    )
    assert profile["score_summary"]["capped_total"] == expected_total


def test_intraday_temporal_evidence_excludes_current_dated_bar() -> None:
    closes, highs, lows, volumes = _series(length=90)
    close_dates = [f"2026-07-{(index % 28) + 1:02d}" for index in range(89)] + ["2026-08-01"]
    spiked_closes = [*closes[:-1], closes[-1] * 3]
    spiked_highs = [*highs[:-1], spiked_closes[-1] + 2]
    spiked_lows = [*lows[:-1], spiked_closes[-1] - 2]

    intraday = build_technical_profile_payload(
        closes=spiked_closes,
        highs=spiked_highs,
        lows=spiked_lows,
        volumes=volumes,
        close_dates=close_dates,
        data_date="2026-07-31",
        observation_date="2026-08-01",
        is_final=False,
    )
    completed = build_technical_profile_payload(
        closes=spiked_closes[:-1],
        highs=spiked_highs[:-1],
        lows=spiked_lows[:-1],
        volumes=volumes[:-1],
        data_date="2026-07-05",
        is_final=True,
    )

    assert intraday is not None and completed is not None
    for field in (
        "ma20_slope_pct_5d",
        "ma60_slope_pct_10d",
        "macd_hist_slope_pct_3d",
        "atr_pct_percentile_60d",
        "bollinger_bandwidth_percentile_60d",
    ):
        assert intraday["technical_indicators"][field] == completed["technical_indicators"][field]
    assert intraday["technical_profile"]["data_quality"]["temporal_data_date"] == close_dates[-2]
    assert intraday["technical_profile"]["data_quality"]["temporal_completed_bars_only"] is True
    assert intraday["technical_profile"]["data_quality"]["temporal_missing_reason"] is None


def test_intraday_temporal_evidence_keeps_prior_completed_market_date() -> None:
    closes, highs, lows, volumes = _series(length=90)
    close_dates = [f"2026-07-{(index % 28) + 1:02d}" for index in range(89)] + ["2026-07-31"]

    intraday = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        close_dates=close_dates,
        data_date="2026-07-31",
        observation_date="2026-08-01",
        is_final=False,
    )
    completed = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        data_date="2026-07-31",
        is_final=True,
    )

    assert intraday is not None and completed is not None
    assert (
        intraday["technical_indicators"]["ma20_slope_pct_5d"]
        == completed["technical_indicators"]["ma20_slope_pct_5d"]
    )
    assert intraday["technical_profile"]["data_quality"]["temporal_data_date"] == "2026-07-31"


def test_intraday_temporal_evidence_fails_closed_without_dated_bars() -> None:
    closes, highs, lows, volumes = _series(length=90)

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        data_date="2026-08-01",
        is_final=False,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["ma20_slope_pct_5d"] is None
    assert raw["atr_pct_percentile_60d"] is None
    assert raw["bollinger_bandwidth_percentile_60d"] is None
    data_quality = payload["technical_profile"]["data_quality"]
    assert data_quality["temporal_data_date"] is None
    assert data_quality["temporal_completed_bars_only"] is False
    assert data_quality["temporal_missing_reason"] == "completed_bar_dates_unavailable"


def test_profile_intraday_average_volumes_exclude_current_dated_bar() -> None:
    closes, highs, lows, volumes = _series(length=61)
    volume_dates = ["2026-07-29"] * 60 + ["2026-07-30"]

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        volume_dates=volume_dates,
        data_date="2026-07-29",
        observation_date="2026-07-30",
        is_final=False,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["avg_volume_20"] == pytest.approx(sum(volumes[-21:-1]) / 20)
    assert raw["avg_volume_60"] == pytest.approx(sum(volumes[:-1]) / 60)
    assert raw["volume_ratio"] == pytest.approx(volumes[-1] / (sum(volumes[-20:]) / 20))


def test_profile_intraday_average_volumes_keep_latest_completed_bar() -> None:
    closes, highs, lows, volumes = _series(length=60)
    volume_dates = ["2026-07-29"] * 60

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        volume_dates=volume_dates,
        data_date="2026-07-30",
        is_final=False,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["avg_volume_20"] == pytest.approx(sum(volumes[-20:]) / 20)
    assert raw["avg_volume_60"] == pytest.approx(sum(volumes) / 60)


def test_profile_intraday_legacy_volume_series_reports_unknown_average() -> None:
    closes, highs, lows, volumes = _series(length=61)

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        data_date="2026-07-30",
        is_final=False,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["avg_volume_20"] is None
    assert raw["avg_volume_60"] is None
    assert raw["volume_ratio"] == pytest.approx(volumes[-1] / (sum(volumes[-20:]) / 20))


def test_intraday_average_volume_fields_do_not_change_volume_ratio_scoring() -> None:
    closes, highs, lows, volumes = _series(length=61)
    volume_dates = ["2026-07-29"] * 60 + ["2026-07-30"]

    with_average_volumes = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        volume_dates=volume_dates,
        data_date="2026-07-30",
        is_final=False,
    )
    legacy_without_dates = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        data_date="2026-07-30",
        is_final=False,
    )

    assert with_average_volumes is not None
    assert legacy_without_dates is not None
    assert with_average_volumes["technical_indicators"]["avg_volume_20"] is not None
    assert legacy_without_dates["technical_indicators"]["avg_volume_20"] is None
    assert (
        with_average_volumes["technical_indicators"]["volume_ratio"]
        == legacy_without_dates["technical_indicators"]["volume_ratio"]
    )
    assert (
        with_average_volumes["technical_profile"]["primary_score_inputs"]["volume_ratio"]
        == legacy_without_dates["technical_profile"]["primary_score_inputs"]["volume_ratio"]
    )
    assert (
        with_average_volumes["technical_profile"]["score_summary"]
        == legacy_without_dates["technical_profile"]["score_summary"]
    )


def test_profile_average_volumes_use_valid_volume_lookback_when_ohlcv_is_misaligned() -> None:
    closes, highs, lows, volumes = _series(length=100)
    valid_volumes = volumes[1:]

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=valid_volumes,
        is_final=True,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["avg_volume_20"] == pytest.approx(sum(valid_volumes[-20:]) / 20)
    assert raw["avg_volume_60"] == pytest.approx(sum(valid_volumes[-60:]) / 60)
    assert payload["technical_profile"]["data_quality"]["volume_aligned"] is False


def test_profile_from_snapshot_uses_taipei_date_for_intraday_volume_alignment() -> None:
    closes, highs, lows, volumes = _series(length=60)
    snapshot = {
        "current_price": closes[-1],
        "recent_closes": closes,
        "recent_highs": highs,
        "recent_lows": lows,
        "recent_volumes": volumes,
        "recent_volume_dates": ["2026-07-29"] * 60,
        "fetched_at": "2026-07-29T23:00:00+00:00",
    }

    payload = build_technical_profile_from_snapshot(snapshot, is_final=False)

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["avg_volume_20"] == pytest.approx(sum(volumes[-20:]) / 20)
    assert raw["avg_volume_60"] == pytest.approx(sum(volumes) / 60)
    assert payload["technical_profile"]["data_quality"]["data_date"] == "2026-07-30"


def test_profile_average_volume_reports_insufficient_60_day_lookback() -> None:
    closes, highs, lows, volumes = _series(length=40)

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["avg_volume_20"] == pytest.approx(sum(volumes[-20:]) / 20)
    assert raw["avg_volume_60"] is None
    assert "avg_volume_60" in payload["technical_profile"]["data_quality"]["missing_fields"]


def test_profile_average_volume_rejects_non_finite_aggregate() -> None:
    closes = [100.0] * 61

    payload = build_technical_profile_payload(
        closes=closes,
        volumes=[1e308] * 60,
        is_final=True,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    assert raw["avg_volume_20"] is None
    assert raw["avg_volume_60"] is None
    assert "avg_volume_20" in payload["technical_profile"]["data_quality"]["missing_fields"]
    assert "avg_volume_60" in payload["technical_profile"]["data_quality"]["missing_fields"]


def test_profile_score_summary_uses_bucket_caps_and_excludes_display_only() -> None:
    closes, highs, lows, volumes = _series()

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    profile = payload["technical_profile"]
    score_summary = profile["score_summary"]

    assert -3 <= score_summary["primary_score"] <= 3
    assert -3 <= score_summary["risk_filter_score"] <= 0
    assert -1 <= score_summary["secondary_score"] <= 1
    assert -5 <= score_summary["capped_total"] <= 5
    assert score_summary["technical_score"] == round(50 + score_summary["capped_total"] * (17 / 5))
    assert "obv_absolute_value" in profile["display_only"]
    assert "display_only" not in score_summary


def test_profile_keeps_atr_primary_risk_separate_from_atr_risk_filter() -> None:
    closes, highs, lows, volumes = _series()

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    profile = payload["technical_profile"]
    atr_primary = profile["primary_score_inputs"]["atr_risk"]
    atr_filter = profile["risk_overheat_filters"]["atr_state"]

    assert "support" in atr_primary["reason"].lower() or "atr" in atr_primary["reason"].lower()
    assert atr_filter["impact"] <= 0
    assert atr_filter["impact"] == 0 or atr_filter["state"] == "high"


def test_profile_data_quality_tracks_signal_specific_missing_lookback() -> None:
    closes, highs, lows, volumes = _series(length=80)

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    data_quality = payload["technical_profile"]["data_quality"]

    assert data_quality["lookback_days_available"] == 80
    assert data_quality["required_lookback_days"] == 60
    assert "lookback_60d" not in data_quality["missing_fields"]
    assert "obv_mid_long_trend" in data_quality["missing_fields"]


def test_profile_does_not_score_support_from_close_fallback_when_high_low_missing() -> None:
    closes, _, _, volumes = _series()

    payload = build_technical_profile_payload(
        closes=closes,
        volumes=volumes,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    profile = payload["technical_profile"]
    data_quality = profile["data_quality"]
    support = profile["primary_score_inputs"]["support_resistance"]
    atr_primary = profile["primary_score_inputs"]["atr_risk"]

    assert raw["high_20d"] == max(closes[-20:])
    assert raw["low_20d"] == min(closes[-20:])
    assert data_quality["ohlcv_aligned"] is False
    assert data_quality["price_level_basis"] == "close_fallback"
    assert {"highs", "lows"}.issubset(set(data_quality["missing_fields"]))
    assert support["state"] == "missing"
    assert support["impact"] == 0
    assert atr_primary["state"] == "missing"
    assert atr_primary["impact"] == 0


def test_profile_scores_support_breakdown_against_prior_completed_bars() -> None:
    closes = [110.0] * 24 + [95.0]
    highs = [115.0] * 24 + [100.0]
    lows = [100.0] * 24 + [94.0]
    volumes = [1000.0 + index for index in range(len(closes))]

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    support = payload["technical_profile"]["primary_score_inputs"]["support_resistance"]

    assert raw["low_20d"] == 94.0
    assert support["state"] == "breakdown"
    assert support["impact"] == -2


def test_profile_requires_prior_completed_bars_for_support_scoring() -> None:
    closes = [100.0] * 19 + [95.0]
    highs = [105.0] * len(closes)
    lows = [95.0] * len(closes)
    volumes = [1000.0 + index for index in range(len(closes))]

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    raw = payload["technical_indicators"]
    support = payload["technical_profile"]["primary_score_inputs"]["support_resistance"]

    assert raw["low_20d"] == 95.0
    assert support["state"] == "missing"
    assert support["impact"] == 0


def test_profile_from_snapshot_builds_without_analysis_schema_dependency() -> None:
    closes, highs, lows, volumes = _series()
    snapshot = {
        "current_price": closes[-1],
        "recent_closes": closes,
        "recent_highs": highs,
        "recent_lows": lows,
        "recent_volumes": volumes,
        "data_date": "2026-06-23",
    }

    payload = build_technical_profile_from_snapshot(snapshot, is_final=False)

    assert payload is not None
    profile = payload["technical_profile"]
    assert profile["data_quality"]["data_date"] == "2026-06-23"
    assert profile["data_quality"]["is_final"] is False
    assert profile["companion_context_refs"]["chip_stability_context"] == "tdcc_weekly_major_holders"
    for bucket_name in ("primary_score_inputs", "risk_overheat_filters", "secondary_evidence", "display_only"):
        assert "chip_stability_context" not in profile[bucket_name]


def test_profile_from_snapshot_uses_embedded_ohlcv_date_for_temporal_boundary() -> None:
    closes, highs, lows, volumes = _series(length=90)
    close_dates = ["2026-07-31"] * 90
    snapshot = {
        "current_price": closes[-1],
        "recent_closes": closes,
        "recent_highs": highs,
        "recent_lows": lows,
        "recent_volumes": volumes,
        "recent_volume_dates": ["2026-07-31"] * 90,
        "recent_close_dates": close_dates,
        "data_dates": {"ohlcv": "2026-07-31"},
        "fetched_at": "2026-08-01T01:00:00+08:00",
    }

    payload = build_technical_profile_from_snapshot(snapshot, is_final=False)

    assert payload is not None
    data_quality = payload["technical_profile"]["data_quality"]
    assert data_quality["data_date"] == "2026-07-31"
    assert data_quality["temporal_data_date"] == "2026-07-31"
    assert payload["technical_indicators"]["avg_volume_60"] == pytest.approx(sum(volumes[-60:]) / 60)
    assert payload["technical_indicators"]["ma20_slope_pct_5d"] is not None


def test_profile_from_snapshot_ignores_zero_current_price_sentinel() -> None:
    closes, highs, lows, volumes = _series()
    snapshot = {
        "current_price": 0.0,
        "recent_closes": closes,
        "recent_highs": highs,
        "recent_lows": lows,
        "recent_volumes": volumes,
    }

    payload = build_technical_profile_from_snapshot(snapshot)
    fallback_payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    assert fallback_payload is not None
    assert payload["technical_indicators"]["bias20"] == fallback_payload["technical_indicators"]["bias20"]
    assert payload["technical_indicators"]["bollinger_position"] == fallback_payload["technical_indicators"]["bollinger_position"]
    assert payload["technical_profile"]["score_summary"] == fallback_payload["technical_profile"]["score_summary"]
    assert payload["technical_profile"]["primary_score_inputs"]["support_resistance"] == fallback_payload["technical_profile"]["primary_score_inputs"]["support_resistance"]


@pytest.mark.parametrize("current_price", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_profile_payload_falls_back_to_latest_close_for_invalid_current_price(current_price: float) -> None:
    closes, highs, lows, volumes = _series()

    payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        current_price=current_price,
    )
    fallback_payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

    assert payload is not None
    assert fallback_payload is not None
    assert payload["technical_indicators"]["bias20"] == fallback_payload["technical_indicators"]["bias20"]
    assert payload["technical_profile"]["primary_score_inputs"]["ma_structure"] == fallback_payload["technical_profile"]["primary_score_inputs"]["ma_structure"]
