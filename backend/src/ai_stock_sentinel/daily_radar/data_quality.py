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
        "three_party_net_shares",
        "consecutive_positive_days",
        "flow_state",
        "net_flow_to_avg_volume",
    ),
    "margin": (
        "margin_delta_pct",
        "margin_to_volume",
    ),
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
    return [
        f"{section}.{field}"
        for section, required_fields in REQUIRED_SCORING_FIELDS.items()
        for field in required_fields
        if field not in sections[section] or sections[section].get(field) is None
    ]


__all__ = ["REQUIRED_SCORING_FIELDS", "missing_scoring_fields"]
