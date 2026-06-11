from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun, StockRawData
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.daily_radar.repository import (
    create_daily_radar_run,
    get_daily_radar_run_by_date,
    get_final_raw_data_rows_for_date,
    get_final_raw_data_rows_for_symbols,
    get_latest_daily_radar_run,
    get_symbol_candidate_history,
    replace_run_candidates,
    update_daily_radar_run,
)


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


def _create_run(
    session: Session,
    *,
    run_date: date,
    status: str = "completed",
    created_at: datetime | None = None,
    candidate_count: int = 0,
) -> DailyRadarRun:
    run = DailyRadarRun(
        run_date=run_date,
        market="TW",
        status=status,
        started_at=created_at or datetime(2026, 6, 1, tzinfo=timezone.utc),
        finished_at=created_at if status == "completed" else None,
        universe_count=5,
        prefilter_count=3,
        candidate_count=candidate_count,
        errors=[] if status == "completed" else [{"code": "fixture_status"}],
        created_at=created_at or datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def _add_candidate(
    session: Session,
    run: DailyRadarRun,
    *,
    symbol: str = "2330.TW",
    score: int = 91,
) -> DailyRadarCandidate:
    candidate = DailyRadarCandidate(
        run_id=run.id,
        symbol=symbol,
        name=f"{symbol} fixture",
        primary_bucket="institutional_accumulation",
        secondary_buckets=["price_volume_strengthening"],
        observation_score=score,
        bucket_scores={"institutional_accumulation": score},
        risk_labels=["data_gap"] if score < 80 else [],
        matched_rules=["fixture_rule"],
        explanation="Rule-based observation summary for persistence tests.",
        repeat_status="new",
        score_breakdown={"base": score, "risk_penalty": 0},
        input_snapshot={"symbol": symbol, "close": 980},
        data_dates={"ohlcv": "2026-06-01"},
    )
    session.add(candidate)
    session.flush()
    return candidate


def _add_raw_data(
    session: Session,
    *,
    symbol: str,
    record_date: date,
    is_final: bool,
) -> StockRawData:
    row = StockRawData(
        symbol=symbol,
        record_date=record_date,
        technical={"name": symbol, "ohlcv": {}, "indicators": {}},
        institutional={"institutional_flow": {}},
        fundamental={"margin": {}},
        raw_data_is_final=is_final,
    )
    session.add(row)
    session.flush()
    return row


def _latest_completed_run(session: Session) -> DailyRadarRun | None:
    return session.execute(
        select(DailyRadarRun)
        .where(DailyRadarRun.status == "completed")
        .order_by(
            DailyRadarRun.run_date.desc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def _load_daily_radar_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob("*_add_daily_radar_tables.py")
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("daily_radar_tables_migration", migration_paths[0])
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_migration_sql(direction: str) -> str:
    migration = _load_daily_radar_migration()
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    operations = Operations(context)
    original_op = migration.op
    migration.op = operations
    try:
        getattr(migration, direction)()
    finally:
        migration.op = original_op
    return buffer.getvalue()


def test_migration_upgrade_creates_daily_radar_tables_constraints_and_indexes() -> None:
    sql = _render_migration_sql("upgrade")

    assert "CREATE TABLE daily_radar_runs" in sql
    assert "CREATE TABLE daily_radar_candidates" in sql
    assert "FOREIGN KEY(run_id) REFERENCES daily_radar_runs (id)" in sql
    assert "CONSTRAINT uq_daily_radar_candidates_run_symbol UNIQUE (run_id, symbol)" in sql
    assert "CREATE INDEX idx_daily_radar_runs_run_date ON daily_radar_runs (run_date)" in sql
    assert "CREATE INDEX idx_daily_radar_candidates_symbol ON daily_radar_candidates (symbol)" in sql
    assert "CREATE INDEX idx_daily_radar_candidates_primary_bucket ON daily_radar_candidates (primary_bucket)" in sql
    assert "CREATE INDEX idx_daily_radar_candidates_observation_score ON daily_radar_candidates (observation_score)" in sql
    assert "JSONB" in sql


def test_migration_downgrade_drops_constraints_indexes_and_tables_in_reverse_order() -> None:
    sql = _render_migration_sql("downgrade")

    candidate_index_position = sql.index("DROP INDEX idx_daily_radar_candidates_observation_score")
    constraint_position = sql.index("ALTER TABLE daily_radar_candidates DROP CONSTRAINT uq_daily_radar_candidates_run_symbol")
    candidate_table_position = sql.index("DROP TABLE daily_radar_candidates")
    run_index_position = sql.index("DROP INDEX idx_daily_radar_runs_run_date")
    run_table_position = sql.index("DROP TABLE daily_radar_runs")

    assert candidate_index_position < constraint_position < candidate_table_position
    assert candidate_table_position < run_index_position < run_table_position


def test_can_create_run_and_write_candidates(db_session: Session) -> None:
    run = _create_run(
        db_session,
        run_date=date(2026, 6, 1),
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        candidate_count=2,
    )
    _add_candidate(db_session, run, symbol="2330.TW", score=91)
    _add_candidate(db_session, run, symbol="2317.TW", score=84)
    db_session.commit()

    stored_run = db_session.execute(select(DailyRadarRun)).scalar_one()

    assert stored_run.run_date == date(2026, 6, 1)
    assert stored_run.status == "completed"
    assert stored_run.candidate_count == 2
    candidates_by_symbol = {candidate.symbol: candidate for candidate in stored_run.candidates}

    assert set(candidates_by_symbol) == {"2330.TW", "2317.TW"}
    assert candidates_by_symbol["2330.TW"].bucket_scores == {"institutional_accumulation": 91}
    assert candidates_by_symbol["2330.TW"].data_dates == {"ohlcv": "2026-06-01"}


def test_same_run_rejects_duplicate_symbol_candidates(db_session: Session) -> None:
    run = _create_run(db_session, run_date=date(2026, 6, 1), candidate_count=2)
    _add_candidate(db_session, run, symbol="2330.TW")
    db_session.add(
        DailyRadarCandidate(
            run_id=run.id,
            symbol="2330.TW",
            name="duplicate fixture",
            primary_bucket="institutional_accumulation",
            secondary_buckets=[],
            observation_score=72,
            bucket_scores={"institutional_accumulation": 72},
            risk_labels=[],
            matched_rules=["fixture_rule"],
            explanation="Rule-based duplicate symbol fixture.",
            repeat_status="repeat",
            score_breakdown={"base": 72},
            input_snapshot={"symbol": "2330.TW"},
            data_dates={"ohlcv": "2026-06-01"},
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_latest_completed_run_uses_newest_completed_log_for_public_reads(db_session: Session) -> None:
    run_date = date(2026, 6, 1)
    old_completed = _create_run(
        db_session,
        run_date=run_date,
        status="completed",
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        candidate_count=1,
    )
    _add_candidate(db_session, old_completed, symbol="2330.TW", score=82)
    _create_run(
        db_session,
        run_date=run_date,
        status="failed",
        created_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
        candidate_count=0,
    )
    latest_completed = _create_run(
        db_session,
        run_date=run_date,
        status="completed",
        created_at=datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc),
        candidate_count=1,
    )
    _add_candidate(db_session, latest_completed, symbol="2317.TW", score=88)
    db_session.commit()

    public_run = _latest_completed_run(db_session)

    assert public_run is not None
    assert public_run.id == latest_completed.id
    assert public_run.run_date == run_date
    assert public_run.status == "completed"
    assert [candidate.symbol for candidate in public_run.candidates] == ["2317.TW"]
    run_count = db_session.scalar(
        select(func.count()).select_from(DailyRadarRun).where(DailyRadarRun.run_date == run_date)
    )

    assert run_count == 3


def test_repository_creates_running_run_and_updates_counts_and_status(db_session: Session) -> None:
    run = create_daily_radar_run(db_session, run_date=date(2026, 6, 2), market="TW")

    assert run.id is not None
    assert run.status == "running"
    assert run.run_date == date(2026, 6, 2)
    assert run.market == "TW"

    updated = update_daily_radar_run(
        db_session,
        run,
        status="completed",
        universe_count=8,
        prefilter_count=4,
        candidate_count=3,
        errors=[{"code": "prefilter_rejected", "symbol": "2603.TW"}],
    )
    db_session.commit()

    assert updated.status == "completed"
    assert updated.finished_at is not None
    assert updated.universe_count == 8
    assert updated.prefilter_count == 4
    assert updated.candidate_count == 3
    assert updated.errors == [{"code": "prefilter_rejected", "symbol": "2603.TW"}]


def test_repository_replaces_candidates_for_same_run(db_session: Session) -> None:
    run = create_daily_radar_run(db_session, run_date=date(2026, 6, 2), market="TW")
    first_payload = [
        {
            "symbol": "2330.TW",
            "name": "TSMC",
            "primary_bucket": "institutional_accumulation",
            "secondary_buckets": [],
            "observation_score": 76,
            "bucket_scores": {"institutional_accumulation": 76},
            "risk_labels": [],
            "matched_rules": [{"rule_id": "first"}],
            "explanation": "Rule-based observation summary.",
            "repeat_status": "new",
            "score_breakdown": {
                "scoring_version": "daily-radar-scoring-v2.1c",
                "rule_version": "daily-radar-rules-v2.1c",
                "relative_strength": {
                    "benchmark_symbol": "TAIEX",
                    "lookback_days": 20,
                    "relative_value": 0.031,
                    "score": 3,
                    "freshness": "fresh",
                },
                "observation_score": 76,
            },
            "input_snapshot": {
                "symbol": "2330.TW",
                "evidence": [
                    {
                        "evidence_type": "relative_strength",
                        "source": {
                            "domain": "daily_trigger_signal",
                            "provider": "deterministic_relative_strength",
                        },
                        "as_of_date": "2026-06-02",
                        "freshness": "fresh",
                        "missing_reason": None,
                        "replay_key": "relative_strength:2330.TW:TAIEX:2026-06-02:L20",
                        "applicable_consumers": ["daily_radar"],
                    }
                ],
            },
            "data_dates": {"ohlcv": "2026-06-02", "relative_strength": "2026-06-02"},
        }
    ]
    second_payload = [
        first_payload[0]
        | {
            "observation_score": 88,
            "score_breakdown": first_payload[0]["score_breakdown"] | {"observation_score": 88},
        },
        first_payload[0] | {"symbol": "2454.TW", "name": "MediaTek", "observation_score": 82},
    ]

    replace_run_candidates(db_session, run, first_payload)
    replace_run_candidates(db_session, run, second_payload)
    db_session.commit()

    candidates = db_session.scalars(
        select(DailyRadarCandidate).where(DailyRadarCandidate.run_id == run.id).order_by(DailyRadarCandidate.symbol)
    ).all()

    assert [(candidate.symbol, candidate.observation_score) for candidate in candidates] == [
        ("2330.TW", 88),
        ("2454.TW", 82),
    ]
    assert candidates[0].score_breakdown["scoring_version"] == "daily-radar-scoring-v2.1c"
    assert candidates[0].score_breakdown["rule_version"] == "daily-radar-rules-v2.1c"
    assert candidates[0].score_breakdown["relative_strength"]["benchmark_symbol"] == "TAIEX"
    assert candidates[0].input_snapshot["evidence"][0]["replay_key"] == "relative_strength:2330.TW:TAIEX:2026-06-02:L20"
    assert candidates[0].data_dates["relative_strength"] == "2026-06-02"


def test_repository_returns_only_final_raw_data_rows_for_requested_date_ordered_by_symbol(db_session: Session) -> None:
    _add_raw_data(db_session, symbol="2454.TW", record_date=date(2026, 6, 2), is_final=True)
    _add_raw_data(db_session, symbol="2330.TW", record_date=date(2026, 6, 2), is_final=True)
    _add_raw_data(db_session, symbol="2317.TW", record_date=date(2026, 6, 2), is_final=False)
    _add_raw_data(db_session, symbol="2303.TW", record_date=date(2026, 6, 1), is_final=True)
    db_session.commit()

    rows = get_final_raw_data_rows_for_date(db_session, run_date=date(2026, 6, 2))

    assert [row.symbol for row in rows] == ["2330.TW", "2454.TW"]


def test_repository_returns_final_raw_data_rows_for_selected_symbols_in_selected_order(db_session: Session) -> None:
    _add_raw_data(db_session, symbol="2454.TW", record_date=date(2026, 6, 2), is_final=True)
    _add_raw_data(db_session, symbol="2330.TW", record_date=date(2026, 6, 2), is_final=True)
    _add_raw_data(db_session, symbol="2317.TW", record_date=date(2026, 6, 2), is_final=False)
    _add_raw_data(db_session, symbol="2303.TW", record_date=date(2026, 6, 1), is_final=True)
    db_session.commit()

    rows = get_final_raw_data_rows_for_symbols(
        db_session,
        run_date=date(2026, 6, 2),
        symbols=["2317.TW", "2454.TW", "2330.TW", "2454.TW"],
    )

    assert [row.symbol for row in rows] == ["2454.TW", "2330.TW"]


def test_repository_returns_empty_selected_symbol_rows_without_symbols(db_session: Session) -> None:
    _add_raw_data(db_session, symbol="2330.TW", record_date=date(2026, 6, 2), is_final=True)
    db_session.commit()

    rows = get_final_raw_data_rows_for_symbols(db_session, run_date=date(2026, 6, 2), symbols=[])

    assert rows == []


def test_repository_public_queries_use_latest_completed_or_stale_data_run(db_session: Session) -> None:
    _create_run(
        db_session,
        run_date=date(2026, 6, 1),
        status="completed",
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
    )
    _create_run(
        db_session,
        run_date=date(2026, 6, 2),
        status="running",
        created_at=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
    )
    stale = _create_run(
        db_session,
        run_date=date(2026, 6, 2),
        status="stale_data",
        created_at=datetime(2026, 6, 2, 11, 0, tzinfo=timezone.utc),
    )
    db_session.commit()

    assert get_latest_daily_radar_run(db_session, market="TW") == stale
    assert get_daily_radar_run_by_date(db_session, run_date=date(2026, 6, 2), market="TW") == stale


def test_repository_symbol_history_returns_recent_candidates_for_cooldown(db_session: Session) -> None:
    older_run = _create_run(db_session, run_date=date(2026, 5, 28), status="completed")
    newer_run = _create_run(db_session, run_date=date(2026, 5, 30), status="stale_data")
    ignored_run = _create_run(db_session, run_date=date(2026, 5, 31), status="failed")
    _add_candidate(db_session, older_run, symbol="2330.TW", score=71)
    _add_candidate(db_session, newer_run, symbol="2330.TW", score=83)
    _add_candidate(db_session, ignored_run, symbol="2330.TW", score=99)
    db_session.commit()

    history = get_symbol_candidate_history(
        db_session,
        symbols=["2330.TW"],
        before_date=date(2026, 6, 1),
        lookback_days=5,
        market="TW",
    )

    assert [item["record_date"] for item in history] == ["2026-05-30", "2026-05-28"]
    assert [item["observation_score"] for item in history] == [83, 71]
    assert all(item["symbol"] == "2330.TW" for item in history)


def test_repository_symbol_history_returns_version_trace_from_score_breakdown(db_session: Session) -> None:
    run = _create_run(db_session, run_date=date(2026, 5, 30), status="completed")
    candidate = _add_candidate(db_session, run, symbol="2330.TW", score=83)
    candidate.score_breakdown = {
        "scoring_version": "daily-radar-scoring-v2.1c",
        "rule_version": "daily-radar-rules-v2.1c",
        "relative_strength": {"benchmark_symbol": "TAIEX", "score": 3},
    }
    db_session.commit()

    history = get_symbol_candidate_history(
        db_session,
        symbols=["2330.TW"],
        before_date=date(2026, 6, 1),
        lookback_days=5,
        market="TW",
    )

    assert history[0]["scoring_version"] == "daily-radar-scoring-v2.1c"
    assert history[0]["rule_version"] == "daily-radar-rules-v2.1c"
    assert history[0]["score_breakdown"]["relative_strength"]["benchmark_symbol"] == "TAIEX"
