from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
    "same_day_institutional": ("same_day_actor", "same_day_net_buy"),
    "recent_accumulation": ("three_party_net_shares", "consecutive_positive_days"),
}


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
        if field not in sections[section] or sections[section].get(field) is None
    ]
    required_institutional_fields = _required_institutional_fields(institutional_flow)
    missing_fields.extend(
        f"institutional_flow.{field}"
        for field in required_institutional_fields
        if field not in institutional_flow or institutional_flow.get(field) is None
    )
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
        if field not in sections[section] or sections[section].get(field) is None
    ]


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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "REQUIRED_SCORING_FIELDS",
    "missing_scoring_fields",
    "missing_technical_scoring_fields",
    "required_institutional_scoring_fields",
]
