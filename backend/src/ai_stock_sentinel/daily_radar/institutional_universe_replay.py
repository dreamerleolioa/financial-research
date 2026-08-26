from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_archive_universe import (
    InstitutionalArchiveUniverseError,
    build_segmented_institutional_tracks,
)
from ai_stock_sentinel.daily_radar.institutional_flow import InstitutionalFlowRow
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    InstitutionalArchiveIntegrityError,
    get_complete_institutional_archive_window,
)
from ai_stock_sentinel.daily_radar.universe import (
    DailyRadarUniverseEntry,
    InstitutionalLeaderRow,
    SegmentedInstitutionalUniverseTrack,
    select_daily_radar_universe,
)


INSTITUTIONAL_UNIVERSE_REPLAY_VERSION = (
    "daily-radar-institutional-universe-replay-v1"
)
ARCHIVE_COMBINED_BASELINE_VERSION = "archive-combined-legacy-proxy-v1"


class _PreloadedSegmentedProvider:
    def __init__(
        self,
        tracks: Mapping[
            SegmentedInstitutionalUniverseTrack,
            Sequence[InstitutionalLeaderRow],
        ],
    ) -> None:
        self._tracks = tracks

    def institutional_track_leaders(
        self,
        *,
        track: SegmentedInstitutionalUniverseTrack,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        del run_date, market
        return self._tracks[track][: max(0, limit)]


class _ArchiveCombinedBaselineProvider:
    def __init__(
        self,
        *,
        same_day: Sequence[InstitutionalLeaderRow],
        recent: Sequence[InstitutionalLeaderRow],
    ) -> None:
        self._same_day = same_day
        self._recent = recent

    def same_day_institutional_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        del run_date, market
        return self._same_day[: max(0, limit)]

    def recent_accumulation_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        del run_date, market
        return self._recent[: max(0, limit)]


def build_institutional_universe_replay_report(
    session: Session,
    *,
    run_date: date,
    market: str = "TW",
    track_limit: int = 50,
    max_symbols: int = 250,
    recent_market_days: int = 5,
    recent_calendar_window_days: int = 10,
) -> dict[str, Any]:
    if market.upper() != "TW":
        raise InstitutionalArchiveUniverseError(
            "institutional_archive_universe_market_unsupported",
            market=market,
            query_date=run_date,
        )
    try:
        archive_window = get_complete_institutional_archive_window(
            session,
            start_date=run_date - timedelta(days=max(1, recent_calendar_window_days)),
            end_date=run_date,
        )
    except InstitutionalArchiveIntegrityError as exc:
        raise InstitutionalArchiveUniverseError(
            exc.code,
            market=exc.market,
            query_date=exc.trade_date,
        ) from exc
    if run_date not in archive_window:
        raise InstitutionalArchiveUniverseError(
            "institutional_archive_current_date_unavailable",
            market="TW",
            query_date=run_date,
        )

    selected_dates = tuple(
        sorted(archive_window)[-max(2, recent_market_days) :]
    )
    rows_by_date = {
        trade_date: archive_window[trade_date]
        for trade_date in selected_dates
    }
    segmented_tracks = build_segmented_institutional_tracks(
        rows_by_date,
        run_date=run_date,
        recent_market_days=recent_market_days,
    )
    segmented_universe = select_daily_radar_universe(
        _PreloadedSegmentedProvider(segmented_tracks),
        run_date,
        market=market,
        track_limit=track_limit,
    )[: max(0, max_symbols)]

    baseline_provider = _ArchiveCombinedBaselineProvider(
        same_day=_rank_archive_combined_same_day(rows_by_date[run_date]),
        recent=_rank_archive_combined_recent(rows_by_date),
    )
    baseline_universe = select_daily_radar_universe(
        baseline_provider,
        run_date,
        market=market,
        track_limit=track_limit,
    )[: max(0, max_symbols)]
    return _comparison_report(
        run_date=run_date,
        market=market,
        source_dates=selected_dates,
        segmented=segmented_universe,
        baseline=baseline_universe,
        track_limit=track_limit,
        max_symbols=max_symbols,
    )


def _rank_archive_combined_same_day(
    rows: Sequence[InstitutionalFlowRow],
) -> list[InstitutionalLeaderRow]:
    ranked: list[tuple[str, str, int]] = []
    for row in rows:
        actor_values = (
            ("foreign", row.foreign_net_shares),
            ("trust", row.investment_trust_net_shares),
        )
        actor, net_buy = min(
            actor_values,
            key=lambda item: (-item[1], 0 if item[0] == "foreign" else 1),
        )
        if net_buy > 0:
            ranked.append((row.symbol, actor, net_buy))
    ranked.sort(
        key=lambda item: (
            -item[2],
            0 if item[1] == "foreign" else 1,
            item[0],
        )
    )
    return [
        InstitutionalLeaderRow(
            symbol=symbol,
            rank=index,
            score=float(net_buy),
            actor=actor,
            net_buy=float(net_buy),
            flow_state="same_day_net_buy",
            bucket_hints=("same_day_institutional",),
        )
        for index, (symbol, actor, net_buy) in enumerate(ranked, start=1)
    ]


def _rank_archive_combined_recent(
    rows_by_date: Mapping[date, Sequence[InstitutionalFlowRow]],
) -> list[InstitutionalLeaderRow]:
    ordered_dates = tuple(sorted(rows_by_date))
    daily_net_by_symbol: dict[str, dict[date, int]] = defaultdict(dict)
    for trade_date in ordered_dates:
        for row in rows_by_date[trade_date]:
            daily_net_by_symbol[row.symbol][trade_date] = (
                row.foreign_net_shares + row.investment_trust_net_shares
            )

    ranked: list[tuple[str, int, int]] = []
    for symbol, values_by_date in daily_net_by_symbol.items():
        values = [values_by_date.get(trade_date, 0) for trade_date in ordered_dates]
        cumulative_net_buy = sum(values)
        consecutive_buy_days = _maximum_positive_streak(
            values,
            ordered_dates=ordered_dates,
        )
        if cumulative_net_buy <= 0 or consecutive_buy_days <= 0:
            continue
        ranked.append((symbol, consecutive_buy_days, cumulative_net_buy))
    ranked.sort(key=lambda item: (-item[1], -item[2], item[0]))
    source_dates = tuple(trade_date.isoformat() for trade_date in ordered_dates)
    return [
        InstitutionalLeaderRow(
            symbol=symbol,
            rank=index,
            score=float(cumulative_net_buy),
            actor="institutional",
            cumulative_net_buy=float(cumulative_net_buy),
            consecutive_buy_days=consecutive_buy_days,
            source_dates=source_dates,
            flow_state=(
                "consistent_accumulation"
                if consecutive_buy_days >= 2
                else "weak_confirmation"
            ),
            bucket_hints=("recent_accumulation",),
        )
        for index, (symbol, consecutive_buy_days, cumulative_net_buy) in enumerate(
            ranked,
            start=1,
        )
    ]


def _maximum_positive_streak(
    values: Sequence[int],
    *,
    ordered_dates: Sequence[date],
) -> int:
    if len(values) != len(ordered_dates):
        raise ValueError("values and ordered_dates must have the same length")
    maximum = 0
    current = 0
    previous_date: date | None = None
    for value, current_date in zip(values, ordered_dates, strict=True):
        if previous_date is not None and _next_weekday(previous_date) != current_date:
            current = 0
        if value > 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
        previous_date = current_date
    return maximum


def _next_weekday(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _dates_are_weekday_contiguous(values: Sequence[date]) -> bool:
    return all(
        _next_weekday(previous) == current
        for previous, current in zip(values, values[1:], strict=False)
    )


def _comparison_report(
    *,
    run_date: date,
    market: str,
    source_dates: Sequence[date],
    segmented: Sequence[DailyRadarUniverseEntry],
    baseline: Sequence[DailyRadarUniverseEntry],
    track_limit: int,
    max_symbols: int,
) -> dict[str, Any]:
    weekday_contiguous = _dates_are_weekday_contiguous(source_dates[-5:])
    history_complete = len(source_dates) >= 5 and weekday_contiguous
    blocking_reasons = []
    if len(source_dates) < 5:
        blocking_reasons.append("insufficient_complete_market_days")
    if not weekday_contiguous:
        blocking_reasons.append("archive_weekday_gap")
    segmented_ranks = {entry.symbol: entry.rank for entry in segmented}
    baseline_ranks = {entry.symbol: entry.rank for entry in baseline}
    segmented_symbols = set(segmented_ranks)
    baseline_symbols = set(baseline_ranks)
    shared_symbols = segmented_symbols & baseline_symbols
    union_symbols = segmented_symbols | baseline_symbols
    rank_changes = [
        {
            "symbol": symbol,
            "segmented_rank": segmented_ranks[symbol],
            "baseline_rank": baseline_ranks[symbol],
            "rank_improvement": baseline_ranks[symbol] - segmented_ranks[symbol],
        }
        for symbol in shared_symbols
    ]
    rank_changes.sort(
        key=lambda item: (
            -abs(int(item["rank_improvement"])),
            str(item["symbol"]),
        )
    )
    return {
        "report_version": INSTITUTIONAL_UNIVERSE_REPLAY_VERSION,
        "run_date": run_date.isoformat(),
        "market": market.upper(),
        "source": {
            "provider": "taiwan_institutional_flow_archive",
            "source_dates": [trade_date.isoformat() for trade_date in source_dates],
            "complete_market_days": len(source_dates),
            "weekday_contiguous": weekday_contiguous,
        },
        "parameters": {
            "track_limit_per_track": track_limit,
            "max_symbols": max_symbols,
            "segmented_institutional_track_count": 4,
            "baseline_institutional_track_count": 2,
        },
        "segmented": _universe_summary(segmented),
        "baseline": {
            "version": ARCHIVE_COMBINED_BASELINE_VERSION,
            "limitations": [
                "proxy_uses_archive_foreign_plus_trust_without_legacy_report_volume_concentration",
                "proxy_is_not_an_exact_reconstruction_of_twt38u_or_twt44u",
            ],
            **_universe_summary(baseline),
        },
        "comparison": {
            "scope": "single_run_membership_and_rank_only",
            "interpretation_limits": [
                "single_run_membership_comparison_is_not_forward_performance_evidence",
                "segmented_four_track_capacity_differs_from_baseline_two_track_capacity",
            ],
            "overlap_count": len(shared_symbols),
            "union_count": len(union_symbols),
            "jaccard_ratio": (
                round(len(shared_symbols) / len(union_symbols), 6)
                if union_symbols
                else 1.0
            ),
            "segmented_only_symbols": sorted(segmented_symbols - baseline_symbols),
            "baseline_only_symbols": sorted(baseline_symbols - segmented_symbols),
            "shared_rank_changes": rank_changes,
        },
        "calibration_gate": {
            "minimum_complete_market_days": 5,
            "history_complete": history_complete,
            "ready_for_human_review": history_complete,
            "auto_apply_scoring_change": False,
            "blocking_reasons": blocking_reasons,
        },
        "human_approval_boundary": {
            "automated_report": True,
            "updates_live_universe_or_scoring": False,
            "requires_human_approved_versioned_change": True,
        },
    }


def _universe_summary(
    universe: Sequence[DailyRadarUniverseEntry],
) -> dict[str, Any]:
    primary_tracks = Counter(entry.primary_track for entry in universe)
    track_memberships = Counter(
        track
        for entry in universe
        for track in entry.tracks
    )
    return {
        "universe_count": len(universe),
        "symbols": [entry.symbol for entry in universe],
        "primary_track_counts": dict(sorted(primary_tracks.items())),
        "track_membership_counts": dict(sorted(track_memberships.items())),
    }


__all__ = [
    "ARCHIVE_COMBINED_BASELINE_VERSION",
    "INSTITUTIONAL_UNIVERSE_REPLAY_VERSION",
    "build_institutional_universe_replay_report",
]
