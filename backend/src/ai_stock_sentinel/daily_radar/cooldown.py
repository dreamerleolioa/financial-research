from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ai_stock_sentinel.daily_radar.constants import (
    DAILY_RADAR_REPEAT_STATUSES,
)
from ai_stock_sentinel.daily_radar.types import DailyRadarRepeatStatus


REPEAT_STATUS_NEW: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[0]
REPEAT_STATUS_REPEAT: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[1]
REPEAT_STATUS_UPGRADED: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[2]
REPEAT_STATUS_COOLED_DOWN: DailyRadarRepeatStatus = DAILY_RADAR_REPEAT_STATUSES[3]

COOLDOWN_REPEAT_STATUS_LABELS: dict[DailyRadarRepeatStatus, str] = {
    REPEAT_STATUS_NEW: "入選歷史待確認",
    REPEAT_STATUS_REPEAT: "曾列入觀察",
    REPEAT_STATUS_UPGRADED: "訊號升級",
    REPEAT_STATUS_COOLED_DOWN: "訊號冷卻",
}


@dataclass(frozen=True)
class CooldownConfig:
    lookback_days: int = 5
    score_upgrade_threshold: int = 8
    min_current_signal_score: int = 60



def apply_cooldown_status(
    today_candidates: Iterable[Mapping[str, Any]],
    history_candidates: Iterable[Mapping[str, Any]],
    *,
    run_date: str | date,
    config: CooldownConfig | None = None,
    include_cooled_down: bool = False,
    trading_dates: Iterable[str | date] | None = None,
) -> list[dict[str, Any]]:
    active_config = config or CooldownConfig()
    history_rows = list(history_candidates)
    verified_trading_dates = list(trading_dates or [])
    recent_history = _latest_recent_history(history_rows, run_date, active_config.lookback_days)
    all_history = _history_by_symbol(history_rows, run_date)
    today_symbols: dict[str, None] = {}
    results: list[dict[str, Any]] = []

    for candidate in today_candidates:
        symbol = str(candidate["symbol"])
        today_symbols[symbol] = None
        symbol_history = all_history.get(symbol, [])
        history = symbol_history[0] if symbol_history else None
        current_score = _int(candidate.get("observation_score"))

        result = dict(candidate)
        result["repeat_status"] = _repeat_status_for_candidate(candidate, history, active_config)
        if current_score < active_config.min_current_signal_score:
            result = _cooled_down_candidate(result, reason="current_signal_below_threshold", config=active_config)
        snapshot = dict(candidate.get("input_snapshot") or {})
        snapshot["observation_history"] = observation_history(
            candidate, symbol_history, run_date=run_date, config=active_config, trading_dates=verified_trading_dates,
        )
        result["input_snapshot"] = snapshot
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
    if _versions_match(candidate, history) and _score_upgraded(candidate, history, config):
        return REPEAT_STATUS_UPGRADED
    return REPEAT_STATUS_REPEAT


def _versions_match(candidate: Mapping[str, Any], history: Mapping[str, Any]) -> bool:
    def version(row: Mapping[str, Any]) -> Any:
        return row.get("scoring_version") or (row.get("score_breakdown") or {}).get("scoring_version")
    current_version = version(candidate)
    return current_version is not None and current_version == version(history)


def _history_by_symbol(rows: Iterable[Mapping[str, Any]], run_date: str | date) -> dict[str, list[Mapping[str, Any]]]:
    by_symbol: dict[str, dict[date, Mapping[str, Any]]] = {}
    for row in rows:
        day = _parse_date(row["record_date"])
        if day < _parse_date(run_date):
            by_symbol.setdefault(str(row["symbol"]), {}).setdefault(day, row)
    return {symbol: [dates[day] for day in sorted(dates, reverse=True)] for symbol, dates in by_symbol.items()}


def observation_history(
    candidate: Mapping[str, Any], history: Iterable[Mapping[str, Any]], *, run_date: str | date,
    config: CooldownConfig | None = None, trading_dates: Iterable[str | date] | None = None,
) -> dict[str, Any]:
    active_config = config or CooldownConfig()
    run_day = _parse_date(run_date)
    rows = _history_by_symbol(history, run_day).get(str(candidate["symbol"]), [])
    days = {_parse_date(row["record_date"]) for row in rows}
    calendar = sorted({_parse_date(day) for day in trading_dates or [] if _parse_date(day) <= run_day})
    previous_day = next((day for day in reversed(calendar) if day < run_day), None)
    membership = "new" if not rows else "previously_selected"
    consecutive = None
    if run_day in calendar and previous_day is not None:
        if rows:
            membership = "continuing" if previous_day in days else "returning"
        consecutive = 1
        for day in reversed(calendar[:-1]):
            if day not in days:
                break
            consecutive += 1
        if consecutive == len(calendar) and any(day < calendar[0] for day in days):
            consecutive = None
    signal = "stable"
    if _int(candidate.get("observation_score")) < active_config.min_current_signal_score:
        signal = "cooled_down"
    elif rows and not _versions_match(candidate, rows[0]):
        signal = "unknown"
    elif rows and _score_upgraded(candidate, rows[0], active_config):
        signal = "improved"
    return {
        "membership_status": membership,
        "first_seen_date": min(days | {run_day}).isoformat(),
        "last_seen_date": max(days).isoformat() if days else None,
        "appearance_count": len(days) + 1,
        "consecutive_trading_days": consecutive,
        "signal_status": signal,
        "score_comparison": _score_comparison(candidate, rows[0] if rows else None),
    }


def _score_comparison(candidate: Mapping[str, Any], previous: Mapping[str, Any] | None) -> dict[str, Any]:
    result = {"status": "first_observation", "previous_date": None, "previous_score": None, "score_change": None}
    if previous is None:
        return result
    result.update(previous_date=str(previous["record_date"]), previous_score=_int(previous.get("observation_score")))
    def identity(row):
        snapshot = row.get("input_snapshot") or {}
        versions = row.get("strategy_versions") or snapshot.get("versions") or {}
        keys = ("scoring_version", "rule_version", "config_version")
        return tuple(versions.get(key) for key in keys) + (row.get("selection_version") or snapshot.get("selection_version"),)
    current, prior = identity(candidate), identity(previous)
    if not all(current) or not all(prior):
        return result | {"status": "unavailable"}
    if current != prior:
        return result | {"status": "version_changed"}
    return result | {"status": "comparable", "score_change": _int(candidate.get("observation_score")) - result["previous_score"]}


def radar_trading_dates(context: Mapping[str, Any]) -> list[str]:
    benchmark = context.get("benchmark") or {}
    return [str(row["date"]) for row in benchmark.get("price_history", [])
            if isinstance(row, Mapping) and row.get("date")]


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
