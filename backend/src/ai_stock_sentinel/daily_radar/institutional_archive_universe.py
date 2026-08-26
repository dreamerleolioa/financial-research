from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_flow import InstitutionalFlowRow
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    InstitutionalArchiveIntegrityError,
    get_complete_institutional_archive_window,
)
from ai_stock_sentinel.daily_radar.universe import (
    InstitutionalLeaderRow,
    SegmentedInstitutionalUniverseTrack,
)


class InstitutionalArchiveUniverseError(RuntimeError):
    def __init__(self, code: str, *, market: str, query_date: date) -> None:
        super().__init__(code)
        self.code = code
        self.market = market
        self.query_date = query_date


class ArchivedInstitutionalUniverseProvider:
    name = "institutional_archive"

    def __init__(
        self,
        session: Session,
        *,
        recent_market_days: int = 5,
        recent_calendar_window_days: int = 10,
        minimum_consecutive_buy_days: int = 2,
    ) -> None:
        self._session = session
        self._recent_market_days = max(2, recent_market_days)
        self._recent_calendar_window_days = max(1, recent_calendar_window_days)
        self._minimum_consecutive_buy_days = max(2, minimum_consecutive_buy_days)
        self._tracks_by_context: dict[
            tuple[date, str],
            dict[SegmentedInstitutionalUniverseTrack, tuple[InstitutionalLeaderRow, ...]],
        ] = {}

    def institutional_track_leaders(
        self,
        *,
        track: SegmentedInstitutionalUniverseTrack,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        if market.upper() != "TW":
            raise InstitutionalArchiveUniverseError(
                "institutional_archive_universe_market_unsupported",
                market=market,
                query_date=run_date,
            )
        context_key = (run_date, market.upper())
        tracks = self._tracks_by_context.get(context_key)
        if tracks is None:
            tracks = self._build_tracks(run_date=run_date)
            self._tracks_by_context[context_key] = tracks
        return tracks[track][: max(0, limit)]

    def _build_tracks(
        self,
        *,
        run_date: date,
    ) -> dict[SegmentedInstitutionalUniverseTrack, tuple[InstitutionalLeaderRow, ...]]:
        try:
            archive_window = get_complete_institutional_archive_window(
                self._session,
                start_date=run_date - timedelta(days=self._recent_calendar_window_days),
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
        return build_segmented_institutional_tracks(
            archive_window,
            run_date=run_date,
            recent_market_days=self._recent_market_days,
            minimum_consecutive_buy_days=self._minimum_consecutive_buy_days,
        )


def build_segmented_institutional_tracks(
    archive_window: Mapping[date, Sequence[InstitutionalFlowRow]],
    *,
    run_date: date,
    recent_market_days: int = 5,
    minimum_consecutive_buy_days: int = 2,
) -> dict[
    SegmentedInstitutionalUniverseTrack,
    tuple[InstitutionalLeaderRow, ...],
]:
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
    return {
        "foreign_same_day": tuple(
            _rank_same_day_actor(
                rows_by_date[run_date],
                actor="foreign",
                track="foreign_same_day",
            )
        ),
        "trust_same_day": tuple(
            _rank_same_day_actor(
                rows_by_date[run_date],
                actor="trust",
                track="trust_same_day",
            )
        ),
        "foreign_recent_accumulation": tuple(
            _rank_recent_actor(
                rows_by_date,
                actor="foreign",
                track="foreign_recent_accumulation",
                minimum_consecutive_buy_days=max(
                    2,
                    minimum_consecutive_buy_days,
                ),
            )
        ),
        "trust_recent_accumulation": tuple(
            _rank_recent_actor(
                rows_by_date,
                actor="trust",
                track="trust_recent_accumulation",
                minimum_consecutive_buy_days=max(
                    2,
                    minimum_consecutive_buy_days,
                ),
            )
        ),
    }


def _rank_same_day_actor(
    rows: Sequence[InstitutionalFlowRow],
    *,
    actor: str,
    track: SegmentedInstitutionalUniverseTrack,
) -> list[InstitutionalLeaderRow]:
    scored = [
        (row, _actor_net_shares(row, actor))
        for row in rows
        if _actor_net_shares(row, actor) > 0
    ]
    scored.sort(key=lambda item: (-item[1], item[0].symbol))
    return [
        InstitutionalLeaderRow(
            symbol=row.symbol,
            rank=index,
            score=float(net_shares),
            actor=actor,
            net_buy=float(net_shares),
            source_dates=(row.trade_date.isoformat(),),
            flow_state="same_day_net_buy",
            bucket_hints=(track,),
        )
        for index, (row, net_shares) in enumerate(scored, start=1)
    ]


def _rank_recent_actor(
    rows_by_date: Mapping[date, Sequence[InstitutionalFlowRow]],
    *,
    actor: str,
    track: SegmentedInstitutionalUniverseTrack,
    minimum_consecutive_buy_days: int,
) -> list[InstitutionalLeaderRow]:
    ordered_dates = tuple(sorted(rows_by_date))
    if len(ordered_dates) < minimum_consecutive_buy_days:
        return []
    daily_nets_by_symbol: dict[str, dict[date, int]] = defaultdict(dict)
    for trade_date in ordered_dates:
        for row in rows_by_date[trade_date]:
            daily_nets_by_symbol[row.symbol][trade_date] = _actor_net_shares(row, actor)

    scored: list[tuple[str, int, int, int]] = []
    for symbol, nets_by_date in daily_nets_by_symbol.items():
        ordered_nets = [nets_by_date.get(trade_date, 0) for trade_date in ordered_dates]
        consecutive_buy_days = _trailing_positive_days(
            ordered_nets,
            ordered_dates=ordered_dates,
        )
        cumulative_net_buy = sum(ordered_nets)
        if (
            consecutive_buy_days < minimum_consecutive_buy_days
            or cumulative_net_buy <= 0
        ):
            continue
        scored.append(
            (
                symbol,
                consecutive_buy_days,
                cumulative_net_buy,
                ordered_nets[-1],
            )
        )
    scored.sort(key=lambda item: (-item[1], -item[2], item[0]))
    source_dates = tuple(trade_date.isoformat() for trade_date in ordered_dates)
    return [
        InstitutionalLeaderRow(
            symbol=symbol,
            rank=index,
            score=float(cumulative_net_buy),
            actor=actor,
            net_buy=float(latest_net_buy),
            cumulative_net_buy=float(cumulative_net_buy),
            consecutive_buy_days=consecutive_buy_days,
            source_dates=source_dates,
            flow_state="consistent_accumulation",
            bucket_hints=(track,),
        )
        for index, (
            symbol,
            consecutive_buy_days,
            cumulative_net_buy,
            latest_net_buy,
        ) in enumerate(scored, start=1)
    ]


def _actor_net_shares(row: InstitutionalFlowRow, actor: str) -> int:
    if actor == "foreign":
        return row.foreign_net_shares
    if actor == "trust":
        return row.investment_trust_net_shares
    raise ValueError("unsupported institutional actor")


def _trailing_positive_days(
    values: Sequence[int],
    *,
    ordered_dates: Sequence[date],
) -> int:
    if len(values) != len(ordered_dates):
        raise ValueError("values and ordered_dates must have the same length")
    count = 0
    next_date: date | None = None
    for index in range(len(values) - 1, -1, -1):
        value = values[index]
        if value <= 0:
            break
        current_date = ordered_dates[index]
        if next_date is not None and _next_weekday(current_date) != next_date:
            break
        count += 1
        next_date = current_date
    return count


def _next_weekday(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


__all__ = [
    "ArchivedInstitutionalUniverseProvider",
    "InstitutionalArchiveUniverseError",
    "build_segmented_institutional_tracks",
]
