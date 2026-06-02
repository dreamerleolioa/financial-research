from __future__ import annotations

from typing import Literal, TypeAlias

DailyRadarBucket: TypeAlias = Literal[
    "institutional_accumulation",
    "price_volume_strengthening",
    "bottoming_reversal",
    "support_retest",
]
DailyRadarRiskLabel: TypeAlias = Literal[
    "overextended",
    "flow_conflict",
    "margin_crowding",
    "market_weakness",
    "data_gap",
]
DailyRadarRepeatStatus: TypeAlias = Literal[
    "new",
    "repeat",
    "upgraded",
    "cooled_down",
]

__all__ = [
    "DailyRadarBucket",
    "DailyRadarRiskLabel",
    "DailyRadarRepeatStatus",
]
