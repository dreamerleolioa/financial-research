from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from typing import Any

from ai_stock_sentinel.daily_radar.universe import TRACK_PRIORITY
from ai_stock_sentinel.technical.profile import (
    TECHNICAL_LAYER_VERSION,
    TECHNICAL_METRICS_VERSION,
)


DAILY_RADAR_REPLAY_INPUT_VERSION = "daily-radar-replay-input-v2"
REQUIRED_SCORING_FIELDS: dict[str, tuple[str, ...]] = {
    "ohlcv": (
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "volume",
        "avg_volume_20",
    ),
    "indicators": (
        "ma5",
        "ma20",
        "ma60",
        "rsi14",
        "bias20",
        "macd_histogram",
        "kd_k",
        "kd_d",
        "atr14",
        "volume_ratio",
        "missing_trading_days_60",
    ),
    "institutional_flow": (
        "flow_state",
    ),
    "margin": (
        "margin_delta_pct",
        "margin_to_volume",
    ),
}

_LEGACY_INSTITUTIONAL_REQUIRED_FIELDS = (
    "three_party_net_shares",
    "consecutive_positive_days",
    "flow_state",
    "net_flow_to_avg_volume",
)
_TRACK_REQUIRED_INSTITUTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "foreign_same_day": ("foreign_same_day_net_shares",),
    "trust_same_day": ("trust_same_day_net_shares",),
    "foreign_recent_accumulation": (
        "foreign_cumulative_net_shares",
        "foreign_consecutive_buy_days",
    ),
    "trust_recent_accumulation": (
        "trust_cumulative_net_shares",
        "trust_consecutive_buy_days",
    ),
    "same_day_institutional": ("same_day_actor", "same_day_net_buy"),
    "recent_accumulation": ("three_party_net_shares", "consecutive_positive_days"),
}
_TEXT_REQUIRED_SCORING_FIELDS = frozenset(
    {
        ("institutional_flow", "flow_state"),
        ("institutional_flow", "same_day_actor"),
    }
)
_KNOWN_UNIVERSE_TRACKS = frozenset(TRACK_PRIORITY)
REQUIRED_TECHNICAL_DATA_DATE_FIELDS = (
    "ohlcv",
    "technical_indicators",
    "technical_profile",
)


def missing_scoring_fields(
    *,
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
    institutional_flow: Mapping[str, Any],
    margin: Mapping[str, Any],
) -> list[str]:
    sections = {
        "ohlcv": ohlcv,
        "indicators": indicators,
        "institutional_flow": institutional_flow,
        "margin": margin,
    }
    missing_fields = [
        f"{section}.{field}"
        for section, required_fields in REQUIRED_SCORING_FIELDS.items()
        for field in required_fields
        if _missing_required_scoring_value(section, field, sections[section])
    ]
    required_institutional_fields = _required_institutional_fields(institutional_flow)
    missing_fields.extend(
        f"institutional_flow.{field}"
        for field in required_institutional_fields
        if _missing_required_scoring_value("institutional_flow", field, institutional_flow)
    )
    if _unknown_institutional_tracks(institutional_flow):
        missing_fields.append("institutional_flow.universe_track")
    return list(dict.fromkeys(missing_fields))


def missing_technical_scoring_fields(technical: Mapping[str, Any]) -> list[str]:
    sections = {
        "ohlcv": _mapping(technical.get("ohlcv")),
        "indicators": _mapping(technical.get("indicators")),
    }
    return [
        f"{section}.{field}"
        for section in ("ohlcv", "indicators")
        for field in REQUIRED_SCORING_FIELDS[section]
        if field not in sections[section] or not _is_finite_number(sections[section].get(field))
    ]


def missing_daily_radar_candidate_technical_fields(
    technical: Mapping[str, Any],
    *,
    record_date: date | None = None,
) -> list[str]:
    missing_fields = missing_technical_scoring_fields(technical)
    technical_profile = _mapping(technical.get("technical_profile"))
    missing_fields.extend(
        missing_current_technical_contract_fields(
            ohlcv=_mapping(technical.get("ohlcv")),
            indicators=_mapping(technical.get("indicators")),
            technical_profile=technical_profile,
        )
    )

    price_history = technical.get("price_history")
    if not _has_replayable_price_history(price_history, record_date=record_date):
        missing_fields.append("price_history")

    data_dates = _mapping(technical.get("data_dates"))
    for field in REQUIRED_TECHNICAL_DATA_DATE_FIELDS:
        if not _valid_data_date(data_dates.get(field), record_date=record_date):
            missing_fields.append(f"data_dates.{field}")
    return missing_fields


def technical_data_dates_match_record_date(
    technical: Mapping[str, Any],
    *,
    record_date: date,
) -> bool:
    data_dates = _mapping(technical.get("data_dates"))
    return all(
        _parse_data_date(data_dates.get(field)) == record_date
        for field in REQUIRED_TECHNICAL_DATA_DATE_FIELDS
    )


def missing_current_technical_contract_fields(
    *,
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
    technical_profile: Mapping[str, Any],
) -> list[str]:
    """Validate fields whose semantics changed with the current technical layer."""
    missing: list[str] = []
    if str(technical_profile.get("version") or "").strip() != TECHNICAL_LAYER_VERSION:
        missing.append("technical_profile.version")

    formula_versions = _mapping(technical_profile.get("formula_versions"))
    if formula_versions.get("metrics") != TECHNICAL_METRICS_VERSION:
        missing.append("technical_profile.formula_versions.metrics")
    if formula_versions.get("layering") != TECHNICAL_LAYER_VERSION:
        missing.append("technical_profile.formula_versions.layering")

    data_quality = _mapping(technical_profile.get("data_quality"))
    required_quality_values = {
        "ohlcv_aligned": True,
        "price_level_basis": "ohlc_high_low",
        "price_level_completed_bars_only": True,
        "price_level_missing_reason": None,
    }
    for field, expected in required_quality_values.items():
        if field not in data_quality or data_quality.get(field) != expected:
            missing.append(f"technical_profile.data_quality.{field}")

    if not _is_finite_number(indicators.get("macd_hist_pct")):
        missing.append("indicators.macd_hist_pct")
    for field in ("support_level", "resistance_level"):
        if not _is_positive_finite_number(indicators.get(field)):
            missing.append(f"indicators.{field}")
    if not _is_positive_finite_number(ohlcv.get("close")):
        missing.append("ohlcv.close_positive")
    if (
        _is_positive_finite_number(indicators.get("support_level"))
        and _is_positive_finite_number(indicators.get("resistance_level"))
        and float(indicators["support_level"]) > float(indicators["resistance_level"])
    ):
        missing.append("indicators.support_resistance_order")

    if (
        _is_finite_number(indicators.get("macd_hist_pct"))
        and _is_finite_number(indicators.get("macd_histogram"))
        and _is_finite_number(ohlcv.get("close"))
    ):
        close = float(ohlcv["close"])
        expected_pct = float(indicators["macd_histogram"]) / close * 100 if close > 0 else math.nan
        if not math.isfinite(expected_pct) or not math.isclose(
            float(indicators["macd_hist_pct"]),
            expected_pct,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            missing.append("indicators.macd_hist_pct_consistency")
    return list(dict.fromkeys(missing))


def required_institutional_scoring_fields(
    institutional_flow: Mapping[str, Any],
) -> tuple[str, ...]:
    return _required_institutional_fields(institutional_flow)


def _required_institutional_fields(institutional_flow: Mapping[str, Any]) -> tuple[str, ...]:
    tracks = _institutional_tracks(institutional_flow)
    if not tracks:
        return _LEGACY_INSTITUTIONAL_REQUIRED_FIELDS

    required_fields = list(REQUIRED_SCORING_FIELDS["institutional_flow"])
    for track in tracks:
        required_fields.extend(_TRACK_REQUIRED_INSTITUTIONAL_FIELDS.get(track, ()))
    return tuple(dict.fromkeys(required_fields))


def _institutional_tracks(institutional_flow: Mapping[str, Any]) -> tuple[str, ...]:
    tracks: list[str] = []
    raw_tracks = institutional_flow.get("institutional_universe_tracks")
    if isinstance(raw_tracks, (list, tuple, set, frozenset)):
        tracks.extend(str(track).strip() for track in raw_tracks if str(track).strip())
    primary_track = str(institutional_flow.get("universe_primary_track") or "").strip()
    if primary_track:
        tracks.append(primary_track)
    return tuple(dict.fromkeys(tracks))


def _unknown_institutional_tracks(institutional_flow: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        track
        for track in _institutional_tracks(institutional_flow)
        if track not in _KNOWN_UNIVERSE_TRACKS
    )


def _has_replayable_price_history(value: Any, *, record_date: date | None) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, Mapping) or not _is_finite_number(item.get("close")):
            return False
        if not _valid_data_date(item.get("date"), record_date=record_date):
            return False
    return True


def _parse_data_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _valid_data_date(value: Any, *, record_date: date | None) -> bool:
    parsed = _parse_data_date(value)
    if parsed is None:
        return False
    return record_date is None or parsed <= record_date


def _is_finite_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _is_positive_finite_number(value: Any) -> bool:
    return _is_finite_number(value) and float(value) > 0


def margin_evidence_is_complete(margin: Mapping[str, Any]) -> bool:
    delta_pct_available = _is_finite_number(margin.get("margin_delta_pct")) or (
        margin.get("margin_delta_pct_unavailable_reason") == "baseline_zero"
    )
    return delta_pct_available and _is_finite_number(margin.get("margin_to_volume"))


def _missing_required_scoring_value(
    section: str,
    field: str,
    payload: Mapping[str, Any],
) -> bool:
    value = payload.get(field)
    if section == "margin" and field == "margin_delta_pct":
        return not (
            _is_finite_number(value)
            or payload.get("margin_delta_pct_unavailable_reason") == "baseline_zero"
        )
    if (section, field) in _TEXT_REQUIRED_SCORING_FIELDS:
        return not isinstance(value, str) or not value.strip()
    return not _is_finite_number(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "DAILY_RADAR_REPLAY_INPUT_VERSION",
    "REQUIRED_TECHNICAL_DATA_DATE_FIELDS",
    "REQUIRED_SCORING_FIELDS",
    "margin_evidence_is_complete",
    "missing_scoring_fields",
    "missing_daily_radar_candidate_technical_fields",
    "missing_current_technical_contract_fields",
    "missing_technical_scoring_fields",
    "required_institutional_scoring_fields",
    "technical_data_dates_match_record_date",
]
