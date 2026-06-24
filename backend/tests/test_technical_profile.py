from __future__ import annotations

import pytest

from ai_stock_sentinel.analysis import metrics as compatibility_metrics
from ai_stock_sentinel.technical import metrics as canonical_metrics
from ai_stock_sentinel.technical.profile import (
    TECHNICAL_LAYER_VERSION,
    TECHNICAL_METRICS_VERSION,
    build_technical_profile_from_snapshot,
    build_technical_profile_payload,
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
    assert raw["macd_hist"] is not None
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
