from __future__ import annotations

import json
import importlib.util
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi.testclient import TestClient
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.daily_radar.forward_validation import (
    build_forward_validation_report,
    evaluate_forward_window,
    forward_validation_fixture_inputs,
)
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarRun,
    StockRawData,
)
from ai_stock_sentinel.db.session import Base, get_db


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"


def test_forward_window_calculates_return_mfe_mae_and_benchmark_excess() -> None:
    candidate = _candidate_snapshot()
    outcome = evaluate_forward_window(
        candidate,
        price_series=[
            _price("2026-06-01", 100, 101, 99, 100),
            _price("2026-06-02", 101, 104, 98, 102),
            _price("2026-06-03", 102, 106, 101, 104),
            _price("2026-06-04", 104, 108, 103, 106),
            _price("2026-06-05", 106, 110, 105, 108),
            _price("2026-06-08", 108, 112, 107, 110),
        ],
        benchmark_prices=[
            _price("2026-06-01", 1000, 1005, 995, 1000),
            _price("2026-06-02", 1002, 1010, 1000, 1004),
            _price("2026-06-03", 1004, 1012, 1001, 1008),
            _price("2026-06-04", 1008, 1016, 1004, 1012),
            _price("2026-06-05", 1012, 1020, 1008, 1016),
            _price("2026-06-08", 1016, 1026, 1010, 1020),
        ],
        window_days=5,
        as_of_date=date(2026, 6, 8),
        benchmark_symbol="TAIEX",
        validation_version="unit-validation",
        hit_threshold_pct=0.0,
    )

    assert outcome["status"] == "validated"
    assert outcome["target_date"] == "2026-06-08"
    assert outcome["outcome"]["forward_return_pct"] == 10.0
    assert outcome["outcome"]["benchmark_return_pct"] == 2.0
    assert outcome["outcome"]["excess_return_vs_benchmark_pct"] == 8.0
    assert outcome["outcome"]["max_favorable_excursion_pct"] == 12.0
    assert outcome["outcome"]["max_adverse_excursion_pct"] == -2.0
    assert outcome["outcome"]["close_below_defense_reference"] is False
    assert outcome["outcome"]["hit_above_threshold"] is True


def test_forward_window_records_explicit_skip_reasons_for_missing_inputs() -> None:
    candidate = _candidate_snapshot()

    missing_future = evaluate_forward_window(
        candidate,
        price_series=[_price("2026-06-01", 100, 101, 99, 100)],
        benchmark_prices=[_price("2026-06-01", 1000, 1005, 995, 1000)],
        window_days=5,
        as_of_date=date(2026, 6, 8),
        benchmark_symbol="TAIEX",
        validation_version="unit-validation",
        hit_threshold_pct=0.0,
    )
    missing_benchmark = evaluate_forward_window(
        candidate,
        price_series=[
            _price("2026-06-01", 100, 101, 99, 100),
            _price("2026-06-02", 101, 104, 98, 102),
            _price("2026-06-03", 102, 106, 101, 104),
            _price("2026-06-04", 104, 108, 103, 106),
            _price("2026-06-05", 106, 110, 105, 108),
            _price("2026-06-08", 108, 112, 107, 110),
        ],
        benchmark_prices=[],
        window_days=5,
        as_of_date=date(2026, 6, 8),
        benchmark_symbol="TAIEX",
        validation_version="unit-validation",
        hit_threshold_pct=0.0,
    )
    stale_candidate = evaluate_forward_window(
        _candidate_snapshot(data_dates={"ohlcv": "2026-05-30"}),
        price_series=[_price("2026-06-01", 100, 101, 99, 100)],
        benchmark_prices=[_price("2026-06-01", 1000, 1005, 995, 1000)],
        window_days=5,
        as_of_date=date(2026, 6, 8),
        benchmark_symbol="TAIEX",
        validation_version="unit-validation",
        hit_threshold_pct=0.0,
    )

    assert missing_future["skip_reason"] == "missing_future_price"
    assert missing_benchmark["skip_reason"] == "missing_benchmark"
    assert stale_candidate["skip_reason"] == "stale_candidate_price"


def test_forward_validation_fixture_report_is_deterministic_and_grouped() -> None:
    candidates, prices_by_symbol, benchmark_prices, benchmark_symbol = forward_validation_fixture_inputs(
        fixture_dir=FIXTURE_DIR,
        run_date=date(2026, 5, 29),
        market="TW",
    )

    first = build_forward_validation_report(
        candidates,
        price_series_by_symbol=prices_by_symbol,
        benchmark_prices=benchmark_prices,
        market="TW",
        sample_source="fixture",
        as_of_date=date(2026, 6, 26),
        windows=[5, 10, 20],
        benchmark_symbol=benchmark_symbol,
    ).report
    second = build_forward_validation_report(
        candidates,
        price_series_by_symbol=prices_by_symbol,
        benchmark_prices=benchmark_prices,
        market="TW",
        sample_source="fixture",
        as_of_date=date(2026, 6, 26),
        windows=[5, 10, 20],
        benchmark_symbol=benchmark_symbol,
    ).report

    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert first["metadata"]["positioning"] == "rule_quality_calibration_diagnostic_not_performance_marketing"
    assert first["sample_summary"]["candidate_count"] == 4
    assert first["sample_summary"]["validated_by_window"] == {"5": 4, "10": 4, "20": 4}
    assert first["skip_reasons"] == {}
    assert "institutional_accumulation" in first["bucket_outcomes"]
    assert "price_volume_expanded_participation" in first["rule_outcomes"]
    assert first["version_manifest"]["live_scoring_changed"] is False
    assert first["version_manifest"]["live_ranking_changed"] is False


def test_forward_validation_internal_api_writes_idempotent_results(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
            StockRawData.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        _add_candidate(session, run)
        for row_date, close in [
            (date(2026, 6, 1), 100),
            (date(2026, 6, 2), 102),
            (date(2026, 6, 3), 104),
            (date(2026, 6, 4), 106),
            (date(2026, 6, 5), 108),
            (date(2026, 6, 8), 110),
        ]:
            _add_raw(session, "2330.TW", row_date, close)
        for row_date, close in [
            (date(2026, 6, 1), 1000),
            (date(2026, 6, 2), 1004),
            (date(2026, 6, 3), 1008),
            (date(2026, 6, 4), 1012),
            (date(2026, 6, 5), 1016),
            (date(2026, 6, 8), 1020),
        ]:
            _add_raw(session, "TAIEX", row_date, close)
        session.commit()

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    try:
        client = TestClient(api.app)
        payload = {
            "mode": "due",
            "market": "TW",
            "as_of_date": "2026-06-08",
            "windows": [5],
            "benchmark_symbol": "TAIEX",
        }
        first = client.post(
            "/internal/daily-radar/forward-validation/run",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        )
        second = client.post(
            "/internal/daily-radar/forward-validation/run",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["records_written"] == 1
    assert first.json()["validated_count"] == 1
    assert second.json()["records_written"] == 1

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 1
        row = session.execute(select(DailyRadarForwardValidationResult)).scalar_one()

    assert row.status == "validated"
    assert row.window_days == 5
    assert row.outcome["forward_return_pct"] == 10.0


def test_forward_validation_due_mode_only_writes_matured_windows(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
            StockRawData.__table__,
        ],
    )
    with Session(engine) as session:
        matured_run = _add_run(session, run_date=date(2026, 6, 1))
        matured_candidate = _add_candidate(session, matured_run, symbol="2330.TW", close=100)
        recent_run = _add_run(session, run_date=date(2026, 6, 5))
        recent_candidate = _add_candidate(session, recent_run, symbol="2454.TW", close=200)
        matured_candidate_id = matured_candidate.id
        recent_candidate_id = recent_candidate.id
        for row_date, close in [
            (date(2026, 6, 1), 100),
            (date(2026, 6, 2), 102),
            (date(2026, 6, 3), 104),
            (date(2026, 6, 4), 106),
            (date(2026, 6, 5), 108),
            (date(2026, 6, 8), 110),
        ]:
            _add_raw(session, "2330.TW", row_date, close)
        for row_date, close in [
            (date(2026, 6, 5), 200),
            (date(2026, 6, 8), 202),
        ]:
            _add_raw(session, "2454.TW", row_date, close)
        for row_date, close in [
            (date(2026, 6, 1), 1000),
            (date(2026, 6, 2), 1004),
            (date(2026, 6, 3), 1008),
            (date(2026, 6, 4), 1012),
            (date(2026, 6, 5), 1016),
            (date(2026, 6, 8), 1020),
        ]:
            _add_raw(session, "TAIEX", row_date, close)
        session.commit()

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    try:
        client = TestClient(api.app)
        resp = client.post(
            "/internal/daily-radar/forward-validation/run",
            json={
                "mode": "due",
                "market": "TW",
                "as_of_date": "2026-06-08",
                "windows": [5],
                "benchmark_symbol": "TAIEX",
            },
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["candidate_count"] == 2
    assert data["records_written"] == 1
    assert data["validated_count"] == 1
    assert data["skipped_count"] == 0
    assert data["report"]["skip_reasons"] == {}

    with Session(engine) as session:
        rows = session.execute(select(DailyRadarForwardValidationResult)).scalars().all()

    assert [row.candidate_id for row in rows] == [matured_candidate_id]
    assert recent_candidate_id not in [row.candidate_id for row in rows]


def test_forward_validation_due_mode_uses_price_rows_not_weekdays(monkeypatch) -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
            StockRawData.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session, run_date=date(2026, 6, 1))
        _add_candidate(session, run, symbol="2330.TW", close=100)
        # 2026-06-04 is intentionally absent from both series. Weekday count reaches
        # five by 2026-06-08, but the market-data trading rows only contain four
        # forward rows, so a 5-day window is not due yet.
        for row_date, close in [
            (date(2026, 6, 1), 100),
            (date(2026, 6, 2), 102),
            (date(2026, 6, 3), 104),
            (date(2026, 6, 5), 108),
            (date(2026, 6, 8), 110),
        ]:
            _add_raw(session, "2330.TW", row_date, close)
        for row_date, close in [
            (date(2026, 6, 1), 1000),
            (date(2026, 6, 2), 1004),
            (date(2026, 6, 3), 1008),
            (date(2026, 6, 5), 1016),
            (date(2026, 6, 8), 1020),
        ]:
            _add_raw(session, "TAIEX", row_date, close)
        session.commit()

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    try:
        resp = TestClient(api.app).post(
            "/internal/daily-radar/forward-validation/run",
            json={
                "mode": "due",
                "market": "TW",
                "as_of_date": "2026-06-08",
                "windows": [5],
                "benchmark_symbol": "TAIEX",
            },
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["candidate_count"] == 1
    assert data["records_written"] == 0
    assert data["validated_count"] == 0
    assert data["skipped_count"] == 0
    assert data["report"]["skip_reasons"] == {}

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0


def test_forward_validation_due_mode_keeps_missing_benchmark_skip_reason(monkeypatch) -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
            StockRawData.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session, run_date=date(2026, 6, 1))
        _add_candidate(session, run, symbol="2330.TW", close=100)
        for row_date, close in [
            (date(2026, 6, 1), 100),
            (date(2026, 6, 2), 102),
            (date(2026, 6, 3), 104),
            (date(2026, 6, 4), 106),
            (date(2026, 6, 5), 108),
            (date(2026, 6, 8), 110),
        ]:
            _add_raw(session, "2330.TW", row_date, close)
        for row_date, close in [
            (date(2026, 6, 1), 1000),
            (date(2026, 6, 2), 1004),
            (date(2026, 6, 3), 1008),
            (date(2026, 6, 5), 1016),
            (date(2026, 6, 8), 1020),
        ]:
            _add_raw(session, "TAIEX", row_date, close)
        session.commit()

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    try:
        resp = TestClient(api.app).post(
            "/internal/daily-radar/forward-validation/run",
            json={
                "mode": "due",
                "market": "TW",
                "as_of_date": "2026-06-08",
                "windows": [5],
                "benchmark_symbol": "TAIEX",
            },
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["records_written"] == 1
    assert data["validated_count"] == 0
    assert data["skipped_count"] == 1
    assert data["report"]["skip_reasons"] == {"missing_benchmark": 1}

    with Session(engine) as session:
        row = session.execute(select(DailyRadarForwardValidationResult)).scalar_one()

    assert row.status == "skipped"
    assert row.skip_reason == "missing_benchmark"


def test_forward_validation_migration_creates_idempotency_key_and_indexes() -> None:
    upgrade_sql = _render_forward_validation_migration_sql("upgrade")
    downgrade_sql = _render_forward_validation_migration_sql("downgrade")

    assert "CREATE TABLE daily_radar_forward_validation_results" in upgrade_sql
    assert "FOREIGN KEY(candidate_id) REFERENCES daily_radar_candidates (id)" in upgrade_sql
    assert "uq_daily_radar_forward_validation_candidate_window_version" in upgrade_sql
    assert "CREATE INDEX idx_daily_radar_forward_validation_candidate_id" in upgrade_sql
    assert "CREATE INDEX idx_daily_radar_forward_validation_window_days" in upgrade_sql
    assert "CREATE INDEX idx_daily_radar_forward_validation_status" in upgrade_sql
    assert "JSONB" in upgrade_sql
    assert "DROP TABLE daily_radar_forward_validation_results" in downgrade_sql


def _load_forward_validation_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob(
            "*_add_daily_radar_forward_validation_results.py"
        )
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("daily_radar_forward_validation_migration", migration_paths[0])
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_forward_validation_migration_sql(direction: str) -> str:
    migration = _load_forward_validation_migration()
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


def _forward_validation_sqlite_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _candidate_snapshot(*, data_dates: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "candidate_id": 1,
        "symbol": "2330.TW",
        "name": "TSMC",
        "record_date": "2026-06-01",
        "primary_bucket": "institutional_accumulation",
        "secondary_buckets": ["price_volume_strengthening"],
        "observation_score": 88,
        "risk_labels": [],
        "matched_rules": [{"rule_id": "institutional_consecutive_flow"}],
        "repeat_status": "new",
        "score_breakdown": {
            "market_context": {"details": {"regime": "constructive"}},
            "relative_strength": {"freshness": "fresh", "relative_value": 0.03},
        },
        "input_snapshot": {
            "ohlcv": {"close": 100},
            "indicators": {"support_level": 96, "ma20": 98},
            "market_context": {"regime": "constructive"},
        },
        "data_dates": data_dates or {"ohlcv": "2026-06-01"},
    }


def _price(row_date: str, open_: float, high: float, low: float, close: float) -> dict[str, Any]:
    return {"date": row_date, "open": open_, "high": high, "low": low, "close": close}


def _add_run(session: Session, *, run_date: date = date(2026, 6, 1)) -> DailyRadarRun:
    run = DailyRadarRun(
        run_date=run_date,
        market="TW",
        status="completed",
        started_at=datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc),
        finished_at=datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc),
        universe_count=1,
        prefilter_count=1,
        candidate_count=1,
        errors=[],
        created_at=datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def _add_candidate(
    session: Session,
    run: DailyRadarRun,
    *,
    symbol: str = "2330.TW",
    close: float = 100,
) -> DailyRadarCandidate:
    candidate = DailyRadarCandidate(
        run_id=run.id,
        symbol=symbol,
        name="TSMC",
        primary_bucket="institutional_accumulation",
        secondary_buckets=[],
        observation_score=88,
        bucket_scores={"institutional_accumulation": 88},
        risk_labels=[],
        matched_rules=[{"rule_id": "institutional_consecutive_flow"}],
        explanation="Observation summary.",
        repeat_status="new",
        score_breakdown={"relative_strength": {"freshness": "fresh", "relative_value": 0.03}},
        input_snapshot={
            "ohlcv": {"close": close},
            "indicators": {"support_level": close * 0.96},
            "market_context": {"regime": "constructive"},
        },
        data_dates={"ohlcv": run.run_date.isoformat()},
    )
    session.add(candidate)
    session.flush()
    return candidate


def _add_raw(session: Session, symbol: str, row_date: date, close: float) -> None:
    session.add(
        StockRawData(
            symbol=symbol,
            record_date=row_date,
            technical={"ohlcv": {"open": close, "high": close + 2, "low": close - 2, "close": close}},
            institutional={},
            fundamental={},
            raw_data_is_final=True,
        )
    )
