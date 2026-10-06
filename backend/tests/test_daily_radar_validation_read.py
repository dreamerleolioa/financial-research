from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.daily_radar import dependencies, read_router
from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.db.models import (
    DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult,
    DailyRadarPreparedRun, StockRawData,
)
from ai_stock_sentinel.db.session import Base, get_db


@compiles(JSONB, "sqlite")
def _jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def storage(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (
        DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult,
        DailyRadarPreparedRun, StockRawData,
    )])
    monkeypatch.setattr(dependencies, "_backend_today", lambda: date(2026, 6, 30))
    with Session(engine) as session:
        app = FastAPI()
        app.include_router(read_router.router)
        app.dependency_overrides[get_db] = lambda: session
        with TestClient(app) as client:
            yield session, client, engine
    engine.dispose()


def add_run(session, day=date(2026, 6, 1), *, count=1, market="TW", status="completed"):
    run = DailyRadarRun(run_date=day, market=market, status=status,
        started_at=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
        universe_count=count, prefilter_count=count, candidate_count=count, errors=[])
    session.add(run)
    session.flush()
    return run


def add_candidate(session, run, symbol="2330.TW", *, score=90, selection="v1", status="selected"):
    row = DailyRadarCandidate(run_id=run.id, symbol=symbol, name="測試股票",
        primary_bucket="support_retest", secondary_buckets=[], observation_score=score,
        selection_status=status, bucket_scores={}, risk_labels=[], matched_rules=[],
        explanation="觀察", repeat_status="new", score_breakdown={}, data_dates={},
        input_snapshot={"versions": {"scoring_version": "s1", "rule_version": "r1", "config_version": "c1"},
                        "selection_version": selection, "secret": "DO_NOT_EXPOSE"})
    session.add(row)
    session.flush()
    return row


def add_result(session, row, *, status="confirmed", diagnostic=True, window=5, version=FORWARD_VALIDATION_VERSION):
    payload = {"forward_return_pct": 2, "secret": "DO_NOT_EXPOSE"}
    if diagnostic:
        payload["observation_diagnostic"] = {
            "version": "daily-radar-observation-v1", "status": status,
            "lead_trading_days": 3 if status == "confirmed" else None,
            "waiting_max_adverse_excursion_pct": -2,
        }
    session.add(DailyRadarForwardValidationResult(candidate_id=row.id, window_days=window,
        validation_version=version, status="validated", signal_date=row.run.run_date,
        target_date=date(2026, 6, 8), benchmark_symbol="TAIEX",
        evaluation_as_of_date=date(2026, 6, 8), outcome=payload))


def add_calendar(session):
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(30)
            if (date(2026, 6, 1) + timedelta(days=i)).weekday() < 5]
    session.add(DailyRadarPreparedRun(run_date=date(2026, 6, 30), market="TW", status="prepared",
        selected_symbols=[], universe=[], symbol_count=0, step_statuses={}, errors=[],
        market_context={"benchmark": {"symbol": "TAIEX", "price_history": [
            {"date": day.isoformat(), "close": 1000} for day in days]}}))


def groups(body, window="5", cohort=0):
    return body["cohorts"][cohort]["windows"][window]


def test_empty_read_is_200_without_any_write(storage):
    session, client, engine = storage
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, cu, sql, p, ctx, many: statements.append(sql))
    response = client.get("/daily-radar/validation")
    assert response.status_code == 200
    assert response.json()["cohorts"] == []
    assert response.json()["default_cohort_id"] is None
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
    assert not session.new and not session.dirty


def test_read_keeps_full_daily_ranks_and_returns_only_aggregate_public_fields(storage):
    session, client, _ = storage
    run = add_run(session, count=6)
    rows = [add_candidate(session, run, str(i) + ".TW", score=100-i) for i in range(1, 7)]
    for row in rows[1:]:
        add_result(session, row)
    add_calendar(session)
    session.commit()
    response = client.get("/daily-radar/validation")
    assert response.status_code == 200
    body = response.json()
    top = groups(body)["top_3"]
    assert top["selected_count"] == 3
    assert top["evaluated_observation_count"] == 2
    assert top["missing_outcome_count"] == 1
    assert top["confirmation_rate"] is None
    assert groups(body)["remaining_after_3"]["confirmation_rate"] == 1
    assert groups(body)["top_5"]["selected_count"] == 5
    assert body["last_evaluated_date"] == "2026-06-08"
    assert body["calendar_through_date"] == "2026-06-30"
    assert not any(term in response.text for term in ("candidate_ids", "DO_NOT_EXPOSE", "input_snapshot", "forward_return_pct"))


def test_cohorts_are_separate_and_default_to_latest_signal_strategy(storage):
    session, client, _ = storage
    old = add_candidate(session, add_run(session), selection="z-old")
    add_result(session, old)
    new = add_candidate(session, add_run(session, date(2026, 6, 29)), selection="a-new")
    add_calendar(session)
    session.commit()
    body = client.get("/daily-radar/validation").json()
    assert len(body["cohorts"]) == 2
    latest = next(c for c in body["cohorts"] if c["id"] == body["default_cohort_id"])
    assert latest["strategy"]["selection_version"] == "a-new"
    assert latest["windows"]["5"]["all_selected"]["immature_observation_count"] == 1
    assert latest["windows"]["5"]["all_selected"]["confirmation_rate"] is None


def test_legacy_diagnostics_are_missing_not_zero_and_unknown_calendar_is_disclosed(storage):
    session, client, _ = storage
    row = add_candidate(session, add_run(session))
    add_result(session, row, diagnostic=False)
    session.commit()
    body = client.get("/daily-radar/validation").json()
    assert body["calendar_through_date"] is None
    summary = groups(body)["all_selected"]
    assert summary["missing_diagnostic_count"] == 1
    assert summary["confirmation_rate"] is None
    assert summary["evaluated_observation_count"] == 0


def test_partial_calendar_cannot_hide_an_already_saved_mature_observation(storage):
    session, client, _ = storage
    row = add_candidate(session, add_run(session))
    add_result(session, row)
    session.add(DailyRadarPreparedRun(run_date=date(2026, 6, 30), market="TW", status="prepared",
        selected_symbols=[], universe=[], symbol_count=0, step_statuses={}, errors=[],
        market_context={"benchmark": {"symbol": "TAIEX", "price_history": [
            {"date": "2026-06-01", "close": 1000}, {"date": "2026-06-08", "close": 1000}]}}))
    session.commit()
    body = client.get("/daily-radar/validation").json()
    assert body["calendar_through_date"] is None
    assert groups(body)["all_selected"]["evaluated_observation_count"] == 1
    assert groups(body)["all_selected"]["immature_observation_count"] == 0
    assert groups(body)["all_selected"]["confirmation_rate"] == 1


def test_read_does_not_flush_pending_work_or_include_future_or_wrong_identity_results(storage):
    session, client, engine = storage
    row = add_candidate(session, add_run(session))
    add_result(session, row)
    session.flush()
    result = session.query(DailyRadarForwardValidationResult).one()
    result.evaluation_as_of_date = date(2026, 7, 1)
    add_calendar(session)
    session.commit()
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, cu, sql, p, ctx, many: statements.append(sql))
    pending = DailyRadarRun(run_date=date(2026, 7, 2), market="TW", status="running",
        started_at=datetime.now(timezone.utc), candidate_count=0, universe_count=0, prefilter_count=0, errors=[])
    session.add(pending)
    body = client.get("/daily-radar/validation").json()
    assert groups(body)["all_selected"]["missing_outcome_count"] == 1
    assert body["last_evaluated_date"] is None
    assert pending in session.new and pending.id is None
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
    session.expunge(pending)
    result.evaluation_as_of_date = date(2026, 6, 8)
    result.benchmark_symbol = "WRONG"
    session.commit()
    body = client.get("/daily-radar/validation").json()
    assert groups(body)["all_selected"]["missing_outcome_count"] == 1
    assert groups(body)["all_selected"]["confirmation_rate"] is None


def test_repeat_origin_before_interval_is_not_counted_again(storage):
    session, client, _ = storage
    add_candidate(session, add_run(session, date(2026, 5, 1)))
    add_candidate(session, add_run(session, date(2026, 6, 29)))
    add_calendar(session)
    session.commit()
    body = client.get("/daily-radar/validation?lookback_days=30").json()
    summary = groups(body)["all_selected"]
    assert summary["excluded_repeat_count"] == 1
    assert summary["evaluated_observation_count"] == 0
    assert summary["immature_observation_count"] == 0


def test_latest_empty_revision_shadow_failed_other_market_and_future_are_excluded(storage):
    session, client, _ = storage
    old = add_candidate(session, add_run(session))
    add_result(session, old)
    add_run(session, count=0)
    add_candidate(session, add_run(session, date(2026, 6, 2), count=0), status="shadow")
    add_candidate(session, add_run(session, date(2026, 6, 3), status="failed"))
    add_candidate(session, add_run(session, date(2026, 6, 4), market="OTHER"))
    add_candidate(session, add_run(session, date(2026, 7, 1)))
    session.commit()
    body = client.get("/daily-radar/validation").json()
    assert body["cohorts"] == []


@pytest.mark.parametrize("query", ["lookback_days=0", "lookback_days=366", "lookback_days=bad"])
def test_read_bounds_sample_size(storage, query):
    assert storage[1].get("/daily-radar/validation?" + query).status_code == 422
