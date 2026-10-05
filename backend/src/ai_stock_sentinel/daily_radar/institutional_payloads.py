"""Pure conversion of institutional universe entries and archived metrics."""

from __future__ import annotations

from datetime import date
from typing import Any

from ai_stock_sentinel.daily_radar.universe import (
    DailyRadarUniverseEntry,
    SEGMENTED_INSTITUTIONAL_TRACKS,
)


def _universe_entry_payload(entry: DailyRadarUniverseEntry) -> dict[str, Any]:
    return {
        "symbol": entry.symbol,
        "rank": entry.rank,
        "primary_track": entry.primary_track,
        "tracks": list(entry.tracks),
        "same_day_rank": entry.same_day_rank,
        "same_day_score": entry.same_day_score,
        "recent_accumulation_rank": entry.recent_accumulation_rank,
        "recent_accumulation_score": entry.recent_accumulation_score,
        "track_metrics": {track: dict(metrics) for track, metrics in entry.track_metrics.items()},
    }


def _prepared_universe_entries(payloads: list[dict[str, Any]]) -> list[DailyRadarUniverseEntry]:
    entries: list[DailyRadarUniverseEntry] = []
    for payload in payloads:
        entries.append(
            DailyRadarUniverseEntry(
                symbol=str(payload["symbol"]),
                rank=int(payload["rank"]),
                primary_track=payload["primary_track"],
                tracks=tuple(payload.get("tracks") or (payload["primary_track"],)),
                same_day_rank=_optional_int(payload.get("same_day_rank")),
                same_day_score=_optional_float(payload.get("same_day_score")),
                recent_accumulation_rank=_optional_int(payload.get("recent_accumulation_rank")),
                recent_accumulation_score=_optional_float(payload.get("recent_accumulation_score")),
                track_metrics={
                    str(track): dict(metrics)
                    for track, metrics in _mapping(payload.get("track_metrics")).items()
                    if isinstance(metrics, dict)
                },
            )
        )
    return entries


def _institutional_payload(entry: DailyRadarUniverseEntry, *, run_date: date) -> dict[str, Any]:
    same_day_metrics = dict(entry.track_metrics.get("same_day_institutional") or {})
    recent_metrics = dict(entry.track_metrics.get("recent_accumulation") or {})
    segmented = any(track in SEGMENTED_INSTITUTIONAL_TRACKS for track in entry.tracks)
    flat_payload: dict[str, Any] = {
        "flow_label": "institutional_accumulation",
        "flow_state": _flow_state(entry),
        "universe_primary_track": entry.primary_track,
        "institutional_universe_tracks": list(entry.tracks),
        "universe_track_metrics": {track: dict(metrics) for track, metrics in entry.track_metrics.items()},
        "same_day_rank": entry.same_day_rank,
        "recent_accumulation_rank": entry.recent_accumulation_rank,
        "scores": _score_payload(entry),
        "source_provider": (
            "taiwan_institutional_flow_archive"
            if segmented
            else "daily_radar_universe"
        ),
        "data_dates": {"institutional_flow": _latest_institutional_date(entry, run_date=run_date)},
    }
    if segmented:
        _merge_segmented_institutional_metrics(flat_payload, entry)
    else:
        _merge_same_day_metrics(flat_payload, same_day_metrics)
        _merge_recent_metrics(flat_payload, recent_metrics)
    return flat_payload | {"institutional_flow": dict(flat_payload)}


def _flow_state(entry: DailyRadarUniverseEntry) -> str:
    recent_tracks = (
        "foreign_recent_accumulation",
        "trust_recent_accumulation",
        "recent_accumulation",
    )
    recent_metrics = [entry.track_metrics.get(track) or {} for track in recent_tracks]
    recent_positive_days = [
        value
        for metrics in recent_metrics
        if (value := _int_metric(metrics.get("consecutive_buy_days"))) is not None
    ]
    if recent_positive_days:
        return (
            "consistent_accumulation"
            if max(recent_positive_days) >= 2
            else "weak_confirmation"
        )
    for metrics in recent_metrics:
        if metrics.get("flow_state") is not None:
            return str(metrics["flow_state"])

    same_day_tracks = ("foreign_same_day", "trust_same_day", "same_day_institutional")
    for track in same_day_tracks:
        same_day_metrics = entry.track_metrics.get(track) or {}
        if same_day_metrics.get("flow_state") is not None:
            return str(same_day_metrics["flow_state"])
    if not any(track in {*same_day_tracks, *recent_tracks} for track in entry.tracks):
        return "technical_trigger"
    return "weak_confirmation"


def _score_payload(entry: DailyRadarUniverseEntry) -> dict[str, float]:
    scores: dict[str, float] = {}
    _add_score(scores, "same_day_institutional", entry.same_day_score)
    _add_score(scores, "recent_accumulation", entry.recent_accumulation_score)
    for track, metrics in entry.track_metrics.items():
        _add_score(scores, track, metrics.get("score"))
    return scores


def _add_score(scores: dict[str, float], key: str, value: float | None) -> None:
    if value is not None:
        scores[key] = float(value)


def _merge_same_day_metrics(payload: dict[str, Any], metrics: dict[str, Any]) -> None:
    same_day_actor = _normalized_actor(metrics.get("actor"))
    raw_same_day_net_buy = metrics.get("net_buy")
    same_day_net_buy = _float_metric(raw_same_day_net_buy)
    _add_payload_metric(payload, "same_day_actor", metrics.get("actor"))
    _add_payload_metric(payload, "same_day_net_buy", raw_same_day_net_buy)
    _add_payload_metric(payload, "same_day_concentration", metrics.get("concentration"))
    _add_payload_metric(payload, "same_day_source_dates", _source_dates(metrics))
    if same_day_net_buy is not None:
        if same_day_actor == "foreign":
            payload["foreign_net_shares"] = same_day_net_buy
        elif same_day_actor == "trust":
            payload["investment_trust_net_shares"] = same_day_net_buy


def _merge_recent_metrics(payload: dict[str, Any], metrics: dict[str, Any]) -> None:
    recent_actor = _normalized_actor(metrics.get("actor"))
    positive_days = _int_metric(metrics.get("consecutive_buy_days"))
    if positive_days is not None:
        payload["consecutive_buy_days"] = positive_days
        payload["consecutive_positive_days"] = positive_days

    cumulative_net_buy = _float_metric(metrics.get("cumulative_net_buy"))
    if cumulative_net_buy is not None:
        payload["cumulative_net_buy"] = cumulative_net_buy
        payload["net_buy_cumulative"] = cumulative_net_buy
        payload["three_party_net_shares"] = cumulative_net_buy
        if recent_actor == "foreign":
            payload["foreign_net_shares"] = cumulative_net_buy
        elif recent_actor == "trust":
            payload["investment_trust_net_shares"] = cumulative_net_buy

    _add_payload_metric(payload, "recent_actor", metrics.get("actor"))
    raw_recent_concentration = metrics.get("concentration")
    recent_concentration = _float_metric(raw_recent_concentration)
    _add_payload_metric(payload, "recent_concentration", raw_recent_concentration)
    _add_payload_metric(payload, "net_flow_to_avg_volume", recent_concentration)
    _add_payload_metric(payload, "recent_source_dates", _source_dates(metrics))


def _merge_segmented_institutional_metrics(
    payload: dict[str, Any],
    entry: DailyRadarUniverseEntry,
) -> None:
    same_day_values: dict[str, float] = {}
    recent_cumulative_values: dict[str, float] = {}
    recent_consecutive_days: dict[str, int] = {}
    same_day_source_dates: set[str] = set()
    recent_source_dates: set[str] = set()

    for actor, track in (
        ("foreign", "foreign_same_day"),
        ("trust", "trust_same_day"),
    ):
        metrics = dict(entry.track_metrics.get(track) or {})
        net_buy = _float_metric(metrics.get("net_buy"))
        if net_buy is None:
            continue
        same_day_values[actor] = net_buy
        payload[f"{actor}_same_day_net_shares"] = net_buy
        same_day_source_dates.update(_source_dates(metrics) or ())

    for actor, track in (
        ("foreign", "foreign_recent_accumulation"),
        ("trust", "trust_recent_accumulation"),
    ):
        metrics = dict(entry.track_metrics.get(track) or {})
        cumulative_net_buy = _float_metric(metrics.get("cumulative_net_buy"))
        consecutive_buy_days = _int_metric(metrics.get("consecutive_buy_days"))
        latest_net_buy = _float_metric(metrics.get("net_buy"))
        if cumulative_net_buy is not None:
            recent_cumulative_values[actor] = cumulative_net_buy
            payload[f"{actor}_cumulative_net_shares"] = cumulative_net_buy
        if consecutive_buy_days is not None:
            recent_consecutive_days[actor] = consecutive_buy_days
            payload[f"{actor}_consecutive_buy_days"] = consecutive_buy_days
        if latest_net_buy is not None:
            payload[f"{actor}_latest_net_shares"] = latest_net_buy
        recent_source_dates.update(_source_dates(metrics) or ())

    if same_day_values:
        same_day_actors = tuple(actor for actor in ("foreign", "trust") if actor in same_day_values)
        payload["same_day_actor"] = same_day_actors[0] if len(same_day_actors) == 1 else "mixed"
        payload["same_day_net_buy"] = sum(same_day_values.values())
        payload["same_day_source_dates"] = sorted(same_day_source_dates)

    for actor, output_key in (
        ("foreign", "foreign_net_shares"),
        ("trust", "investment_trust_net_shares"),
    ):
        current_net_buy = same_day_values.get(actor)
        if current_net_buy is None:
            current_net_buy = _float_metric(payload.get(f"{actor}_latest_net_shares"))
        if current_net_buy is not None:
            payload[output_key] = current_net_buy

    if recent_cumulative_values:
        recent_actors = tuple(
            actor for actor in ("foreign", "trust") if actor in recent_cumulative_values
        )
        cumulative_net_buy = sum(recent_cumulative_values.values())
        payload["recent_actor"] = recent_actors[0] if len(recent_actors) == 1 else "mixed"
        payload["cumulative_net_buy"] = cumulative_net_buy
        payload["net_buy_cumulative"] = cumulative_net_buy
        payload["recent_source_dates"] = sorted(recent_source_dates)
    if recent_consecutive_days:
        consecutive_buy_days = max(recent_consecutive_days.values())
        payload["consecutive_buy_days"] = consecutive_buy_days
        payload["consecutive_positive_days"] = consecutive_buy_days


def _normalized_actor(value: Any) -> str | None:
    if value is None:
        return None
    actor = str(value).strip().lower()
    if actor in {"foreign", "trust", "institutional", "mixed"}:
        return actor
    return None


def _add_payload_metric(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def _latest_institutional_date(entry: DailyRadarUniverseEntry, *, run_date: date) -> str:
    source_dates: list[str] = []
    for metrics in entry.track_metrics.values():
        source_dates.extend(_source_dates(metrics) or [])
    return max(source_dates) if source_dates else run_date.isoformat()


def _source_dates(metrics: dict[str, Any]) -> list[str] | None:
    raw_dates = metrics.get("source_dates")
    if not isinstance(raw_dates, (list, tuple)):
        return None
    source_dates = [str(value) for value in raw_dates if value]
    return source_dates or None


def _int_metric(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
