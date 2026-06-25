from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal, Protocol, Sequence, TypeAlias

DailyRadarUniverseTrack: TypeAlias = Literal[
    "same_day_institutional",
    "recent_accumulation",
    "price_volume",
    "reversal",
    "support_retake",
]

INSTITUTIONAL_TRACKS: tuple[DailyRadarUniverseTrack, ...] = (
    "same_day_institutional",
    "recent_accumulation",
)
TECHNICAL_TRIGGER_TRACKS: tuple[DailyRadarUniverseTrack, ...] = (
    "price_volume",
    "reversal",
    "support_retake",
)
TRACK_PRIORITY: tuple[DailyRadarUniverseTrack, ...] = (*INSTITUTIONAL_TRACKS, *TECHNICAL_TRIGGER_TRACKS)


def is_daily_radar_supported_symbol(symbol: str) -> bool:
    normalized = str(symbol).strip().upper()
    if normalized.endswith(".TW"):
        return is_daily_radar_supported_tw_stock_id(normalized.removesuffix(".TW"))
    return True


def is_daily_radar_supported_tw_stock_id(stock_id: str) -> bool:
    normalized = str(stock_id).strip().upper()
    if not normalized.isalnum():
        return False
    if len(normalized) == 4 and normalized.isdigit():
        return True
    return normalized.startswith("00")


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


@dataclass(frozen=True, slots=True)
class TechnicalTriggerRow:
    symbol: str
    track: DailyRadarUniverseTrack
    rank: int
    score: float
    metrics: dict[str, Any] = field(default_factory=dict)


def select_dual_track_universe(
    provider: DailyRadarUniverseProvider,
    run_date: date,
    market: str = "TW",
    track_limit: int = 50,
) -> list[DailyRadarUniverseEntry]:
    return select_daily_radar_universe(
        provider,
        run_date,
        market=market,
        track_limit=track_limit,
        technical_records=None,
    )


def select_daily_radar_universe(
    provider: DailyRadarUniverseProvider,
    run_date: date,
    market: str = "TW",
    track_limit: int = 50,
    technical_records: Iterable[Any] | None = None,
) -> list[DailyRadarUniverseEntry]:
    entries: list[DailyRadarUniverseEntry] = []
    indexes_by_symbol: dict[str, int] = {}

    _merge_institutional_rows(
        entries,
        indexes_by_symbol,
        track="same_day_institutional",
        rows=provider.same_day_institutional_leaders(
            run_date=run_date,
            market=market,
            limit=track_limit,
        ),
    )
    _merge_institutional_rows(
        entries,
        indexes_by_symbol,
        track="recent_accumulation",
        rows=provider.recent_accumulation_leaders(
            run_date=run_date,
            market=market,
            limit=track_limit,
        ),
    )

    technical_metrics_by_symbol = _technical_track_metrics_by_symbol(technical_records)
    for track in TECHNICAL_TRIGGER_TRACKS:
        trigger_rows = _ranked_technical_trigger_rows(
            track,
            technical_metrics_by_symbol,
            track_limit=track_limit,
        )
        _merge_technical_rows(entries, indexes_by_symbol, rows=trigger_rows)

    _attach_missing_technical_trace(entries, technical_metrics_by_symbol)
    return [replace(entry, rank=index + 1) for index, entry in enumerate(entries)]


def refresh_daily_radar_universe_technical_tracks(
    universe: Iterable[DailyRadarUniverseEntry],
    technical_records: Iterable[Any] | None,
) -> list[DailyRadarUniverseEntry]:
    entries = [_entry_without_technical_tracks(entry) for entry in universe]
    indexes_by_symbol = {entry.symbol: index for index, entry in enumerate(entries)}
    technical_metrics_by_symbol = _technical_track_metrics_by_symbol(technical_records)
    for track in TECHNICAL_TRIGGER_TRACKS:
        trigger_rows = _ranked_technical_trigger_rows(
            track,
            technical_metrics_by_symbol,
            track_limit=len(entries) or 1,
        )
        _merge_technical_rows(entries, indexes_by_symbol, rows=trigger_rows)
    _attach_missing_technical_trace(entries, technical_metrics_by_symbol)
    return [replace(entry, rank=index + 1) for index, entry in enumerate(entries)]


def _entry_without_technical_tracks(entry: DailyRadarUniverseEntry) -> DailyRadarUniverseEntry:
    retained_tracks = tuple(track for track in entry.tracks if track not in TECHNICAL_TRIGGER_TRACKS)
    retained_metrics = {
        track: dict(metrics)
        for track, metrics in entry.track_metrics.items()
        if track not in TECHNICAL_TRIGGER_TRACKS
    }
    return replace(
        entry,
        primary_track=retained_tracks[0] if retained_tracks else entry.primary_track,
        tracks=retained_tracks,
        track_metrics=retained_metrics,
    )


def _merge_institutional_rows(
    entries: list[DailyRadarUniverseEntry],
    indexes_by_symbol: dict[str, int],
    *,
    track: DailyRadarUniverseTrack,
    rows: Sequence[InstitutionalLeaderRow],
) -> None:
    seen_in_track: set[str] = set()
    for row in rows:
        if row.symbol in seen_in_track:
            continue
        seen_in_track.add(row.symbol)
        existing_index = indexes_by_symbol.get(row.symbol)
        if existing_index is not None:
            entries[existing_index] = _entry_with_institutional_track(entries[existing_index], track, row)
            continue

        indexes_by_symbol[row.symbol] = len(entries)
        entries.append(_institutional_entry(row, track, rank=len(entries) + 1))


def _institutional_entry(
    row: InstitutionalLeaderRow,
    track: DailyRadarUniverseTrack,
    *,
    rank: int,
) -> DailyRadarUniverseEntry:
    same_day_rank = row.rank if track == "same_day_institutional" else None
    same_day_score = row.score if track == "same_day_institutional" else None
    recent_rank = row.rank if track == "recent_accumulation" else None
    recent_score = row.score if track == "recent_accumulation" else None
    return DailyRadarUniverseEntry(
        symbol=row.symbol,
        rank=rank,
        primary_track=track,
        tracks=(track,),
        same_day_rank=same_day_rank,
        same_day_score=same_day_score,
        recent_accumulation_rank=recent_rank,
        recent_accumulation_score=recent_score,
        track_metrics={track: _leader_metrics(row)},
    )


def _entry_with_institutional_track(
    entry: DailyRadarUniverseEntry,
    track: DailyRadarUniverseTrack,
    row: InstitutionalLeaderRow,
) -> DailyRadarUniverseEntry:
    if track in entry.tracks:
        return entry
    track_metrics = dict(entry.track_metrics)
    track_metrics[track] = _leader_metrics(row)
    return replace(
        entry,
        tracks=_ordered_tracks((*entry.tracks, track)),
        same_day_rank=row.rank if track == "same_day_institutional" else entry.same_day_rank,
        same_day_score=row.score if track == "same_day_institutional" else entry.same_day_score,
        recent_accumulation_rank=row.rank if track == "recent_accumulation" else entry.recent_accumulation_rank,
        recent_accumulation_score=row.score if track == "recent_accumulation" else entry.recent_accumulation_score,
        track_metrics=track_metrics,
    )


def _merge_technical_rows(
    entries: list[DailyRadarUniverseEntry],
    indexes_by_symbol: dict[str, int],
    *,
    rows: Sequence[TechnicalTriggerRow],
) -> None:
    for row in rows:
        existing_index = indexes_by_symbol.get(row.symbol)
        if existing_index is not None:
            existing_entry = entries[existing_index]
            track_metrics = dict(existing_entry.track_metrics)
            track_metrics[row.track] = dict(row.metrics) | {"rank": row.rank, "score": row.score, "matched": True}
            entries[existing_index] = replace(
                existing_entry,
                tracks=_ordered_tracks((*existing_entry.tracks, row.track)),
                track_metrics=track_metrics,
            )
            continue

        indexes_by_symbol[row.symbol] = len(entries)
        entries.append(
            DailyRadarUniverseEntry(
                symbol=row.symbol,
                rank=len(entries) + 1,
                primary_track=row.track,
                tracks=(row.track,),
                track_metrics={row.track: dict(row.metrics) | {"rank": row.rank, "score": row.score, "matched": True}},
            )
        )


def _ranked_technical_trigger_rows(
    track: DailyRadarUniverseTrack,
    metrics_by_symbol: Mapping[str, Mapping[DailyRadarUniverseTrack, dict[str, Any]]],
    *,
    track_limit: int,
) -> list[TechnicalTriggerRow]:
    rows: list[TechnicalTriggerRow] = []
    for symbol, metrics_by_track in metrics_by_symbol.items():
        metrics = metrics_by_track.get(track)
        if not metrics or not metrics.get("matched"):
            continue
        rows.append(TechnicalTriggerRow(symbol=symbol, track=track, rank=0, score=float(metrics["score"]), metrics=dict(metrics)))
    rows.sort(key=lambda row: (-row.score, row.symbol))
    return [replace(row, rank=index + 1) for index, row in enumerate(rows[:track_limit])]


def _technical_track_metrics_by_symbol(
    technical_records: Iterable[Any] | None,
) -> dict[str, dict[DailyRadarUniverseTrack, dict[str, Any]]]:
    metrics_by_symbol: dict[str, dict[DailyRadarUniverseTrack, dict[str, Any]]] = {}
    for record in technical_records or ():
        symbol = _record_symbol(record)
        if symbol is None:
            continue
        ohlcv = _record_section(record, "ohlcv", parent="technical")
        indicators = _record_section(record, "indicators", parent="technical")
        metrics_by_symbol[symbol] = {
            "price_volume": _price_volume_metrics(ohlcv, indicators),
            "reversal": _reversal_metrics(ohlcv, indicators),
            "support_retake": _support_retake_metrics(ohlcv, indicators),
        }
    return metrics_by_symbol


def _price_volume_metrics(ohlcv: Mapping[str, Any], indicators: Mapping[str, Any]) -> dict[str, Any]:
    required = _missing_fields(
        ("ohlcv.close", _float(ohlcv.get("close"))),
        ("ohlcv.previous_close", _float(ohlcv.get("previous_close"))),
        ("indicators.volume_ratio", _float(indicators.get("volume_ratio"))),
        ("indicators.ma5", _float(indicators.get("ma5"))),
        ("indicators.ma20", _float(indicators.get("ma20"))),
    )
    if required:
        return _missing_metrics("price_volume", required)
    close = float(ohlcv["close"])
    previous_close = float(ohlcv["previous_close"])
    volume_ratio = float(indicators["volume_ratio"])
    ma5 = float(indicators["ma5"])
    ma20 = float(indicators["ma20"])
    score = 0.0
    reasons: list[str] = []
    if close > previous_close:
        score += 20
        reasons.append("close_above_previous")
    if volume_ratio >= 1.5:
        score += 35
        reasons.append("volume_expansion")
    elif volume_ratio >= 1.25:
        score += 22
        reasons.append("volume_constructive")
    if close > ma20:
        score += 20
        reasons.append("close_above_ma20")
    if ma5 >= ma20:
        score += 15
        reasons.append("ma5_above_ma20")
    return _technical_metrics(
        score=score,
        matched=score >= 55,
        reasons=reasons,
        close=close,
        previous_close=previous_close,
        volume_ratio=volume_ratio,
        ma5=ma5,
        ma20=ma20,
    )


def _reversal_metrics(ohlcv: Mapping[str, Any], indicators: Mapping[str, Any]) -> dict[str, Any]:
    required = _missing_fields(
        ("ohlcv.close", _float(ohlcv.get("close"))),
        ("ohlcv.previous_close", _float(ohlcv.get("previous_close"))),
        ("ohlcv.low", _float(ohlcv.get("low"))),
        ("indicators.support_level", _float(indicators.get("support_level"))),
        ("indicators.kd_k", _float(indicators.get("kd_k"))),
        ("indicators.kd_d", _float(indicators.get("kd_d"))),
        ("indicators.rsi14", _float(indicators.get("rsi14"))),
        ("indicators.macd_histogram", _float(indicators.get("macd_histogram"))),
    )
    if required:
        return _missing_metrics("reversal", required)
    close = float(ohlcv["close"])
    previous_close = float(ohlcv["previous_close"])
    low = float(ohlcv["low"])
    support = float(indicators["support_level"])
    kd_k = float(indicators["kd_k"])
    kd_d = float(indicators["kd_d"])
    rsi14 = float(indicators["rsi14"])
    macd_histogram = float(indicators["macd_histogram"])
    score = 0.0
    reasons: list[str] = []
    if close > previous_close:
        score += 20
        reasons.append("close_recovery")
    if support and low <= support * 1.03:
        score += 20
        reasons.append("low_near_support")
    if kd_k > kd_d and kd_k <= 45:
        score += 25
        reasons.append("kd_low_turn")
    if 35 <= rsi14 <= 60:
        score += 15
        reasons.append("rsi_recovery_zone")
    if macd_histogram >= -0.15:
        score += 15
        reasons.append("macd_stabilizing")
    return _technical_metrics(
        score=score,
        matched=score >= 55,
        reasons=reasons,
        close=close,
        previous_close=previous_close,
        low=low,
        support_level=support,
        kd_k=kd_k,
        kd_d=kd_d,
        rsi14=rsi14,
        macd_histogram=macd_histogram,
    )


def _support_retake_metrics(ohlcv: Mapping[str, Any], indicators: Mapping[str, Any]) -> dict[str, Any]:
    required = _missing_fields(
        ("ohlcv.close", _float(ohlcv.get("close"))),
        ("ohlcv.previous_close", _float(ohlcv.get("previous_close"))),
        ("indicators.support_level", _float(indicators.get("support_level"))),
        ("indicators.ma20", _float(indicators.get("ma20"))),
        ("indicators.volume_ratio", _float(indicators.get("volume_ratio"))),
    )
    if required:
        return _missing_metrics("support_retake", required)
    close = float(ohlcv["close"])
    previous_close = float(ohlcv["previous_close"])
    support = float(indicators["support_level"])
    ma20 = float(indicators["ma20"])
    volume_ratio = float(indicators["volume_ratio"])
    score = 0.0
    reasons: list[str] = []
    if support and previous_close < support <= close:
        score += 35
        reasons.append("support_reclaimed")
    elif support and close >= support and close > previous_close:
        score += 18
        reasons.append("support_held")
    if previous_close <= ma20 < close:
        score += 30
        reasons.append("ma20_reclaimed")
    elif close >= ma20:
        score += 12
        reasons.append("above_ma20")
    if 0.9 <= volume_ratio <= 1.8:
        score += 15
        reasons.append("orderly_volume")
    return _technical_metrics(
        score=score,
        matched=score >= 55,
        reasons=reasons,
        close=close,
        previous_close=previous_close,
        support_level=support,
        ma20=ma20,
        volume_ratio=volume_ratio,
    )


def _technical_metrics(*, score: float, matched: bool, reasons: list[str], **values: Any) -> dict[str, Any]:
    return {"score": round(score, 3), "matched": matched, "reasons": reasons, **values}


def _missing_metrics(track: DailyRadarUniverseTrack, missing_fields: list[str]) -> dict[str, Any]:
    return {
        "score": 0.0,
        "matched": False,
        "missing_data": True,
        "reason": "insufficient_technical_data",
        "missing_fields": missing_fields,
        "track": track,
    }


def _attach_missing_technical_trace(
    entries: list[DailyRadarUniverseEntry],
    metrics_by_symbol: Mapping[str, Mapping[DailyRadarUniverseTrack, dict[str, Any]]],
) -> None:
    for index, entry in enumerate(entries):
        track_metrics = dict(entry.track_metrics)
        symbol_metrics = metrics_by_symbol.get(entry.symbol)
        if symbol_metrics is None:
            continue
        for track in TECHNICAL_TRIGGER_TRACKS:
            if track in track_metrics:
                continue
            if track in symbol_metrics:
                track_metrics[track] = dict(symbol_metrics[track])
        entries[index] = replace(entry, track_metrics=track_metrics)


def _record_symbol(record: Any) -> str | None:
    value = _record_value(record, "symbol")
    if value is None:
        return None
    symbol = str(value).strip()
    return symbol or None


def _record_section(record: Any, key: str, *, parent: str) -> Mapping[str, Any]:
    direct = _record_value(record, key)
    if isinstance(direct, Mapping):
        return direct
    parent_value = _record_value(record, parent)
    if isinstance(parent_value, Mapping) and isinstance(parent_value.get(key), Mapping):
        return parent_value[key]
    return {}


def _record_value(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(key)
    return getattr(record, key, None)


def _missing_fields(*fields: tuple[str, float | None]) -> list[str]:
    return [name for name, value in fields if value is None]


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ordered_tracks(tracks: Iterable[DailyRadarUniverseTrack]) -> tuple[DailyRadarUniverseTrack, ...]:
    track_set = set(tracks)
    return tuple(track for track in TRACK_PRIORITY if track in track_set)


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
    "TECHNICAL_TRIGGER_TRACKS",
    "TechnicalTriggerRow",
    "refresh_daily_radar_universe_technical_tracks",
    "select_daily_radar_universe",
    "select_dual_track_universe",
]
