from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ai_stock_sentinel.daily_radar.constants import (
    DAILY_RADAR_BUCKETS,
    DAILY_RADAR_REPEAT_STATUSES,
)
from ai_stock_sentinel.daily_radar.types import DailyRadarBucket, DailyRadarRepeatStatus


REPEAT_STATUS_NEW: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[0]
REPEAT_STATUS_REPEAT: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[1]
REPEAT_STATUS_UPGRADED: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[2]
REPEAT_STATUS_COOLED_DOWN: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[3]

COOLDOWN_REPEAT_STATUS_LABELS: dict[DailyRadarRepeatStatus, str] = {
    REPEAT_STATUS_NEW: "首次觀察",
    REPEAT_STATUS_REPEAT: "連續觀察",
    REPEAT_STATUS_UPGRADED: "訊號升級",
    REPEAT_STATUS_COOLED_DOWN: "訊號冷卻",
}


@dataclass(frozen=True)
class CooldownConfig:
    lookback_days: int = 5
    score_upgrade_threshold: int = 8
    min_current_signal_score: int = 60
    bucket_upgrade_steps: int = 1
    bucket_strength_order: tuple[DailyRadarBucket, ...] = field(
        default_factory=lambda: tuple(reversed(DAILY_RADAR_BUCKETS))
    )


def apply_cooldown_status(
    today_candidates: Iterable[Mapping[str, Any]],
    history_candidates: Iterable[Mapping[str, Any]],
    *,
    run_date: str | date,
    config: CooldownConfig | None = None,
    include_cooled_down: bool = False,
) -> list[dict[str, Any]]:
    active_config = config or CooldownConfig()
    recent_history = _latest_recent_history(history_candidates, run_date, active_config.lookback_days)
    today_symbols: dict[str, None] = {}
    results: list[dict[str, Any]] = []

    for candidate in today_candidates:
        symbol = str(candidate["symbol"])
        today_symbols[symbol] = None
        history = recent_history.get(symbol)
        current_score = _int(candidate.get("observation_score"))

        if history is not None and current_score < active_config.min_current_signal_score:
            if include_cooled_down:
                results.append(
                    _cooled_down_candidate(
                        candidate,
                        reason="current_signal_below_threshold",
                        config=active_config,
                    )
                )
            continue

        result = dict(candidate)
        result["repeat_status"] = _repeat_status_for_candidate(candidate, history, active_config)
        results.append(result)

    if include_cooled_down:
        for symbol, history in sorted(recent_history.items()):
            if symbol not in today_symbols:
                results.append(
                    _cooled_down_candidate(
                        history,
                        reason="absent_from_current_candidates",
                        config=active_config,
                    )
                )

    return results


def repeat_status_label(status: DailyRadarRepeatStatus) -> str:
    return COOLDOWN_REPEAT_STATUS_LABELS[status]


def _repeat_status_for_candidate(
    candidate: Mapping[str, Any],
    history: Mapping[str, Any] | None,
    config: CooldownConfig,
) -> DailyRadarRepeatStatus:
    if history is None:
        return REPEAT_STATUS_NEW
    if _score_upgraded(candidate, history, config) or _bucket_upgraded(candidate, history, config):
        return REPEAT_STATUS_UPGRADED
    return REPEAT_STATUS_REPEAT


def _latest_recent_history(
    history_candidates: Iterable[Mapping[str, Any]],
    run_date: str | date,
    lookback_days: int,
) -> dict[str, Mapping[str, Any]]:
    run_day = _parse_date(run_date)
    earliest_day = run_day - timedelta(days=lookback_days)
    latest: dict[str, Mapping[str, Any]] = {}
    latest_dates: dict[str, date] = {}

    for history in history_candidates:
        history_day = _parse_date(history["record_date"])
        if history_day < earliest_day or history_day >= run_day:
            continue

        symbol = str(history["symbol"])
        if symbol not in latest_dates or history_day > latest_dates[symbol]:
            latest[symbol] = history
            latest_dates[symbol] = history_day

    return latest


def _score_upgraded(
    candidate: Mapping[str, Any],
    history: Mapping[str, Any],
    config: CooldownConfig,
) -> bool:
    current_score = _int(candidate.get("observation_score"))
    previous_score = _int(history.get("observation_score"))
    return current_score - previous_score >= config.score_upgrade_threshold


def _bucket_upgraded(
    candidate: Mapping[str, Any],
    history: Mapping[str, Any],
    config: CooldownConfig,
) -> bool:
    current_strength = _strongest_bucket_rank(candidate, config.bucket_strength_order)
    previous_strength = _strongest_bucket_rank(history, config.bucket_strength_order)
    return current_strength - previous_strength >= config.bucket_upgrade_steps


def _strongest_bucket_rank(
    candidate: Mapping[str, Any],
    bucket_strength_order: tuple[DailyRadarBucket, ...],
) -> int:
    ranks = {bucket: index for index, bucket in enumerate(bucket_strength_order)}
    buckets = [str(candidate["primary_bucket"]), *[str(bucket) for bucket in candidate.get("secondary_buckets", [])]]
    return max(ranks.get(bucket, -1) for bucket in buckets)


def _cooled_down_candidate(
    candidate: Mapping[str, Any],
    *,
    reason: str,
    config: CooldownConfig,
) -> dict[str, Any]:
    result = dict(candidate)
    result["repeat_status"] = REPEAT_STATUS_COOLED_DOWN
    result["cooldown_reason"] = reason
    result["cooldown_thresholds"] = {
        "lookback_days": config.lookback_days,
        "min_current_signal_score": config.min_current_signal_score,
    }
    return result


def _parse_date(value: str | date | Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _int(value: Any) -> int:
    return int(value or 0)


__all__ = [
    "COOLDOWN_REPEAT_STATUS_LABELS",
    "CooldownConfig",
    "apply_cooldown_status",
    "repeat_status_label",
]
