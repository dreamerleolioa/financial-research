from __future__ import annotations

import json
import importlib.util
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.analysis.calibration import GENERAL_ANALYSIS_FORWARD_ADAPTER
from ai_stock_sentinel.calibration.forward_validation import (
    evaluate_forward_window as evaluate_shared_forward_window,
)
from ai_stock_sentinel.calibration.forward_validation_planning import (
    evaluation_ready_windows_by_candidate,
)
from ai_stock_sentinel.calibration.price_provider import get_forward_price_provider
from ai_stock_sentinel.daily_radar.forward_validation import (
    DAILY_RADAR_FORWARD_ADAPTER,
    FORWARD_VALIDATION_VERSION,
    build_forward_validation_report,
    evaluate_forward_window,
    exclude_persisted_daily_radar_windows,
    forward_validation_candidates_from_runs,
    forward_validation_fixture_inputs,
    load_benchmark_prices_from_prepared_market_context,
    merge_price_series,
    symbols_requiring_forward_price_refresh,
    upsert_forward_validation_results,
    validate_forward_validation_benchmark,
)
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarPreparedRun,
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


def test_forward_window_uses_benchmark_trading_dates_for_both_series() -> None:
    daily_candidate = _candidate_snapshot()
    general_candidate = {
        "candidate_id": daily_candidate["candidate_id"],
        "symbol": daily_candidate["symbol"],
        "record_date": daily_candidate["record_date"],
        "data_dates": daily_candidate["data_dates"],
        "input_snapshot": {
            "ohlcv": {
                "close": daily_candidate["input_snapshot"]["ohlcv"]["close"],
            },
        },
    }
    candidate_prices = [
        _price("2026-06-01", 100, 101, 99, 100),
        _price("2026-06-02", 101, 102, 100, 101),
        _price("2026-06-03", 102, 103, 101, 102),
        _price("2026-06-04", 999, 999, 999, 999),  # provider-only row on a market closure
        _price("2026-06-05", 103, 104, 102, 103),
    ]
    benchmark_prices = [
        _price("2026-06-01", 1000, 1001, 999, 1000),
        _price("2026-06-02", 1001, 1002, 1000, 1001),
        _price("2026-06-03", 1002, 1003, 1001, 1002),
        _price("2026-06-05", 1003, 1004, 1002, 1003),
    ]

    daily = evaluate_forward_window(
        daily_candidate,
        price_series=candidate_prices,
        benchmark_prices=benchmark_prices,
        window_days=3,
        as_of_date=date(2026, 6, 5),
        benchmark_symbol="TAIEX",
        validation_version="aligned-calendar-v1",
        hit_threshold_pct=0.0,
    )
    general = evaluate_shared_forward_window(
        general_candidate,
        price_series=candidate_prices,
        benchmark_prices=benchmark_prices,
        adapter=GENERAL_ANALYSIS_FORWARD_ADAPTER,
        window_days=3,
        as_of_date=date(2026, 6, 5),
        benchmark_symbol="TAIEX",
        validation_version="aligned-calendar-v1",
        hit_threshold_pct=0.0,
    )

    assert daily["status"] == general["status"] == "validated"
    assert daily["target_date"] == general["target_date"] == "2026-06-05"
    assert daily["outcome"]["target_price"] == general["outcome"]["target_price"] == 103.0
    assert daily["outcome"]["max_favorable_excursion_pct"] == 4.0
    assert daily["outcome"]["max_adverse_excursion_pct"] == 0.0


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


def test_shared_forward_core_keeps_daily_and_general_outcomes_equivalent() -> None:
    daily_candidate = _candidate_snapshot()
    general_candidate = {
        "candidate_id": daily_candidate["candidate_id"],
        "symbol": daily_candidate["symbol"],
        "record_date": daily_candidate["record_date"],
        "data_dates": daily_candidate["data_dates"],
        "input_snapshot": {
            "ohlcv": {
                "close": daily_candidate["input_snapshot"]["ohlcv"]["close"],
            },
        },
    }
    prices = [
        _price("2026-06-01", 100, 101, 99, 100),
        _price("2026-06-02", 101, 104, 98, 102),
        _price("2026-06-03", 102, 106, 101, 104),
        _price("2026-06-04", 104, 108, 103, 106),
        _price("2026-06-05", 106, 110, 105, 108),
        _price("2026-06-08", 108, 112, 107, 110),
    ]
    benchmark = [
        _price("2026-06-01", 1000, 1005, 995, 1000),
        _price("2026-06-02", 1002, 1010, 1000, 1004),
        _price("2026-06-03", 1004, 1012, 1001, 1008),
        _price("2026-06-04", 1008, 1016, 1004, 1012),
        _price("2026-06-05", 1012, 1020, 1008, 1016),
        _price("2026-06-08", 1016, 1026, 1010, 1020),
    ]
    daily = evaluate_forward_window(
        daily_candidate,
        price_series=prices,
        benchmark_prices=benchmark,
        window_days=5,
        as_of_date=date(2026, 6, 8),
        benchmark_symbol="TAIEX",
        validation_version="parity-v1",
        hit_threshold_pct=0.0,
    )
    general = evaluate_shared_forward_window(
        general_candidate,
        price_series=prices,
        benchmark_prices=benchmark,
        adapter=GENERAL_ANALYSIS_FORWARD_ADAPTER,
        window_days=5,
        as_of_date=date(2026, 6, 8),
        benchmark_symbol="TAIEX",
        validation_version="parity-v1",
        hit_threshold_pct=0.0,
    )

    assert daily["status"] == general["status"]
    assert daily["target_date"] == general["target_date"]
    assert daily["skip_reason"] == general["skip_reason"]
    for key in (
        "forward_return_pct",
        "benchmark_return_pct",
        "excess_return_vs_benchmark_pct",
        "max_favorable_excursion_pct",
        "max_adverse_excursion_pct",
        "hit_above_threshold",
        "entry_price",
        "target_price",
        "target_date",
    ):
        assert daily["outcome"][key] == general["outcome"][key]


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
    assert second.json()["records_written"] == 0
    assert second.json()["validated_count"] == 0

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
    assert data["retryable_skipped_count"] == 0
    assert data["terminal_skipped_count"] == 0
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
    assert data["retryable_skipped_count"] == 0
    assert data["terminal_skipped_count"] == 0
    assert data["report"]["skip_reasons"] == {}

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0


def test_due_windows_do_not_mark_recent_missing_price_series_as_mature() -> None:
    from ai_stock_sentinel.daily_radar.forward_validation import due_windows_by_candidate

    due = due_windows_by_candidate(
        [_candidate_snapshot()],
        as_of_date=date(2026, 6, 2),
        windows=[5, 10, 20],
        price_series_by_symbol={},
        benchmark_prices=[],
    )

    assert due == {}


def test_complete_due_windows_require_both_candidate_and_benchmark_rows() -> None:
    from ai_stock_sentinel.daily_radar.forward_validation import due_windows_by_candidate

    candidate = _candidate_snapshot()
    candidate_prices = [
        _price(f"2026-06-{day:02d}", 100 + day, 100 + day, 100 + day, 100 + day)
        for day in range(1, 22)
    ]
    benchmark_prices = [
        _price(f"2026-06-{day:02d}", 1000 + day, 1000 + day, 1000 + day, 1000 + day)
        for day in range(1, 21)
    ]

    discovery = due_windows_by_candidate(
        [candidate],
        as_of_date=date(2026, 6, 21),
        windows=[20],
        price_series_by_symbol={"2330.TW": candidate_prices},
        benchmark_prices=benchmark_prices,
    )
    complete = evaluation_ready_windows_by_candidate(
        [candidate],
        adapter=DAILY_RADAR_FORWARD_ADAPTER,
        as_of_date=date(2026, 6, 21),
        pending_windows_by_candidate={"id:1": [20]},
        price_series_by_symbol={"2330.TW": candidate_prices},
        benchmark_prices=benchmark_prices,
    )
    overdue = evaluation_ready_windows_by_candidate(
        [candidate],
        adapter=DAILY_RADAR_FORWARD_ADAPTER,
        as_of_date=date(2026, 7, 11),
        pending_windows_by_candidate={"id:1": [20]},
        price_series_by_symbol={"2330.TW": candidate_prices},
        benchmark_prices=benchmark_prices,
    )

    assert discovery == {"id:1": [20]}
    assert complete == {}
    assert overdue == {"id:1": [20]}


def test_complete_due_windows_require_candidate_rows_on_benchmark_dates() -> None:
    candidate = _candidate_snapshot()
    candidate_prices = [
        _price("2026-06-01", 100, 100, 100, 100),
        _price("2026-06-02", 101, 101, 101, 101),
        _price("2026-06-03", 102, 102, 102, 102),
        _price("2026-06-04", 999, 999, 999, 999),  # not a benchmark trading date
    ]
    benchmark_prices = [
        _price("2026-06-01", 1000, 1000, 1000, 1000),
        _price("2026-06-02", 1001, 1001, 1001, 1001),
        _price("2026-06-03", 1002, 1002, 1002, 1002),
        _price("2026-06-05", 1003, 1003, 1003, 1003),
    ]

    incomplete = evaluation_ready_windows_by_candidate(
        [candidate],
        adapter=DAILY_RADAR_FORWARD_ADAPTER,
        as_of_date=date(2026, 6, 5),
        pending_windows_by_candidate={"id:1": [3]},
        price_series_by_symbol={"2330.TW": candidate_prices},
        benchmark_prices=benchmark_prices,
    )
    complete = evaluation_ready_windows_by_candidate(
        [candidate],
        adapter=DAILY_RADAR_FORWARD_ADAPTER,
        as_of_date=date(2026, 6, 5),
        pending_windows_by_candidate={"id:1": [3]},
        price_series_by_symbol={
            "2330.TW": [
                *candidate_prices,
                _price("2026-06-05", 103, 103, 103, 103),
            ],
        },
        benchmark_prices=benchmark_prices,
    )

    assert incomplete == {}
    assert complete == {"id:1": [3]}


def test_forward_validation_uses_latest_public_rerun_for_each_run_date() -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
        ],
    )
    with Session(engine) as session:
        first_run = _add_run(session, run_date=date(2026, 6, 1))
        first_run.created_at = datetime(2026, 6, 1, 10, tzinfo=timezone.utc)
        _add_candidate(session, first_run, symbol="OLD.TW", close=100)
        latest_run = _add_run(session, run_date=date(2026, 6, 1))
        latest_run.created_at = datetime(2026, 6, 2, 1, tzinfo=timezone.utc)
        _add_candidate(session, latest_run, symbol="NEW.TW", close=100)
        session.commit()

        candidates = forward_validation_candidates_from_runs(
            session,
            market="TW",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
        )

    assert [candidate["symbol"] for candidate in candidates] == ["NEW.TW"]


def test_benchmark_prices_fall_back_to_latest_prepared_market_context() -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(engine, tables=[DailyRadarPreparedRun.__table__])
    with Session(engine) as session:
        session.add(
            DailyRadarPreparedRun(
                run_date=date(2026, 6, 8),
                market="TW",
                status="prepared",
                selected_symbols=[],
                universe=[],
                symbol_count=0,
                market_context={
                    "benchmark": {
                        "symbol": "TAIEX",
                        "price_history": [
                            {"date": "2026-06-05", "close": 1000},
                            {"date": "2026-06-08", "close": 1010},
                        ],
                    }
                },
                step_statuses={},
                errors=[],
            )
        )
        session.add(
            DailyRadarPreparedRun(
                run_date=date(2026, 6, 9),
                market="TW",
                status="prepared",
                selected_symbols=[],
                universe=[],
                symbol_count=0,
                market_context={},
                step_statuses={},
                errors=[],
            )
        )
        session.commit()
        prices = load_benchmark_prices_from_prepared_market_context(
            session,
            market="TW",
            benchmark_symbol="TAIEX",
            as_of_date=date(2026, 6, 9),
        )

    assert prices == [
        {"date": "2026-06-05", "open": 1000.0, "high": 1000.0, "low": 1000.0, "close": 1000.0},
        {"date": "2026-06-08", "open": 1010.0, "high": 1010.0, "low": 1010.0, "close": 1010.0},
    ]


def test_retryable_skip_is_retried_but_stale_and_validated_windows_are_terminal() -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        session.flush()
        retryable_summary = upsert_forward_validation_results(
            session,
            [
                {
                    "candidate_id": candidate.id,
                    "window_days": 5,
                    "validation_version": FORWARD_VALIDATION_VERSION,
                    "status": "skipped",
                    "signal_date": "2026-06-01",
                    "benchmark_symbol": "TAIEX",
                    "outcome": {},
                    "skip_reason": "missing_benchmark",
                }
            ],
        )

        pending = exclude_persisted_daily_radar_windows(
            session,
            {f"id:{candidate.id}": [5]},
        )
        terminal_summary = upsert_forward_validation_results(
            session,
            [
                {
                    "candidate_id": candidate.id,
                    "window_days": 5,
                    "validation_version": FORWARD_VALIDATION_VERSION,
                    "status": "skipped",
                    "signal_date": "2026-06-01",
                    "benchmark_symbol": "TAIEX",
                    "outcome": {},
                    "skip_reason": "stale_candidate_price",
                }
            ],
        )
        stale = exclude_persisted_daily_radar_windows(
            session,
            {f"id:{candidate.id}": [5]},
        )
        result = session.execute(select(DailyRadarForwardValidationResult)).scalar_one()
        result.status = "validated"
        result.skip_reason = None
        session.flush()
        completed = exclude_persisted_daily_radar_windows(
            session,
            {f"id:{candidate.id}": [5]},
        )

    assert retryable_summary["skipped_count"] == 1
    assert retryable_summary["retryable_skipped_count"] == 1
    assert retryable_summary["terminal_skipped_count"] == 0
    assert terminal_summary["skipped_count"] == 1
    assert terminal_summary["retryable_skipped_count"] == 0
    assert terminal_summary["terminal_skipped_count"] == 1
    assert pending == {f"id:{candidate.id}": [5]}
    assert stale == {}
    assert completed == {}


def test_daily_radar_validation_rejects_and_requeues_identity_mismatches() -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        candidate.input_snapshot = {
            **candidate.input_snapshot,
            "replay_input": {
                "market_context": {
                    "benchmark": {"symbol": "TAIEX"},
                }
            },
        }
        session.flush()

        with pytest.raises(ValueError, match="benchmark must match"):
            validate_forward_validation_benchmark(
                [
                    {
                        "candidate_id": candidate.id,
                        "input_snapshot": candidate.input_snapshot,
                    }
                ],
                benchmark_symbol="SPY",
            )
        with pytest.raises(ValueError, match="validation identity"):
            upsert_forward_validation_results(
                session,
                [
                    {
                        "candidate_id": candidate.id,
                        "window_days": 5,
                        "validation_version": FORWARD_VALIDATION_VERSION,
                        "status": "validated",
                        "signal_date": "2026-06-02",
                        "benchmark_symbol": "TAIEX",
                        "outcome": {"forward_return_pct": 1.0},
                        "skip_reason": None,
                    }
                ],
            )
        session.add(
            DailyRadarForwardValidationResult(
                candidate_id=candidate.id,
                window_days=5,
                validation_version=FORWARD_VALIDATION_VERSION,
                status="validated",
                signal_date=run.run_date,
                target_date=date(2026, 6, 8),
                benchmark_symbol="SPY",
                outcome={"forward_return_pct": 1.0},
                skip_reason=None,
            )
        )
        session.flush()

        pending = exclude_persisted_daily_radar_windows(
            session,
            {f"id:{candidate.id}": [5]},
            benchmark_symbol="TAIEX",
        )

    assert pending == {f"id:{candidate.id}": [5]}


def test_legacy_v1_result_does_not_block_current_validation_version() -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        session.flush()
        session.add(
            DailyRadarForwardValidationResult(
                candidate_id=candidate.id,
                window_days=5,
                validation_version="daily-radar-forward-validation-v1",
                status="validated",
                signal_date=date(2026, 6, 1),
                target_date=date(2026, 6, 8),
                benchmark_symbol="TAIEX",
                outcome={"forward_return_pct": 1.0},
                skip_reason=None,
            )
        )
        session.flush()

        pending = exclude_persisted_daily_radar_windows(
            session,
            {f"id:{candidate.id}": [5]},
        )

    assert FORWARD_VALIDATION_VERSION == "daily-radar-forward-validation-v2"
    assert pending == {f"id:{candidate.id}": [5]}


def test_forward_price_refresh_only_targets_incomplete_due_symbols_and_merges_rows() -> None:
    candidates = [
        _candidate_snapshot() | {"candidate_id": 1, "symbol": "2330.TW"},
        _candidate_snapshot() | {"candidate_id": 2, "symbol": "2317.TW"},
    ]
    existing = {
        "2330.TW": [
            _price("2026-06-01", 100, 100, 100, 100),
            _price("2026-06-02", 101, 101, 101, 101),
            _price("2026-06-03", 102, 102, 102, 102),
            _price("2026-06-04", 103, 103, 103, 103),
            _price("2026-06-05", 104, 104, 104, 104),
            _price("2026-06-08", 105, 105, 105, 105),
        ],
        "2317.TW": [_price("2026-06-01", 100, 100, 100, 100)],
    }

    required = symbols_requiring_forward_price_refresh(
        candidates,
        windows_by_candidate={"id:1": [5], "id:2": [5]},
        price_series_by_symbol=existing,
        as_of_date=date(2026, 6, 8),
    )
    merged = merge_price_series(
        existing,
        {"2317.TW": [_price("2026-06-02", 101, 101, 101, 101)]},
    )

    assert required == ["2317.TW"]
    assert [row["date"] for row in merged["2317.TW"]] == ["2026-06-01", "2026-06-02"]


def test_forward_price_refresh_targets_symbol_missing_a_benchmark_trading_date() -> None:
    candidate = _candidate_snapshot()
    required = symbols_requiring_forward_price_refresh(
        [candidate],
        windows_by_candidate={"id:1": [3]},
        price_series_by_symbol={
            "2330.TW": [
                _price("2026-06-01", 100, 100, 100, 100),
                _price("2026-06-02", 101, 101, 101, 101),
                _price("2026-06-03", 102, 102, 102, 102),
                _price("2026-06-04", 999, 999, 999, 999),
            ],
        },
        benchmark_prices=[
            _price("2026-06-01", 1000, 1000, 1000, 1000),
            _price("2026-06-02", 1001, 1001, 1001, 1001),
            _price("2026-06-03", 1002, 1002, 1002, 1002),
            _price("2026-06-05", 1003, 1003, 1003, 1003),
        ],
        as_of_date=date(2026, 6, 5),
    )

    assert required == ["2330.TW"]


def test_due_endpoint_fetches_missing_candidate_prices_from_provider(monkeypatch) -> None:
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
            DailyRadarPreparedRun.__table__,
            StockRawData.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session, run_date=date(2026, 6, 1))
        _add_candidate(session, run, symbol="2330.TW", close=100)
        session.add(
            DailyRadarPreparedRun(
                run_date=date(2026, 6, 8),
                market="TW",
                status="prepared",
                selected_symbols=[],
                universe=[],
                symbol_count=0,
                market_context={
                    "benchmark": {
                        "symbol": "TAIEX",
                        "price_history": [
                            {"date": "2026-06-01", "close": 1000},
                            {"date": "2026-06-02", "close": 1001},
                            {"date": "2026-06-03", "close": 1002},
                            {"date": "2026-06-04", "close": 1003},
                            {"date": "2026-06-05", "close": 1004},
                            {"date": "2026-06-08", "close": 1005},
                        ],
                    }
                },
                step_statuses={},
                errors=[],
            )
        )
        session.commit()

    class FakePriceProvider:
        calls: list[list[str]] = []

        def fetch(self, symbols, *, start_date, end_date):
            self.calls.append(list(symbols))
            return {
                "2330.TW": [
                    _price("2026-06-01", 100, 100, 100, 100),
                    _price("2026-06-02", 101, 101, 101, 101),
                    _price("2026-06-03", 102, 102, 102, 102),
                    _price("2026-06-04", 103, 103, 103, 103),
                    _price("2026-06-05", 104, 104, 104, 104),
                    _price("2026-06-08", 105, 105, 105, 105),
                ]
            }

    provider = FakePriceProvider()
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    api.app.dependency_overrides[get_forward_price_provider] = lambda: provider
    try:
        response = TestClient(api.app).post(
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
        api.app.dependency_overrides.pop(get_forward_price_provider, None)

    assert response.status_code == 200
    assert response.json()["validated_count"] == 1
    assert provider.calls == [["2330.TW"]]


def test_forward_validation_due_mode_waits_for_incomplete_benchmark_window(monkeypatch) -> None:
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

    class MissingBenchmarkProvider:
        def fetch(self, symbols, *, start_date, end_date):
            return {}

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    api.app.dependency_overrides[get_forward_price_provider] = lambda: MissingBenchmarkProvider()
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
        api.app.dependency_overrides.pop(get_forward_price_provider, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["records_written"] == 0
    assert data["validated_count"] == 0
    assert data["skipped_count"] == 0
    assert data["retryable_skipped_count"] == 0
    assert data["terminal_skipped_count"] == 0
    assert data["report"]["skip_reasons"] == {}

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0


def test_due_mode_recomputes_candidate_refresh_after_benchmark_expands(monkeypatch) -> None:
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
        for symbol, rows in {
            "2330.TW": [
                (date(2026, 6, 1), 100),
                (date(2026, 6, 2), 101),
                (date(2026, 6, 3), 102),
                (date(2026, 6, 4), 999),
            ],
            "TAIEX": [
                (date(2026, 6, 1), 1000),
                (date(2026, 6, 2), 1001),
                (date(2026, 6, 3), 1002),
            ],
        }.items():
            for row_date, close in rows:
                _add_raw(session, symbol, row_date, close)
        session.commit()

    class ExpandingBenchmarkProvider:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def fetch(self, symbols, *, start_date, end_date):
            ordered = list(symbols)
            self.calls.append(ordered)
            rows = {
                "TAIEX": [
                    _price("2026-06-01", 1000, 1000, 1000, 1000),
                    _price("2026-06-02", 1001, 1001, 1001, 1001),
                    _price("2026-06-03", 1002, 1002, 1002, 1002),
                    _price("2026-06-05", 1003, 1003, 1003, 1003),
                ],
                "2330.TW": [
                    _price("2026-06-01", 100, 100, 100, 100),
                    _price("2026-06-02", 101, 101, 101, 101),
                    _price("2026-06-03", 102, 102, 102, 102),
                    _price("2026-06-04", 999, 999, 999, 999),
                    _price("2026-06-05", 103, 103, 103, 103),
                ],
            }
            return {symbol: rows[symbol] for symbol in ordered}

    provider = ExpandingBenchmarkProvider()
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    api.app.dependency_overrides[get_forward_price_provider] = lambda: provider
    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/forward-validation/run",
            json={
                "mode": "due",
                "market": "TW",
                "as_of_date": "2026-06-05",
                "windows": [3],
                "benchmark_symbol": "TAIEX",
            },
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)
        api.app.dependency_overrides.pop(get_forward_price_provider, None)

    assert response.status_code == 200
    assert response.json()["validated_count"] == 1
    assert provider.calls == [["TAIEX"], ["2330.TW"]]

    with Session(engine) as session:
        result = session.execute(select(DailyRadarForwardValidationResult)).scalar_one()

    assert result.target_date == date(2026, 6, 5)


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
