from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_archive_universe import (
    ArchivedInstitutionalUniverseProvider,
    InstitutionalArchiveUniverseError,
)
from ai_stock_sentinel.daily_radar.institutional_flow import (
    InstitutionalFlowRow,
    InstitutionalReport,
)
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    archive_institutional_report,
)
from ai_stock_sentinel.daily_radar.universe import select_daily_radar_universe
from ai_stock_sentinel.db.models import (
    TaiwanInstitutionalFlow,
    TaiwanInstitutionalReportSnapshot,
)
from ai_stock_sentinel.db.session import Base


def _db_session() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            TaiwanInstitutionalReportSnapshot.__table__,
            TaiwanInstitutionalFlow.__table__,
        ],
    )
    return Session(engine), engine


def _row(
    symbol: str,
    *,
    trade_date: date,
    foreign: int = 0,
    trust: int = 0,
) -> InstitutionalFlowRow:
    market = "TWO" if symbol.endswith(".TWO") else "TW"
    return InstitutionalFlowRow(
        symbol=symbol,
        market=market,
        name=symbol,
        trade_date=trade_date,
        foreign_net_shares=foreign,
        investment_trust_net_shares=trust,
        dealer_net_shares=0,
        total_net_shares=foreign + trust,
    )


def _archive_day(
    session: Session,
    trade_date: date,
    *,
    tw_rows: tuple[InstitutionalFlowRow, ...],
    two_rows: tuple[InstitutionalFlowRow, ...],
) -> None:
    for market, rows in (("TW", tw_rows), ("TWO", two_rows)):
        archive_institutional_report(
            session,
            InstitutionalReport(
                market=market,
                trade_date=trade_date,
                source_provider="fixture",
                source_dataset=f"fixture_{market}",
                rows=rows,
            ),
        )
    session.commit()


def test_archive_provider_builds_four_separate_tracks_across_tw_and_two() -> None:
    session, engine = _db_session()
    dates = (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3))
    try:
        for index, trade_date in enumerate(dates):
            _archive_day(
                session,
                trade_date,
                tw_rows=(
                    _row(
                        "2330.TW",
                        trade_date=trade_date,
                        foreign=(100, 200, 300)[index],
                        trust=(40, 50, 60)[index],
                    ),
                    _row(
                        "2454.TW",
                        trade_date=trade_date,
                        trust=(50, 80, 90)[index],
                    ),
                ),
                two_rows=(
                    _row(
                        "6488.TWO",
                        trade_date=trade_date,
                        foreign=(10, -5, 500)[index],
                    ),
                    _row(
                        "8299.TWO",
                        trade_date=trade_date,
                        trust=(20, 30, 40)[index],
                    ),
                ),
            )

        provider = ArchivedInstitutionalUniverseProvider(session)
        universe = select_daily_radar_universe(
            provider,
            dates[-1],
            market="TW",
            track_limit=50,
        )

        by_symbol = {entry.symbol: entry for entry in universe}
        assert set(by_symbol) == {"2330.TW", "2454.TW", "6488.TWO", "8299.TWO"}
        assert by_symbol["2330.TW"].tracks == (
            "foreign_same_day",
            "trust_same_day",
            "foreign_recent_accumulation",
            "trust_recent_accumulation",
        )
        assert by_symbol["2330.TW"].track_metrics["foreign_same_day"] == {
            "rank": 2,
            "score": 300.0,
            "actor": "foreign",
            "net_buy": 300.0,
            "flow_state": "same_day_net_buy",
            "source_dates": ["2026-06-03"],
            "bucket_hints": ["foreign_same_day"],
        }
        assert (
            by_symbol["2330.TW"]
            .track_metrics["foreign_recent_accumulation"]["consecutive_buy_days"]
            == 3
        )
        assert (
            by_symbol["2330.TW"]
            .track_metrics["foreign_recent_accumulation"]["cumulative_net_buy"]
            == pytest.approx(600.0)
        )
        assert "foreign_recent_accumulation" not in by_symbol["6488.TWO"].tracks
        assert "trust_recent_accumulation" in by_symbol["8299.TWO"].tracks
    finally:
        session.close()
        engine.dispose()


def test_recent_track_requires_trailing_streak_not_historical_maximum() -> None:
    session, engine = _db_session()
    dates = (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3))
    try:
        for index, trade_date in enumerate(dates):
            _archive_day(
                session,
                trade_date,
                tw_rows=(
                    _row(
                        "2330.TW",
                        trade_date=trade_date,
                        foreign=(1000, 1000, -1)[index],
                    ),
                ),
                two_rows=(_row("6488.TWO", trade_date=trade_date),),
            )
        provider = ArchivedInstitutionalUniverseProvider(session)

        recent = provider.institutional_track_leaders(
            track="foreign_recent_accumulation",
            run_date=dates[-1],
            market="TW",
            limit=50,
        )

        assert recent == ()
    finally:
        session.close()
        engine.dispose()


def test_recent_track_does_not_bridge_missing_weekday_archive() -> None:
    session, engine = _db_session()
    dates = (date(2026, 6, 1), date(2026, 6, 3))
    try:
        for trade_date in dates:
            _archive_day(
                session,
                trade_date,
                tw_rows=(_row("2330.TW", trade_date=trade_date, foreign=100),),
                two_rows=(_row("6488.TWO", trade_date=trade_date),),
            )
        provider = ArchivedInstitutionalUniverseProvider(session)

        recent = provider.institutional_track_leaders(
            track="foreign_recent_accumulation",
            run_date=dates[-1],
            market="TW",
            limit=50,
        )

        assert recent == ()
    finally:
        session.close()
        engine.dispose()


def test_recent_track_allows_weekend_between_complete_archive_days() -> None:
    session, engine = _db_session()
    dates = (date(2026, 6, 5), date(2026, 6, 8))
    try:
        for trade_date in dates:
            _archive_day(
                session,
                trade_date,
                tw_rows=(_row("2330.TW", trade_date=trade_date, foreign=100),),
                two_rows=(_row("6488.TWO", trade_date=trade_date),),
            )
        provider = ArchivedInstitutionalUniverseProvider(session)

        recent = provider.institutional_track_leaders(
            track="foreign_recent_accumulation",
            run_date=dates[-1],
            market="TW",
            limit=50,
        )

        assert [row.symbol for row in recent] == ["2330.TW"]
        assert recent[0].consecutive_buy_days == 2
    finally:
        session.close()
        engine.dispose()


def test_recent_tracks_remain_empty_until_two_complete_archive_days_exist() -> None:
    session, engine = _db_session()
    trade_date = date(2026, 6, 3)
    try:
        _archive_day(
            session,
            trade_date,
            tw_rows=(_row("2330.TW", trade_date=trade_date, foreign=100),),
            two_rows=(_row("6488.TWO", trade_date=trade_date, trust=50),),
        )
        provider = ArchivedInstitutionalUniverseProvider(session)

        assert provider.institutional_track_leaders(
            track="foreign_recent_accumulation",
            run_date=trade_date,
            market="TW",
            limit=50,
        ) == ()
        assert [
            row.symbol
            for row in provider.institutional_track_leaders(
                track="foreign_same_day",
                run_date=trade_date,
                market="TW",
                limit=50,
            )
        ] == ["2330.TW"]
    finally:
        session.close()
        engine.dispose()


def test_archive_provider_fails_closed_when_current_date_is_incomplete() -> None:
    session, engine = _db_session()
    trade_date = date(2026, 6, 3)
    try:
        archive_institutional_report(
            session,
            InstitutionalReport(
                market="TW",
                trade_date=trade_date,
                source_provider="fixture",
                source_dataset="fixture_TW",
                rows=(_row("2330.TW", trade_date=trade_date, foreign=100),),
            ),
        )
        session.commit()
        provider = ArchivedInstitutionalUniverseProvider(session)

        with pytest.raises(
            InstitutionalArchiveUniverseError,
            match="institutional_archive_current_date_unavailable",
        ):
            provider.institutional_track_leaders(
                track="foreign_same_day",
                run_date=trade_date,
                market="TW",
                limit=50,
            )
    finally:
        session.close()
        engine.dispose()


def test_archive_provider_fails_closed_when_snapshot_payload_was_mutated() -> None:
    session, engine = _db_session()
    trade_date = date(2026, 6, 3)
    try:
        _archive_day(
            session,
            trade_date,
            tw_rows=(_row("2330.TW", trade_date=trade_date, foreign=100),),
            two_rows=(_row("6488.TWO", trade_date=trade_date, trust=50),),
        )
        flow = session.query(TaiwanInstitutionalFlow).filter_by(symbol="2330.TW").one()
        flow.foreign_net_shares = 999
        session.commit()
        provider = ArchivedInstitutionalUniverseProvider(session)

        with pytest.raises(
            InstitutionalArchiveUniverseError,
            match="institutional_archive_snapshot_payload_hash_mismatch",
        ):
            provider.institutional_track_leaders(
                track="foreign_same_day",
                run_date=trade_date,
                market="TW",
                limit=50,
            )
    finally:
        session.close()
        engine.dispose()
