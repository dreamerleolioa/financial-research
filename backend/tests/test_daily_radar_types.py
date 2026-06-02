from __future__ import annotations

from typing import Any, get_args

import ai_stock_sentinel.daily_radar as daily_radar
from ai_stock_sentinel.daily_radar import constants, types

EXPECTED_BUCKETS = (
    "institutional_accumulation",
    "price_volume_strengthening",
    "bottoming_reversal",
    "support_retest",
)
EXPECTED_RISK_LABELS = (
    "overextended",
    "flow_conflict",
    "margin_crowding",
    "market_weakness",
    "data_gap",
)
EXPECTED_REPEAT_STATUSES = (
    "new",
    "repeat",
    "upgraded",
    "cooled_down",
)
PROHIBITED_TRADING_CONTRACT_TERMS = (
    "buy",
    "sell",
    "recommendation",
)


def _public_contract_names(module: Any) -> tuple[str, ...]:
    return tuple(name for name in vars(module) if not name.startswith("_"))


def _string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def test_daily_radar_bucket_constants_and_type_alias_match_spec() -> None:
    assert constants.DAILY_RADAR_BUCKETS == EXPECTED_BUCKETS
    assert get_args(types.DailyRadarBucket) == EXPECTED_BUCKETS
    assert daily_radar.DAILY_RADAR_BUCKETS == EXPECTED_BUCKETS


def test_daily_radar_risk_label_constants_and_type_alias_match_spec() -> None:
    assert constants.DAILY_RADAR_RISK_LABELS == EXPECTED_RISK_LABELS
    assert get_args(types.DailyRadarRiskLabel) == EXPECTED_RISK_LABELS
    assert daily_radar.DAILY_RADAR_RISK_LABELS == EXPECTED_RISK_LABELS


def test_daily_radar_repeat_status_constants_and_type_alias_match_spec() -> None:
    assert constants.DAILY_RADAR_REPEAT_STATUSES == EXPECTED_REPEAT_STATUSES
    assert get_args(types.DailyRadarRepeatStatus) == EXPECTED_REPEAT_STATUSES
    assert daily_radar.DAILY_RADAR_REPEAT_STATUSES == EXPECTED_REPEAT_STATUSES


def test_daily_radar_public_contract_has_no_trading_recommendation_enum_or_constant() -> None:
    contract_modules = (constants, types, daily_radar)
    public_names = tuple(
        name
        for module in contract_modules
        for name in _public_contract_names(module)
    )
    public_values = tuple(
        string_value
        for module in contract_modules
        for name in _public_contract_names(module)
        for string_value in _string_values(getattr(module, name))
    )

    for term in PROHIBITED_TRADING_CONTRACT_TERMS:
        assert all(term not in name.lower() for name in public_names)
        assert all(term not in value.lower() for value in public_values)
