"""Canonical technical indicator profile builder.

This module is intentionally pure domain logic. It returns plain dictionaries
so feature modules can adapt the contract without creating dependency cycles.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ai_stock_sentinel.technical.metrics import (
    adx,
    atr,
    bollinger_bands,
    calc_bias,
    calc_rsi,
    donchian_channel,
    ma,
    macd,
    mfi,
    obv,
    stochastic_kd,
)

TECHNICAL_METRICS_VERSION = "technical-metrics-v2"
TECHNICAL_LAYER_VERSION = "technical-layer-v2"
REQUIRED_LOOKBACK_DAYS = 60
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def build_technical_profile_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    data_date: str | None = None,
    is_final: bool = True,
) -> dict[str, Any] | None:
    """Build raw indicators and profile semantics from an Analyze-style snapshot."""
    closes = _numbers(snapshot.get("recent_closes"))
    if not closes:
        return None

    snapshot_data_date = data_date or _snapshot_ohlcv_date(snapshot)
    snapshot_observation_date = _date_string_or_none(snapshot.get("fetched_at"))
    current_price = _number_or_none(snapshot.get("current_price"))
    return build_technical_profile_payload(
        closes=closes,
        highs=_numbers(snapshot.get("recent_highs")),
        lows=_numbers(snapshot.get("recent_lows")),
        volumes=_numbers(snapshot.get("recent_volumes")),
        close_dates=_strings(snapshot.get("recent_close_dates")),
        volume_dates=_strings(snapshot.get("recent_volume_dates")),
        current_price=current_price,
        data_date=snapshot_data_date,
        observation_date=snapshot_observation_date,
        is_final=is_final,
    )


def build_technical_profile_payload(
    *,
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    volumes: Sequence[float] | None = None,
    close_dates: Sequence[str] | None = None,
    volume_dates: Sequence[str] | None = None,
    current_price: float | None = None,
    data_date: str | None = None,
    observation_date: str | None = None,
    is_final: bool = True,
) -> dict[str, Any] | None:
    """Return backward-compatible raw indicators plus the v2 layered profile."""
    close_values = [float(value) for value in closes if value is not None]
    if not close_values:
        return None

    high_values = _aligned_values(highs, close_values)
    low_values = _aligned_values(lows, close_values)
    volume_values = _aligned_values(volumes, close_values)
    average_volume_values = _valid_volume_values(volumes)
    aligned_hilo = high_values is not None and low_values is not None
    aligned_volume = volume_values is not None

    high_source = high_values if aligned_hilo else close_values
    low_source = low_values if aligned_hilo else close_values
    close = _positive_finite_number_or_none(current_price) or close_values[-1]

    bb = bollinger_bands(close_values)
    macd_data = macd(close_values)
    kd_data = stochastic_kd(close_values, high_values, low_values) if aligned_hilo else None
    adx_data = adx(close_values, high_values, low_values) if aligned_hilo else None
    atr_data = atr(close_values, high_values, low_values) if aligned_hilo else None
    mfi_data = mfi(close_values, high_values, low_values, volume_values) if aligned_hilo and aligned_volume else None
    donchian_data = donchian_channel(close_values, high_values, low_values) if aligned_hilo else None
    obv_data = obv(close_values, volume_values) if aligned_volume else None
    ma5 = ma(close_values, 5)
    ma20 = ma(close_values, 20)
    ma60 = ma(close_values, 60)
    high_20d = max(high_source[-20:]) if len(high_source) >= 20 else None
    low_20d = min(low_source[-20:]) if len(low_source) >= 20 else None
    high_60d = max(high_source[-60:]) if len(high_source) >= 60 else None
    low_60d = min(low_source[-60:]) if len(low_source) >= 60 else None
    completed_volume_values = _completed_volume_values(
        average_volume_values,
        volume_dates=volume_dates,
        data_date=data_date,
        observation_date=observation_date,
        is_final=is_final,
    )
    avg_volume_20 = _average_volume(completed_volume_values, 20)
    avg_volume_60 = _average_volume(completed_volume_values, 60)
    primary_high_20d = _prior_window_max(high_values, 20) if aligned_hilo else None
    primary_low_20d = _prior_window_min(low_values, 20) if aligned_hilo else None
    volume_ratio = _volume_ratio(volume_values)
    bias20 = calc_bias(close, ma20) if ma20 is not None else None
    rsi14 = calc_rsi(close_values, period=14)
    temporal_inputs = _completed_temporal_inputs(
        closes=close_values,
        highs=high_values,
        lows=low_values,
        close_dates=close_dates,
        data_date=data_date,
        observation_date=observation_date,
        is_final=is_final,
    )
    temporal_metrics = (
        _temporal_metrics(
            closes=temporal_inputs[0],
            highs=temporal_inputs[1],
            lows=temporal_inputs[2],
        )
        if temporal_inputs
        else _empty_temporal_metrics()
    )

    raw_indicators = {
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": rsi14,
        "bias20": bias20,
        "volume_ratio": volume_ratio,
        "avg_volume_20": avg_volume_20,
        "avg_volume_60": avg_volume_60,
        "high_20d": high_20d,
        "low_20d": low_20d,
        "high_60d": high_60d,
        "low_60d": low_60d,
        "bollinger_upper": bb["bollinger_upper"] if bb else None,
        "bollinger_mid": bb["bollinger_mid"] if bb else None,
        "bollinger_lower": bb["bollinger_lower"] if bb else None,
        "bollinger_bandwidth": bb.get("bollinger_bandwidth") if bb else None,
        "bollinger_position": _bollinger_position(bb, close),
        "macd_line": macd_data["macd_line"] if macd_data else None,
        "macd_signal": macd_data["macd_signal"] if macd_data else None,
        "macd_hist": macd_data["macd_hist"] if macd_data else None,
        "macd_bias": macd_data["macd_bias"] if macd_data else None,
        "kd_k": kd_data["k"] if kd_data else None,
        "kd_d": kd_data["d"] if kd_data else None,
        "kd_signal": kd_data["kd_signal"] if kd_data else None,
        "kd_zone": kd_data["kd_zone"] if kd_data else None,
        "adx": adx_data["adx"] if adx_data else None,
        "adx_trend_strength": adx_data["trend_strength"] if adx_data else None,
        "adx_trend_direction": adx_data["trend_direction"] if adx_data else None,
        "obv": obv_data["obv"] if obv_data else None,
        "obv_signal": obv_data["obv_signal"] if obv_data else None,
        "obv_trend_20d": obv_data["obv_trend_20d"] if obv_data else None,
        "obv_trend_mid_long": obv_data["obv_trend_mid_long"] if obv_data else None,
        "obv_trend_mid_long_window": obv_data["obv_trend_mid_long_window"] if obv_data else None,
        "atr": atr_data["atr"] if atr_data else None,
        "atr_pct": atr_data["atr_pct"] if atr_data else None,
        "volatility_level": atr_data["volatility_level"] if atr_data else None,
        "mfi": mfi_data["mfi"] if mfi_data else None,
        "mfi_signal": mfi_data["mfi_signal"] if mfi_data else None,
        "donchian_upper": donchian_data["donchian_upper"] if donchian_data else None,
        "donchian_lower": donchian_data["donchian_lower"] if donchian_data else None,
        "donchian_mid": donchian_data["donchian_mid"] if donchian_data else None,
        "donchian_width_pct": donchian_data["donchian_width_pct"] if donchian_data else None,
        "donchian_position": donchian_data["donchian_position"] if donchian_data else None,
        **temporal_metrics,
    }

    missing_fields = _missing_fields(
        lookback_days_available=len(close_values),
        aligned_hilo=aligned_hilo,
        aligned_volume=aligned_volume,
        indicators=raw_indicators,
    )
    primary = {
        "ma_structure": _ma_structure(close=close, ma5=ma5, ma20=ma20, ma60=ma60),
        "support_resistance": _support_resistance(
            close=close,
            low_20d=primary_low_20d,
            high_20d=primary_high_20d,
        ),
        "volume_ratio": _volume_ratio_signal(volume_ratio),
        "atr_risk": _atr_primary_risk(close=close, support=primary_low_20d, atr_data=atr_data),
        "macd_momentum": _macd_momentum(macd_data),
        "obv_trend": _obv_trend_signal(obv_data),
    }
    risk = {
        "rsi_state": _rsi_state(rsi14),
        "bias_state": _bias_state(bias20),
        "bollinger_state": _bollinger_state(raw_indicators["bollinger_position"], rsi14),
        "atr_state": _atr_state(atr_data),
    }
    secondary = {
        "adx": _adx_evidence(adx_data),
        "donchian": _donchian_evidence(donchian_data),
        "mfi": _mfi_evidence(mfi_data),
        "kd": _kd_evidence(kd_data),
    }
    score_summary = _score_summary(primary=primary, risk=risk, secondary=secondary)
    temporal_evidence = _temporal_evidence(temporal_metrics)
    signal_conflicts = _signal_conflicts(
        primary=primary,
        risk=risk,
        temporal=temporal_evidence,
    )
    raw_indicators["technical_conflicts"] = [item["message"] for item in signal_conflicts]
    caveats = _profile_caveats(missing_fields=missing_fields, aligned_hilo=aligned_hilo, aligned_volume=aligned_volume)

    return {
        "technical_indicators": raw_indicators,
        "technical_profile": {
            "version": TECHNICAL_LAYER_VERSION,
            "primary_score_inputs": primary,
            "risk_overheat_filters": risk,
            "secondary_evidence": secondary,
            "temporal_evidence": temporal_evidence,
            "signal_conflicts": signal_conflicts,
            "display_only": {
                "obv_absolute_value": raw_indicators["obv"],
                "avg_volume_20": raw_indicators["avg_volume_20"],
                "avg_volume_60": raw_indicators["avg_volume_60"],
                "donchian_upper": raw_indicators["donchian_upper"],
                "donchian_lower": raw_indicators["donchian_lower"],
                "donchian_mid": raw_indicators["donchian_mid"],
                "mfi": raw_indicators["mfi"],
                "kd_k": raw_indicators["kd_k"],
                "kd_d": raw_indicators["kd_d"],
            },
            "score_summary": score_summary,
            "data_quality": {
                "data_date": data_date,
                "is_final": is_final,
                "lookback_days_available": len(close_values),
                "required_lookback_days": REQUIRED_LOOKBACK_DAYS,
                "ohlcv_aligned": aligned_hilo,
                "volume_aligned": aligned_volume,
                "price_level_basis": "ohlc_high_low" if aligned_hilo else "close_fallback",
                "temporal_data_date": temporal_inputs[3] if temporal_inputs else None,
                "temporal_completed_bars_only": temporal_inputs is not None,
                "temporal_missing_reason": (
                    None if temporal_inputs else "completed_bar_dates_unavailable"
                ),
                "missing_fields": missing_fields,
            },
            "formula_versions": {
                "metrics": TECHNICAL_METRICS_VERSION,
                "layering": TECHNICAL_LAYER_VERSION,
            },
            "companion_context_refs": {
                "chip_stability_context": "tdcc_weekly_major_holders",
            },
            "caveats": caveats,
        },
    }


def _completed_temporal_inputs(
    *,
    closes: Sequence[float],
    highs: Sequence[float] | None,
    lows: Sequence[float] | None,
    close_dates: Sequence[str] | None,
    data_date: str | None,
    observation_date: str | None,
    is_final: bool,
) -> tuple[list[float], list[float] | None, list[float] | None, str | None] | None:
    """Return bars safe for temporal comparisons without using an unfinished bar."""
    end = len(closes)
    dates = list(close_dates or [])
    dates_aligned = len(dates) == len(closes)
    if not is_final:
        if not dates_aligned or observation_date is None:
            return None
        normalized_observation_date = _iso_date_or_none(observation_date)
        normalized_data_date = _iso_date_or_none(data_date)
        latest_bar_date = _iso_date_or_none(dates[-1]) if dates else None
        if normalized_observation_date is None or latest_bar_date is None:
            return None
        if normalized_data_date is not None and normalized_data_date > normalized_observation_date:
            return None
        if latest_bar_date > normalized_observation_date:
            return None
        if latest_bar_date == normalized_observation_date:
            end -= 1
    if end <= 0:
        return None
    completed_highs = list(highs[:end]) if highs is not None else None
    completed_lows = list(lows[:end]) if lows is not None else None
    temporal_date = _iso_date_or_none(dates[end - 1]) if dates_aligned else _iso_date_or_none(data_date)
    if not is_final and temporal_date is None:
        return None
    return list(closes[:end]), completed_highs, completed_lows, temporal_date


def _temporal_metrics(
    closes: Sequence[float],
    highs: Sequence[float] | None,
    lows: Sequence[float] | None,
) -> dict[str, Any]:
    close_values = list(closes)
    high_values = list(highs) if highs is not None else None
    low_values = list(lows) if lows is not None else None
    ma20_slope = _moving_average_slope_pct(close_values, window=20, lookback=5)
    ma60_slope = _moving_average_slope_pct(close_values, window=60, lookback=10)
    macd_slope = _macd_hist_slope_pct(close_values, lookback=3)
    macd_current = macd(close_values)
    macd_hist = _number_or_none(macd_current.get("macd_hist")) if macd_current else None
    atr_percentile = _atr_pct_percentile(close_values, high_values, low_values)
    bandwidth_percentile = _bollinger_bandwidth_percentile(close_values)
    return {
        "ma20_slope_pct_5d": ma20_slope,
        "ma60_slope_pct_10d": ma60_slope,
        "macd_hist_slope_pct_3d": macd_slope,
        "macd_hist_trend": _macd_hist_trend(macd_hist, macd_slope),
        "atr_pct_percentile_60d": atr_percentile,
        "bollinger_bandwidth_percentile_60d": bandwidth_percentile,
        "volatility_regime": _volatility_regime(atr_percentile, bandwidth_percentile),
    }


def _empty_temporal_metrics() -> dict[str, Any]:
    return {
        "ma20_slope_pct_5d": None,
        "ma60_slope_pct_10d": None,
        "macd_hist_slope_pct_3d": None,
        "macd_hist_trend": "missing",
        "atr_pct_percentile_60d": None,
        "bollinger_bandwidth_percentile_60d": None,
        "volatility_regime": "missing",
    }


def _moving_average_slope_pct(values: Sequence[float], *, window: int, lookback: int) -> float | None:
    if len(values) < window + lookback:
        return None
    current = ma(list(values), window)
    previous = ma(list(values[:-lookback]), window)
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 3)


def _macd_hist_slope_pct(values: Sequence[float], *, lookback: int) -> float | None:
    if len(values) < 35 + lookback or not values or values[-1] == 0:
        return None
    current = macd(list(values))
    previous = macd(list(values[:-lookback]))
    if current is None or previous is None:
        return None
    current_hist = _number_or_none(current.get("macd_hist"))
    previous_hist = _number_or_none(previous.get("macd_hist"))
    if current_hist is None or previous_hist is None:
        return None
    return round((current_hist - previous_hist) / abs(values[-1]) * 100, 4)


def _atr_pct_percentile(
    closes: Sequence[float],
    highs: Sequence[float] | None,
    lows: Sequence[float] | None,
) -> float | None:
    if highs is None or lows is None or len(highs) != len(closes) or len(lows) != len(closes):
        return None
    observations: list[float] = []
    start = max(15, len(closes) - 59)
    for end in range(start, len(closes) + 1):
        result = atr(list(closes[:end]), list(highs[:end]), list(lows[:end]))
        value = _finite_number_or_none(result.get("atr_pct")) if result else None
        if value is not None:
            observations.append(value)
    return _percentile_rank(observations)


def _bollinger_bandwidth_percentile(closes: Sequence[float]) -> float | None:
    observations: list[float] = []
    start = max(20, len(closes) - 59)
    for end in range(start, len(closes) + 1):
        result = bollinger_bands(list(closes[:end]))
        value = _finite_number_or_none(result.get("bollinger_bandwidth")) if result else None
        if value is not None:
            observations.append(value)
    return _percentile_rank(observations)


def _percentile_rank(values: Sequence[float], *, minimum_observations: int = 20) -> float | None:
    if len(values) < minimum_observations:
        return None
    current = values[-1]
    return round(sum(value <= current for value in values) / len(values) * 100, 1)


def _macd_hist_trend(histogram: float | None, slope: float | None) -> str:
    if histogram is None or slope is None:
        return "missing"
    epsilon = 1e-8
    if histogram > epsilon and slope > epsilon:
        return "accelerating_bullish"
    if histogram > epsilon and slope < -epsilon:
        return "bullish_fading"
    if histogram < -epsilon and slope < -epsilon:
        return "accelerating_bearish"
    if histogram < -epsilon and slope > epsilon:
        return "bearish_recovering"
    return "flat"


def _volatility_regime(atr_percentile: float | None, bandwidth_percentile: float | None) -> str:
    values = [value for value in (atr_percentile, bandwidth_percentile) if value is not None]
    if not values:
        return "missing"
    if len(values) == 2 and min(values) <= 20 and max(values) >= 80:
        return "mixed_transition"
    if max(values) >= 80:
        return "expansion"
    if all(value <= 20 for value in values):
        return "compression"
    return "normal"


def _temporal_evidence(metrics: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "ma20_slope": _slope_evidence(metrics.get("ma20_slope_pct_5d"), label="MA20", period="5-day"),
        "ma60_slope": _slope_evidence(metrics.get("ma60_slope_pct_10d"), label="MA60", period="10-day"),
        "macd_hist_trend": _signal(
            str(metrics.get("macd_hist_trend") or "missing"),
            0,
            "MACD histogram direction and 3-day normalized slope; evidence only.",
            value=metrics.get("macd_hist_slope_pct_3d"),
        ),
        "volatility_regime": _signal(
            str(metrics.get("volatility_regime") or "missing"),
            0,
            "ATR% and Bollinger bandwidth percentile regime; evidence only.",
            atr_percentile=metrics.get("atr_pct_percentile_60d"),
            bandwidth_percentile=metrics.get("bollinger_bandwidth_percentile_60d"),
        ),
    }


def _slope_evidence(value: Any, *, label: str, period: str) -> dict[str, Any]:
    slope = _finite_number_or_none(value)
    if slope is None:
        return _signal("missing", 0, f"{label} {period} slope unavailable.")
    if slope > 0.05:
        state = "rising"
    elif slope < -0.05:
        state = "falling"
    else:
        state = "flat"
    return _signal(state, 0, f"{label} {period} slope is display-only evidence.", value=slope)


def _signal_conflicts(
    *,
    primary: Mapping[str, Mapping[str, Any]],
    risk: Mapping[str, Mapping[str, Any]],
    temporal: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    ma_state = str(temporal.get("ma20_slope", {}).get("state") or "missing")
    macd_state = str(temporal.get("macd_hist_trend", {}).get("state") or "missing")
    if ma_state == "rising" and macd_state == "bullish_fading":
        conflicts.append(_conflict("trend_momentum_fading", "MA20 仍上升，但 MACD 多方柱體正在收斂。"))
    elif ma_state == "falling" and macd_state == "bearish_recovering":
        conflicts.append(_conflict("countertrend_recovery", "MA20 仍下降，但 MACD 空方柱體正在收斂。"))

    primary_impact = sum(_impact(value) for value in primary.values())
    risk_impact = sum(_impact(value) for value in risk.values())
    if primary_impact > 0 and risk_impact < 0:
        conflicts.append(_conflict("trend_overheat", "主要趨勢偏多，但過熱或波動濾網正在扣分。"))

    ma_structure = str(primary.get("ma_structure", {}).get("state") or "")
    volume_state = str(primary.get("volume_ratio", {}).get("state") or "")
    obv_state = str(primary.get("obv_trend", {}).get("state") or "")
    if ma_structure in {"bullish_alignment", "above_ma20"} and (
        volume_state == "thin_participation" or obv_state == "weakening"
    ):
        conflicts.append(_conflict("trend_without_participation", "價格結構偏多，但成交量或 OBV 尚未確認。"))
    return conflicts


def _conflict(code: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": "caution", "message": message}


def _score_summary(
    *,
    primary: Mapping[str, Mapping[str, Any]],
    risk: Mapping[str, Mapping[str, Any]],
    secondary: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    primary_score = _clamp(sum(_impact(value) for value in primary.values()), -3, 3)
    risk_filter_score = _clamp(sum(_impact(value) for value in risk.values()), -3, 0)
    secondary_score = _clamp(sum(_impact(value) for value in secondary.values()), -1, 1)
    capped_total = _clamp(primary_score + risk_filter_score + secondary_score, -5, 5)
    return {
        "primary_score": primary_score,
        "risk_filter_score": risk_filter_score,
        "secondary_score": secondary_score,
        "capped_total": capped_total,
        "technical_score": round(50 + capped_total * (17 / 5)),
    }


def _ma_structure(
    *,
    close: float,
    ma5: float | None,
    ma20: float | None,
    ma60: float | None,
) -> dict[str, Any]:
    if ma5 is None or ma20 is None:
        return _signal("missing", 0, "MA5/MA20 unavailable.")
    if close > ma5 > ma20 and (ma60 is None or ma20 > ma60):
        return _signal("bullish_alignment", 2, "close > MA5 > MA20.")
    if close < ma5 < ma20:
        return _signal("bearish_alignment", -2, "close < MA5 < MA20.")
    if close > ma20:
        return _signal("above_ma20", 1, "close is above MA20.")
    if close < ma20:
        return _signal("below_ma20", -1, "close is below MA20.")
    return _signal("neutral", 0, "close is near MA20.")


def _support_resistance(
    *,
    close: float,
    low_20d: float | None,
    high_20d: float | None,
) -> dict[str, Any]:
    if low_20d is None or high_20d is None:
        return _signal("missing", 0, "20-day support/resistance unavailable.")
    if close < low_20d:
        return _signal("breakdown", -2, "close is below 20-day support.")
    if close <= low_20d * 1.02:
        return _signal("near_support", 1, "close is within 2% of 20-day support.")
    if close >= high_20d * 0.995:
        return _signal("near_resistance", 0, "close is near 20-day resistance.")
    return _signal("range_mid", 0, "close is within the 20-day range.")


def _volume_ratio_signal(value: float | None) -> dict[str, Any]:
    if value is None:
        return _signal("missing", 0, "20-day volume ratio unavailable.")
    if value >= 1.5:
        return _signal("expanded_participation", 2, "volume is at least 1.5x the 20-day average.", value=value)
    if value >= 1.15:
        return _signal("constructive_participation", 1, "volume is above the 20-day average.", value=value)
    if value < 0.7:
        return _signal("thin_participation", -1, "volume is materially below the 20-day average.", value=value)
    return _signal("normal", 0, "volume is near the 20-day average.", value=value)


def _atr_primary_risk(
    *,
    close: float,
    support: float | None,
    atr_data: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if support is None or atr_data is None or atr_data.get("atr") is None:
        return _signal("missing", 0, "support distance or ATR unavailable.")
    atr_value = float(atr_data["atr"])
    support_distance = max(0.0, close - support)
    if atr_value <= 0:
        return _signal("unknown", 0, "ATR is zero or unavailable.")
    distance_to_atr = support_distance / atr_value
    if distance_to_atr <= 2:
        return _signal("contained", 1, "support distance is within 2 ATR.", value=round(distance_to_atr, 2))
    if distance_to_atr >= 4:
        return _signal("wide_stop_distance", -1, "support distance is wider than 4 ATR.", value=round(distance_to_atr, 2))
    return _signal("moderate", 0, "support distance is moderate relative to ATR.", value=round(distance_to_atr, 2))


def _macd_momentum(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return _signal("missing", 0, "MACD unavailable.")
    hist = _number_or_none(data.get("macd_hist"))
    bias = data.get("macd_bias")
    if hist is not None and hist > 0 and bias == "bullish":
        return _signal("positive_histogram", 1, "MACD histogram is positive.")
    if hist is not None and hist < 0 and bias == "bearish":
        return _signal("negative_histogram", -1, "MACD histogram is negative.")
    return _signal("neutral", 0, "MACD momentum is neutral.")


def _obv_trend_signal(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return _signal("missing", 0, "OBV unavailable.")
    signal = str(data.get("obv_signal") or "")
    trend_20d = data.get("obv_trend_20d")
    if signal in {"price_volume_confirm", "bullish_divergence"} or trend_20d == "rising":
        return _signal("constructive", 1, "OBV confirms participation.")
    if signal in {"bearish_divergence", "price_volume_weak"} or trend_20d == "falling":
        return _signal("weakening", -1, "OBV participation is weakening.")
    return _signal("neutral", 0, "OBV is neutral.")


def _rsi_state(value: float | None) -> dict[str, Any]:
    if value is None:
        return _signal("missing", 0, "RSI unavailable.")
    if value >= 80:
        return _signal("extreme_overheated", -2, "RSI is extremely overheated.", value=value)
    if value >= 70:
        return _signal("overheated", -1, "RSI is overheated.", value=value)
    return _signal("not_overheated", 0, "RSI does not add positive score.", value=value)


def _bias_state(value: float | None) -> dict[str, Any]:
    if value is None:
        return _signal("missing", 0, "BIAS unavailable.")
    if abs(value) >= 15:
        return _signal("extreme_extension", -2, "BIAS is extremely extended.", value=value)
    if abs(value) >= 10:
        return _signal("extended", -1, "BIAS is extended.", value=value)
    return _signal("not_extended", 0, "BIAS does not add positive score.", value=value)


def _bollinger_state(position: Any, rsi_value: float | None) -> dict[str, Any]:
    if not position:
        return _signal("missing", 0, "Bollinger position unavailable.")
    if position == "near_upper" and rsi_value is not None and rsi_value >= 70:
        return _signal("upper_overheated", -2, "price is near upper band with overheated RSI.")
    if position == "near_upper":
        return _signal("near_upper", -1, "price is near upper Bollinger band.")
    return _signal(str(position), 0, "Bollinger state does not add positive score.")


def _atr_state(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return _signal("missing", 0, "ATR unavailable.")
    level = data.get("volatility_level")
    if level == "high":
        return _signal("high", -2, "ATR volatility is high.", value=data.get("atr_pct"))
    return _signal(str(level or "unknown"), 0, "ATR volatility does not add positive score.", value=data.get("atr_pct"))


def _adx_evidence(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return _signal("missing", 0, "ADX unavailable.")
    if data.get("trend_strength") == "strong" and data.get("trend_direction") == "bullish":
        return _signal("strong_bullish_trend", 1, "ADX confirms bullish trend.")
    if data.get("trend_strength") == "strong" and data.get("trend_direction") == "bearish":
        return _signal("strong_bearish_trend", -1, "ADX confirms bearish trend.")
    return _signal(str(data.get("trend_strength") or "neutral"), 0, "ADX is secondary evidence only.")


def _donchian_evidence(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return _signal("missing", 0, "Donchian unavailable.")
    position = data.get("donchian_position")
    if position == "breakout_up":
        return _signal("breakout_up", 1, "Donchian breakout is secondary confirmation.")
    if position == "breakdown_down":
        return _signal("breakdown_down", -1, "Donchian breakdown is secondary caveat.")
    return _signal(str(position or "neutral"), 0, "Donchian is secondary evidence only.")


def _mfi_evidence(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return _signal("missing", 0, "MFI unavailable.")
    signal = data.get("mfi_signal")
    if signal == "bullish_flow":
        return _signal("bullish_flow", 1, "MFI supports participation.")
    if signal in {"overbought", "bearish_flow"}:
        return _signal(str(signal), -1, "MFI adds a secondary caveat.")
    return _signal(str(signal or "neutral"), 0, "MFI is secondary evidence only.")


def _kd_evidence(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return _signal("missing", 0, "KD unavailable.")
    kd_signal = data.get("kd_signal")
    kd_zone = data.get("kd_zone")
    if kd_signal == "bullish_cross" and kd_zone == "oversold":
        return _signal("low_bullish_cross", 1, "KD low-zone bullish cross is secondary confirmation.")
    if kd_signal == "bearish_cross" and kd_zone == "overbought":
        return _signal("high_bearish_cross", -1, "KD high-zone bearish cross is secondary caveat.")
    return _signal(str(kd_signal or "neutral"), 0, "KD is secondary evidence only.")


def _profile_caveats(
    *,
    missing_fields: Sequence[str],
    aligned_hilo: bool,
    aligned_volume: bool,
) -> list[str]:
    caveats = [
        "RSI/BIAS/Bollinger are treated as risk filters, not independent bullish evidence.",
        "KD/MFI/Donchian are secondary evidence only.",
        "Temporal slopes, volatility percentiles, and conflict flags are evidence only and do not change scoring.",
        "TDCC thousand-lot holder changes are chip-stability companion signals, not technical score inputs.",
    ]
    if missing_fields:
        caveats.append("Some technical fields are unavailable due to insufficient lookback or misaligned inputs.")
    if not aligned_hilo:
        caveats.append("High/low series are missing or not aligned with close prices.")
    if not aligned_volume:
        caveats.append("Volume series is missing or not aligned with close prices.")
    return caveats


def _missing_fields(
    *,
    lookback_days_available: int,
    aligned_hilo: bool,
    aligned_volume: bool,
    indicators: Mapping[str, Any],
) -> list[str]:
    missing: list[str] = []
    if lookback_days_available < REQUIRED_LOOKBACK_DAYS:
        missing.append("lookback_60d")
    if not aligned_hilo:
        missing.extend(["highs", "lows"])
    if not aligned_volume:
        missing.append("volumes")
    for field in (
        "ma20",
        "macd_hist",
        "atr",
        "mfi",
        "kd_k",
        "donchian_upper",
        "obv_trend_20d",
        "avg_volume_20",
        "avg_volume_60",
        "ma20_slope_pct_5d",
        "ma60_slope_pct_10d",
        "macd_hist_slope_pct_3d",
        "atr_pct_percentile_60d",
        "bollinger_bandwidth_percentile_60d",
    ):
        if indicators.get(field) is None:
            missing.append(field)
    if indicators.get("obv_trend_mid_long") is None:
        missing.append("obv_mid_long_trend")
    return sorted(set(missing))


def _bollinger_position(bb: Mapping[str, Any] | None, close: float | None) -> str | None:
    if not bb or close is None:
        return None
    upper = bb.get("bollinger_upper")
    lower = bb.get("bollinger_lower")
    if upper is None or lower is None:
        return None
    band_range = upper - lower
    if band_range <= 0:
        return "flat"
    if close >= upper * 0.99:
        return "near_upper"
    if close <= lower * 1.01:
        return "near_lower"
    if close >= (lower + band_range * 0.5):
        return "above_mid"
    return "below_mid"


def _volume_ratio(volumes: Sequence[float] | None) -> float | None:
    if volumes is None or len(volumes) < 20:
        return None
    avg_volume_20 = sum(volumes[-20:]) / 20
    if avg_volume_20 == 0:
        return None
    return volumes[-1] / avg_volume_20


def _completed_volume_values(
    volumes: Sequence[float] | None,
    *,
    volume_dates: Sequence[str] | None,
    data_date: str | None,
    observation_date: str | None,
    is_final: bool,
) -> list[float] | None:
    if volumes is None:
        return None
    values = [float(value) for value in volumes]
    if is_final or not values:
        return values

    dates_aligned = volume_dates is not None and len(volume_dates) == len(values)
    if not dates_aligned:
        return None
    if observation_date is not None:
        normalized_observation_date = _iso_date_or_none(observation_date)
        normalized_data_date = _iso_date_or_none(data_date)
        latest_bar_date = _iso_date_or_none(volume_dates[-1])
        if normalized_observation_date is None or latest_bar_date is None:
            return None
        if normalized_data_date is not None and normalized_data_date > normalized_observation_date:
            return None
        if latest_bar_date > normalized_observation_date:
            return None
        if latest_bar_date == normalized_observation_date:
            return values[:-1]
        return values
    if data_date is not None and volume_dates[-1] == data_date:
        return values[:-1]
    return values


def _average_volume(volumes: Sequence[float] | None, window: int) -> float | None:
    if volumes is None or len(volumes) < window:
        return None
    average = sum(volumes[-window:]) / window
    return average if math.isfinite(average) else None


def _prior_window_max(values: Sequence[float] | None, window: int) -> float | None:
    if values is None or len(values) < window + 1:
        return None
    return max(values[-(window + 1):-1])


def _prior_window_min(values: Sequence[float] | None, window: int) -> float | None:
    if values is None or len(values) < window + 1:
        return None
    return min(values[-(window + 1):-1])


def _signal(state: str, impact: int, reason: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": state,
        "impact": impact,
        "reason": reason,
    }
    payload.update(extra)
    return payload


def _impact(value: Mapping[str, Any]) -> int:
    try:
        return int(value.get("impact", 0))
    except (TypeError, ValueError):
        return 0


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _numbers(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    numbers: list[float] = []
    for item in value:
        number = _number_or_none(item)
        if number is not None:
            numbers.append(number)
    return numbers


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _string_or_none(item)) is not None]


def _aligned_values(values: Sequence[float] | None, closes: Sequence[float]) -> list[float] | None:
    if values is None:
        return None
    numbers = [float(value) for value in values if value is not None]
    return numbers if len(numbers) == len(closes) else None


def _valid_volume_values(values: Sequence[float] | None) -> list[float] | None:
    if values is None:
        return None
    volumes: list[float] = []
    for value in values:
        number = _number_or_none(value)
        if number is not None and math.isfinite(number) and number >= 0:
            volumes.append(number)
    return volumes


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finite_number_or_none(value: Any) -> float | None:
    number = _number_or_none(value)
    return number if number is not None and math.isfinite(number) else None


def _positive_finite_number_or_none(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None or not math.isfinite(number) or number <= 0:
        return None
    return number


def _snapshot_ohlcv_date(snapshot: Mapping[str, Any]) -> str | None:
    explicit = _string_or_none(snapshot.get("data_date"))
    if explicit:
        return explicit
    data_dates = snapshot.get("data_dates")
    if isinstance(data_dates, Mapping):
        ohlcv_date = _string_or_none(data_dates.get("ohlcv"))
        if ohlcv_date:
            return ohlcv_date
    return _date_string_or_none(snapshot.get("fetched_at"))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_string_or_none(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        return parsed.astimezone(TAIPEI_TZ).date().isoformat()
    return text[:10] if len(text) >= 10 else text


def _iso_date_or_none(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    candidate = text[:10]
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        return None
