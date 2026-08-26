from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_archive_universe import (
    InstitutionalArchiveUniverseError,
)
from ai_stock_sentinel.daily_radar.institutional_flow import (
    InstitutionalFlowRow,
    InstitutionalReport,
)
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    archive_institutional_report,
)
from ai_stock_sentinel.daily_radar.institutional_universe_replay import (
    ARCHIVE_COMBINED_BASELINE_VERSION,
    INSTITUTIONAL_UNIVERSE_REPLAY_VERSION,
    build_institutional_universe_replay_report,
)
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
) -> None:
    for market, rows in (
        ("TW", tw_rows),
        (
            "TWO",
            (_row("6488.TWO", trade_date=trade_date),),
        ),
    ):
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


def test_institutional_universe_replay_compares_segmented_and_combined_proxy() -> None:
    session, engine = _db_session()
    start_date = date(2026, 6, 1)
    dates = tuple(start_date + timedelta(days=offset) for offset in range(5))
    try:
        for index, trade_date in enumerate(dates):
            _archive_day(
                session,
                trade_date,
                tw_rows=(
                    _row(
                        "2330.TW",
                        trade_date=trade_date,
                        foreign=100,
                        trust=-200,
                    ),
                    _row(
                        "2454.TW",
                        trade_date=trade_date,
                        foreign=80 if index == 4 else 0,
                        trust=200 if index == 4 else 0,
                    ),
                    _row(
                        "2303.TW",
                        trade_date=trade_date,
                        foreign=100 if index < 4 else -50,
                    ),
                ),
            )
        snapshot_count = session.query(TaiwanInstitutionalReportSnapshot).count()
        flow_count = session.query(TaiwanInstitutionalFlow).count()

        report = build_institutional_universe_replay_report(
            session,
            run_date=dates[-1],
            track_limit=1,
            max_symbols=250,
        )

        assert report["report_version"] == INSTITUTIONAL_UNIVERSE_REPLAY_VERSION
        assert report["baseline"]["version"] == ARCHIVE_COMBINED_BASELINE_VERSION
        assert report["parameters"] == {
            "track_limit_per_track": 1,
            "max_symbols": 250,
            "segmented_institutional_track_count": 4,
            "baseline_institutional_track_count": 2,
        }
        assert report["source"]["source_dates"] == [
            trade_date.isoformat() for trade_date in dates
        ]
        assert report["calibration_gate"] == {
            "minimum_complete_market_days": 5,
            "history_complete": True,
            "ready_for_human_review": True,
            "auto_apply_scoring_change": False,
            "blocking_reasons": [],
        }
        assert report["human_approval_boundary"][
            "updates_live_universe_or_scoring"
        ] is False
        assert report["comparison"]["segmented_only_symbols"] == ["2330.TW"]
        assert report["comparison"]["baseline_only_symbols"] == ["2303.TW"]
        assert report["comparison"]["scope"] == (
            "single_run_membership_and_rank_only"
        )
        assert (
            "single_run_membership_comparison_is_not_forward_performance_evidence"
            in report["comparison"]["interpretation_limits"]
        )
        assert "2454.TW" in {
            row["symbol"]
            for row in report["comparison"]["shared_rank_changes"]
        }
        assert report["segmented"]["track_membership_counts"] == {
            "foreign_recent_accumulation": 1,
            "foreign_same_day": 1,
            "trust_same_day": 1,
        }
        assert "proxy_is_not_an_exact_reconstruction_of_twt38u_or_twt44u" in (
            report["baseline"]["limitations"]
        )
        assert session.query(TaiwanInstitutionalReportSnapshot).count() == snapshot_count
        assert session.query(TaiwanInstitutionalFlow).count() == flow_count
    finally:
        session.close()
        engine.dispose()


def test_institutional_universe_replay_marks_short_history_not_ready() -> None:
    session, engine = _db_session()
    dates = (date(2026, 6, 4), date(2026, 6, 5))
    try:
        for trade_date in dates:
            _archive_day(
                session,
                trade_date,
                tw_rows=(_row("2330.TW", trade_date=trade_date, foreign=100),),
            )

        report = build_institutional_universe_replay_report(
            session,
            run_date=dates[-1],
        )

        assert report["source"]["complete_market_days"] == 2
        assert report["calibration_gate"]["history_complete"] is False
        assert report["calibration_gate"]["ready_for_human_review"] is False
        assert report["calibration_gate"]["auto_apply_scoring_change"] is False
        assert report["calibration_gate"]["blocking_reasons"] == [
            "insufficient_complete_market_days"
        ]
    finally:
        session.close()
        engine.dispose()


def test_institutional_universe_replay_does_not_bridge_missing_weekday() -> None:
    session, engine = _db_session()
    dates = (
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 4),
        date(2026, 6, 5),
        date(2026, 6, 8),
    )
    try:
        for trade_date in dates:
            _archive_day(
                session,
                trade_date,
                tw_rows=(_row("2330.TW", trade_date=trade_date, foreign=100),),
            )

        report = build_institutional_universe_replay_report(
            session,
            run_date=dates[-1],
        )

        assert report["source"]["complete_market_days"] == 5
        assert report["source"]["weekday_contiguous"] is False
        assert report["calibration_gate"]["history_complete"] is False
        assert report["calibration_gate"]["ready_for_human_review"] is False
        assert report["calibration_gate"]["blocking_reasons"] == [
            "archive_weekday_gap"
        ]
        assert report["baseline"]["track_membership_counts"] == {
            "recent_accumulation": 1,
            "same_day_institutional": 1,
        }
    finally:
        session.close()
        engine.dispose()


def test_institutional_universe_replay_requires_complete_current_archive() -> None:
    session, engine = _db_session()
    try:
        with pytest.raises(
            InstitutionalArchiveUniverseError,
            match="institutional_archive_current_date_unavailable",
        ):
            build_institutional_universe_replay_report(
                session,
                run_date=date(2026, 6, 5),
            )
    finally:
        session.close()
        engine.dispose()
