from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.name_backfill import backfill_daily_radar_symbol_names
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun, StockRawData
from ai_stock_sentinel.db.session import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[DailyRadarRun.__table__, DailyRadarCandidate.__table__, StockRawData.__table__],
    )
    with Session(engine) as session:
        yield session


def test_backfill_updates_candidate_and_raw_cache_names(db_session: Session) -> None:
    run = _daily_radar_run(db_session)
    candidate = _daily_radar_candidate(db_session, run, symbol="2330.TW", name="2330.TW")
    raw_row = _raw_data(db_session, symbol="2330.TW", name="2330.TW")
    preserved_candidate = _daily_radar_candidate(db_session, run, symbol="2454.TW", name="聯發科")
    preserved_raw_row = _raw_data(db_session, symbol="2454.TW", name="聯發科")
    db_session.commit()

    result = backfill_daily_radar_symbol_names(
        db_session,
        name_resolver=lambda symbol: {"2330.TW": "台積電", "2454.TW": "聯發科"}.get(symbol),
    )
    db_session.commit()

    assert result.scanned == 1
    assert result.updated_candidates == 1
    assert result.updated_raw_rows == 1
    assert result.unresolved_symbols == []
    assert candidate.name == "台積電"
    assert raw_row.technical["name"] == "台積電"
    assert preserved_candidate.name == "聯發科"
    assert preserved_raw_row.technical["name"] == "聯發科"


def test_backfill_reports_unresolved_symbols_without_mutation(db_session: Session) -> None:
    run = _daily_radar_run(db_session)
    candidate = _daily_radar_candidate(db_session, run, symbol="9999.TW", name="9999.TW")
    raw_row = _raw_data(db_session, symbol="9999.TW", name="9999.TW")
    db_session.commit()

    result = backfill_daily_radar_symbol_names(db_session, name_resolver=lambda _symbol: None)

    assert result.scanned == 1
    assert result.updated_candidates == 0
    assert result.updated_raw_rows == 0
    assert result.unresolved_symbols == ["9999.TW"]
    assert candidate.name == "9999.TW"
    assert raw_row.technical["name"] == "9999.TW"


def test_backfill_dry_run_counts_updates_without_writing(db_session: Session) -> None:
    run = _daily_radar_run(db_session)
    candidate = _daily_radar_candidate(db_session, run, symbol="2330.TW", name="")
    raw_row = _raw_data(db_session, symbol="2330.TW", name="")
    db_session.commit()

    result = backfill_daily_radar_symbol_names(
        db_session,
        dry_run=True,
        name_resolver=lambda _symbol: "台積電",
    )

    assert result.scanned == 1
    assert result.updated_candidates == 1
    assert result.updated_raw_rows == 1
    assert candidate.name == ""
    assert raw_row.technical["name"] == ""


def _daily_radar_run(session: Session) -> DailyRadarRun:
    run = DailyRadarRun(
        run_date=date(2026, 6, 16),
        market="TW",
        status="completed",
        started_at=datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 16, 1, 5, tzinfo=timezone.utc),
        universe_count=2,
        prefilter_count=2,
        candidate_count=0,
        errors=[],
        created_at=datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def _daily_radar_candidate(
    session: Session,
    run: DailyRadarRun,
    *,
    symbol: str,
    name: str,
) -> DailyRadarCandidate:
    candidate = DailyRadarCandidate(
        run_id=run.id,
        symbol=symbol,
        name=name,
        primary_bucket="institutional_accumulation",
        secondary_buckets=[],
        observation_score=80,
        bucket_scores={},
        risk_labels=[],
        matched_rules=[],
        explanation="fixture",
        repeat_status="new",
        score_breakdown={},
        input_snapshot={"symbol": symbol},
        data_dates={"ohlcv": run.run_date.isoformat()},
    )
    session.add(candidate)
    session.flush()
    run.candidate_count = len(run.candidates)
    return candidate


def _raw_data(session: Session, *, symbol: str, name: str) -> StockRawData:
    row = StockRawData(
        symbol=symbol,
        record_date=date(2026, 6, 16),
        technical=_technical_payload(symbol, name=name),
        institutional={},
        fundamental={},
        raw_data_is_final=True,
    )
    session.add(row)
    session.flush()
    return row


def _technical_payload(symbol: str, *, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "ohlcv": {"close": 100.0},
        "indicators": {},
        "data_dates": {"ohlcv": "2026-06-16"},
        "symbol": symbol,
    }
