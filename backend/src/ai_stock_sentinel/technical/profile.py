"""Canonical technical indicator profile builder.

This module is intentionally pure domain logic. It returns plain dictionaries
so feature modules can adapt the contract without creating dependency cycles.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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

TECHNICAL_METRICS_VERSION = "technical-metrics-v1"
TECHNICAL_LAYER_VERSION = "technical-layer-v1"
REQUIRED_LOOKBACK_DAYS = 60


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

    snapshot_data_date = data_date or _string_or_none(snapshot.get("data_date")) or _date_string_or_none(snapshot.get("fetched_at"))
    current_price = _number_or_none(snapshot.get("current_price"))
    return build_technical_profile_payload(
        closes=closes,
        highs=_numbers(snapshot.get("recent_highs")),
        lows=_numbers(snapshot.get("recent_lows")),
        volumes=_numbers(snapshot.get("recent_volumes")),
        current_price=current_price,
        data_date=snapshot_data_date,
        is_final=is_final,
    )


def build_technical_profile_payload(
    *,
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    volumes: Sequence[float] | None = None,
    current_price: float | None = None,
    data_date: str | None = None,
    is_final: bool = True,
) -> dict[str, Any] | None:
    """Return backward-compatible raw indicators plus the v1 layered profile."""
    close_values = [float(value) for value in closes if value is not None]
    if not close_values:
        return None

    high_values = _aligned_values(highs, close_values)
    low_values = _aligned_values(lows, close_values)
    volume_values = _aligned_values(volumes, close_values)
    aligned_hilo = high_values is not None and low_values is not None
    aligned_volume = volume_values is not None

    high_source = high_values if aligned_hilo else close_values
    low_source = low_values if aligned_hilo else close_values
    close = current_price if current_price is not None else close_values[-1]

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
    primary_high_20d = high_20d if aligned_hilo else None
    primary_low_20d = low_20d if aligned_hilo else None
    volume_ratio = _volume_ratio(volume_values)
    bias20 = calc_bias(close, ma20) if ma20 is not None else None
    rsi14 = calc_rsi(close_values, period=14)

    raw_indicators = {
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": rsi14,
        "bias20": bias20,
        "volume_ratio": volume_ratio,
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
    caveats = _profile_caveats(missing_fields=missing_fields, aligned_hilo=aligned_hilo, aligned_volume=aligned_volume)

    return {
        "technical_indicators": raw_indicators,
        "technical_profile": {
            "version": TECHNICAL_LAYER_VERSION,
            "primary_score_inputs": primary,
            "risk_overheat_filters": risk,
            "secondary_evidence": secondary,
            "display_only": {
                "obv_absolute_value": raw_indicators["obv"],
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
    for field in ("ma20", "macd_hist", "atr", "mfi", "kd_k", "donchian_upper", "obv_trend_20d"):
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


def _aligned_values(values: Sequence[float] | None, closes: Sequence[float]) -> list[float] | None:
    if values is None:
        return None
    numbers = [float(value) for value in values if value is not None]
    return numbers if len(numbers) == len(closes) else None


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_string_or_none(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    return text[:10] if len(text) >= 10 else text
