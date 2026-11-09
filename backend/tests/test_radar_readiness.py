from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.db.models import (
    DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult,
    DailyRadarPreparedRun, StockRawData,
)
from ai_stock_sentinel.daily_radar import readiness_router
from ai_stock_sentinel.daily_radar.readiness import read_readiness, validation_inventory
from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from test_daily_radar_validation_read import add_candidate, add_run, add_result
from radar_calendar_support import calendar


@pytest.fixture
def storage():
    engine = create_engine("sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult,
        DailyRadarPreparedRun, StockRawData)])
    with Session(engine) as db:
        yield db, engine
    engine.dispose()


def proof(db, start=date(2026, 6, 1), end=date(2026, 6, 30), closed=()):
    row = DailyRadarPreparedRun(run_date=end, market="TW", status="prepared",
        selected_symbols=[], universe=[], symbol_count=0, errors=[],
        step_statuses={"validation-calendar": calendar(start, end, closed).payload()})
    db.add(row)
    db.commit()
    return row


def test_published_radar_and_unfinished_bases_are_reported_independently(storage):
    db, engine = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate)
    add_result(db, candidate, version=RESEARCH_VALIDATION_VERSION, diagnostic=False)
    research = db.scalar(select(DailyRadarForwardValidationResult).where(
        DailyRadarForwardValidationResult.validation_version == RESEARCH_VALIDATION_VERSION))
    research.outcome = research.outcome | {"return_basis": "next_open", "price_basis": "unadjusted_price"}
    proof(db)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, cu, sql, *args: statements.append(sql))
    result = read_readiness(db, run_date=date(2026, 6, 30), lookback_days=90)
    assert result["publication"]["state"] == "not_published"
    # The historical run exists independently of readiness of today's inputs.
    assert read_readiness(db, run_date=date(2026, 6, 1))["publication"]["state"] == "published"
    assert result["validation"]["signal_close"]["validated_count"] == 1
    assert result["validation"]["next_open"]["validated_count"] == 1
    assert result["validation"]["next_open"]["ready"] is False
    assert result["validation"]["next_open"]["missing_outcome_count"] == 2
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)


def test_validated_is_not_complete_without_current_first_observation_diagnostic(storage):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate, diagnostic=False)
    proof(db)
    result = validation_inventory(db, as_of_date=date(2026, 6, 30), lookback_days=90)
    close = result["signal_close"]
    assert close["missing_diagnostic_count"] == 1
    assert any(item["reason"] == "missing_observation_diagnostic" for item in close["gaps"])


def test_official_calendar_prevents_shifted_target_and_holiday_signal(storage):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate)  # 6/8 is the fifth weekday after 6/1
    proof(db, closed=(date(2026, 6, 2),))
    result = validation_inventory(db, as_of_date=date(2026, 6, 30))
    assert result["signal_close"]["invalid_window_count"] == 1
    assert result["signal_close"]["validated_count"] == 0


def test_missing_calendar_is_unknown_not_immature(storage):
    db, _ = storage
    add_candidate(db, add_run(db, date(2026, 6, 29)))
    db.commit()
    result = validation_inventory(db, as_of_date=date(2026, 6, 30))
    assert result["signal_close"]["calendar_ready"] is False
    assert result["signal_close"]["immature_count"] == 0
    assert result["signal_close"]["unknown_maturity_count"] == 5


def test_inventory_is_bounded_but_counts_all_gaps(storage):
    db, _ = storage
    run = add_run(db, count=5)
    for i in range(5):
        add_candidate(db, run, f"{2300+i}.TW")
    proof(db)
    result = validation_inventory(db, as_of_date=date(2026, 6, 30), sample_limit=2)
    assert result["next_open"]["missing_outcome_count"] == 15
    assert len(result["next_open"]["gaps"]) == 2
    assert result["next_open"]["gaps_truncated"] is True


def test_readiness_endpoint_requires_auth_and_exact_date(storage, monkeypatch):
    db, _ = storage
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-readiness")
    app = FastAPI()
    app.include_router(readiness_router.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/internal/daily-radar/readiness?run_date=2026-06-30").status_code == 401
        headers = {"Authorization": "Bearer test-readiness"}
        assert client.get("/internal/daily-radar/readiness", headers=headers).status_code == 422
        result = client.get("/internal/daily-radar/readiness?run_date=2026-06-30", headers=headers)
        assert result.status_code == 200
        assert result.json()["requested_run_date"] == result.json()["effective_run_date"] == "2026-06-30"
        assert result.json()["scoring"]["ready"] is False


@pytest.mark.parametrize("status", ["already_broken_out", "already_invalidated"])
def test_legitimate_non_event_diagnostic_is_complete(storage, status):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate, status=status)
    proof(db)
    report = validation_inventory(db, as_of_date=date(2026, 6, 30))["signal_close"]
    assert report["missing_diagnostic_count"] == 0
    assert report["validated_count"] == 1


@pytest.mark.parametrize("data", [{}, {"return_basis": "next_open", "price_basis": "adjusted"},
    {"return_basis": "signal_close", "price_basis": "unadjusted_price"}])
def test_research_basis_mismatch_is_unresolved(storage, data):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate, diagnostic=False, version=RESEARCH_VALIDATION_VERSION)
    row = db.scalar(select(DailyRadarForwardValidationResult))
    row.outcome = data | {'validation_input': row.outcome['validation_input']}
    proof(db)
    report = validation_inventory(db, as_of_date=date(2026, 6, 30))["next_open"]
    assert report["validated_count"] == 0
    assert report["reasons"]["research_basis_mismatch"] == 1


def test_termination_reason_without_matching_lifecycle_evidence_is_not_terminal(storage):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate)
    row = db.scalar(select(DailyRadarForwardValidationResult))
    row.status, row.skip_reason, row.outcome = "skipped", "trading_terminated_in_window", {}
    proof(db)
    report = validation_inventory(db, as_of_date=date(2026, 6, 30))["signal_close"]
    assert report["terminal_count"] == 0
    assert report["retryable_count"] == 1
