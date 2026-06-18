from __future__ import annotations

from typing import Final

from ai_stock_sentinel.daily_radar.types import (
    DailyRadarBucket,
    DailyRadarRepeatStatus,
    DailyRadarRiskLabel,
)

DAILY_RADAR_BUCKETS: Final[tuple[DailyRadarBucket, ...]] = (
    "institutional_accumulation",
    "price_volume_strengthening",
    "bottoming_reversal",
    "support_retest",
)
DAILY_RADAR_RISK_LABELS: Final[tuple[DailyRadarRiskLabel, ...]] = (
    "overextended",
    "flow_conflict",
    "margin_crowding",
    "market_weakness",
    "data_gap",
)
DAILY_RADAR_REPEAT_STATUSES: Final[tuple[DailyRadarRepeatStatus, ...]] = (
    "new",
    "repeat",
    "upgraded",
    "cooled_down",
)
DAILY_RADAR_BACKGROUND_CONTEXT_TYPES: Final[tuple[str, ...]] = (
    "weekly_major_holders",
    "lending",
    "full_margin",
)

__all__ = [
    "DAILY_RADAR_BACKGROUND_CONTEXT_TYPES",
    "DAILY_RADAR_BUCKETS",
    "DAILY_RADAR_RISK_LABELS",
    "DAILY_RADAR_REPEAT_STATUSES",
]
