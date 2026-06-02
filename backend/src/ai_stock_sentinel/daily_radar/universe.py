from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal, Protocol, Sequence, TypeAlias

DailyRadarUniverseTrack: TypeAlias = Literal[
    "same_day_institutional",
    "recent_accumulation",
]


@dataclass(frozen=True, slots=True)
class InstitutionalLeaderRow:
    symbol: str
    rank: int
    score: float | None = None
    actor: str | None = None
    net_buy: float | None = None
    cumulative_net_buy: float | None = None
    concentration: float | None = None
    consecutive_buy_days: int | None = None
    source_dates: tuple[str, ...] = ()
    flow_state: str | None = None
    bucket_hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DailyRadarUniverseEntry:
    symbol: str
    rank: int
    primary_track: DailyRadarUniverseTrack
    tracks: tuple[DailyRadarUniverseTrack, ...]
    same_day_rank: int | None = None
    same_day_score: float | None = None
    recent_accumulation_rank: int | None = None
    recent_accumulation_score: float | None = None
    track_metrics: dict[DailyRadarUniverseTrack, dict[str, Any]] = field(default_factory=dict)


class DailyRadarUniverseProvider(Protocol):
    def same_day_institutional_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]: ...

    def recent_accumulation_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]: ...


def select_dual_track_universe(
    provider: DailyRadarUniverseProvider,
    run_date: date,
    market: str = "TW",
    track_limit: int = 50,
) -> list[DailyRadarUniverseEntry]:
    entries: list[DailyRadarUniverseEntry] = []
    indexes_by_symbol: dict[str, int] = {}

    for row in provider.same_day_institutional_leaders(
        run_date=run_date,
        market=market,
        limit=track_limit,
    ):
        if row.symbol in indexes_by_symbol:
            continue
        indexes_by_symbol[row.symbol] = len(entries)
        entries.append(
            DailyRadarUniverseEntry(
                symbol=row.symbol,
                rank=len(entries) + 1,
                primary_track="same_day_institutional",
                tracks=("same_day_institutional",),
                same_day_rank=row.rank,
                same_day_score=row.score,
                track_metrics={"same_day_institutional": _leader_metrics(row)},
            )
        )

    for row in provider.recent_accumulation_leaders(
        run_date=run_date,
        market=market,
        limit=track_limit,
    ):
        existing_index = indexes_by_symbol.get(row.symbol)
        if existing_index is not None:
            existing_entry = entries[existing_index]
            if "recent_accumulation" not in existing_entry.tracks:
                track_metrics = dict(existing_entry.track_metrics)
                track_metrics["recent_accumulation"] = _leader_metrics(row)
                entries[existing_index] = replace(
                    existing_entry,
                    tracks=(*existing_entry.tracks, "recent_accumulation"),
                    recent_accumulation_rank=row.rank,
                    recent_accumulation_score=row.score,
                    track_metrics=track_metrics,
                )
            continue

        indexes_by_symbol[row.symbol] = len(entries)
        entries.append(
            DailyRadarUniverseEntry(
                symbol=row.symbol,
                rank=len(entries) + 1,
                primary_track="recent_accumulation",
                tracks=("recent_accumulation",),
                recent_accumulation_rank=row.rank,
                recent_accumulation_score=row.score,
                track_metrics={"recent_accumulation": _leader_metrics(row)},
            )
        )

    return entries


def _leader_metrics(row: InstitutionalLeaderRow) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    _add_metric(metrics, "actor", row.actor)
    _add_metric(metrics, "net_buy", row.net_buy)
    _add_metric(metrics, "cumulative_net_buy", row.cumulative_net_buy)
    _add_metric(metrics, "concentration", row.concentration)
    _add_metric(metrics, "consecutive_buy_days", row.consecutive_buy_days)
    _add_metric(metrics, "flow_state", row.flow_state)
    if row.source_dates:
        metrics["source_dates"] = list(row.source_dates)
    if row.bucket_hints:
        metrics["bucket_hints"] = list(row.bucket_hints)
    return metrics


def _add_metric(metrics: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        metrics[key] = value


__all__ = [
    "DailyRadarUniverseEntry",
    "DailyRadarUniverseProvider",
    "DailyRadarUniverseTrack",
    "InstitutionalLeaderRow",
    "select_dual_track_universe",
]
