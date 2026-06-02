from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun, StockRawData
from ai_stock_sentinel.db.session import Base, get_db


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


def _client() -> TestClient:
    app = FastAPI()

    @app.post("/test/internal/daily-radar")
    def protected_route(_: None = Depends(require_daily_radar_internal_auth)) -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_daily_radar_internal_auth_fails_closed_without_configured_token(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DAILY_RADAR_INTERNAL_TOKEN", raising=False)

    response = _client().post(
        "/test/internal/daily-radar",
        headers={"X-Internal-Token": "test-token"},
    )

    assert response.status_code == 503


def test_daily_radar_internal_auth_rejects_missing_token(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")

    response = _client().post("/test/internal/daily-radar")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_daily_radar_internal_auth_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")

    response = _client().post(
        "/test/internal/daily-radar",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 403


def test_daily_radar_internal_auth_accepts_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")

    response = _client().post(
        "/test/internal/daily-radar",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_daily_radar_internal_auth_accepts_x_internal_token(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")

    response = _client().post(
        "/test/internal/daily-radar",
        headers={"X-Internal-Token": "test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def _api_client(monkeypatch, db_session: Session, run: SimpleNamespace | None = None) -> TestClient:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    captured: dict[str, Any] = {}

    def fake_run_daily_radar(run_date: date, market: str, **kwargs: Any) -> SimpleNamespace:
        captured["run_date"] = run_date
        captured["market"] = market
        captured.update(kwargs)
        return run or _daily_radar_run(run_date=run_date, market=market)

    monkeypatch.setattr(daily_radar_router, "run_daily_radar", fake_run_daily_radar)
    monkeypatch.setattr(daily_radar_router, "_backend_today", lambda: date(2026, 6, 1))
    api.app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(api.app)
    client.captured_daily_radar_call = captured  # type: ignore[attr-defined]
    return client


def _daily_radar_run(
    *,
    run_date: date = date(2026, 6, 1),
    market: str = "TW",
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        run_date=run_date,
        market=market,
        status=status,
        universe_count=8,
        prefilter_count=4,
        candidate_count=3,
        errors=[{"code": "prefilter_rejected", "symbol": "9999.TW"}],
        started_at=datetime(2026, 6, 1, 1, 2, 3, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 1, 1, 2, 4, tzinfo=timezone.utc),
    )


def _persist_raw_data(
    session: Session,
    *,
    symbol: str = "2330.TW",
    record_date: date = date(2026, 6, 1),
    is_final: bool = True,
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
    session.commit()
    return row


def test_daily_radar_run_endpoint_accepts_authenticated_explicit_run_date(monkeypatch, daily_radar_db_session: Session) -> None:
    raw_row = _persist_raw_data(daily_radar_db_session, record_date=date(2026, 5, 29))
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-05-29", "market": "US"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    captured = client.captured_daily_radar_call  # type: ignore[attr-defined]
    assert captured["run_date"] == date(2026, 5, 29)
    assert captured["market"] == "US"
    assert captured["session"] is daily_radar_db_session
    assert captured["cache_rows"] == [raw_row]
    assert captured["market_context"] == {}
    assert captured["allow_fixture_fallback"] is False
    assert response.json() == {
        "run_id": 42,
        "run_date": "2026-05-29",
        "market": "US",
        "status": "completed",
        "universe_count": 8,
        "prefilter_count": 4,
        "candidate_count": 3,
        "errors": [{"code": "prefilter_rejected", "symbol": "9999.TW"}],
        "started_at": "2026-06-01T01:02:03Z",
        "finished_at": "2026-06-01T01:02:04Z",
    }


def test_daily_radar_run_endpoint_defaults_body_to_backend_today_and_tw(monkeypatch, daily_radar_db_session: Session) -> None:
    _persist_raw_data(daily_radar_db_session, record_date=date(2026, 6, 1))
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/run",
            headers={"X-Internal-Token": "test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert client.captured_daily_radar_call["run_date"] == date(2026, 6, 1)  # type: ignore[attr-defined]
    assert client.captured_daily_radar_call["market"] == "TW"  # type: ignore[attr-defined]
    assert response.json()["run_date"] == "2026-06-01"
    assert response.json()["market"] == "TW"


def test_daily_radar_run_endpoint_preserves_stale_data_status(monkeypatch, daily_radar_db_session: Session) -> None:
    _persist_raw_data(daily_radar_db_session, record_date=date(2026, 6, 1))
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        run=_daily_radar_run(status="stale_data"),
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["status"] == "stale_data"


def test_daily_radar_run_endpoint_rejects_missing_token_on_real_route(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")

    response = TestClient(api.app).post("/internal/daily-radar/run")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.fixture()
def daily_radar_db_session() -> Session:
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
        tables=[DailyRadarRun.__table__, DailyRadarCandidate.__table__, StockRawData.__table__],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture()
def public_daily_radar_client(daily_radar_db_session: Session, monkeypatch) -> TestClient:
    monkeypatch.delenv("DAILY_RADAR_INTERNAL_TOKEN", raising=False)
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    monkeypatch.setattr(daily_radar_router, "_backend_today", lambda: date(2026, 6, 3))
    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(get_db, None)


def _persist_daily_radar_run(
    session: Session,
    *,
    run_date: date,
    status: str = "completed",
    market: str = "TW",
    created_at: datetime | None = None,
) -> DailyRadarRun:
    created = created_at or datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    run = DailyRadarRun(
        run_date=run_date,
        market=market,
        status=status,
        started_at=created,
        finished_at=created if status != "running" else None,
        universe_count=8,
        prefilter_count=4,
        candidate_count=0,
        errors=[] if status in {"completed", "stale_data"} else [{"code": "fixture_status"}],
        created_at=created,
    )
    session.add(run)
    session.flush()
    return run


def _persist_daily_radar_candidate(
    session: Session,
    run: DailyRadarRun,
    *,
    symbol: str,
    score: int,
    primary_bucket: str = "institutional_accumulation",
    secondary_buckets: list[str] | None = None,
) -> DailyRadarCandidate:
    candidate = DailyRadarCandidate(
        run_id=run.id,
        symbol=symbol,
        name=f"{symbol} fixture",
        primary_bucket=primary_bucket,
        secondary_buckets=secondary_buckets or ["price_volume_strengthening"],
        observation_score=score,
        bucket_scores={primary_bucket: score, "price_volume_strengthening": max(score - 10, 0)},
        risk_labels=["data_gap"] if score < 80 else [],
        matched_rules=[
            {
                "rule_id": "fixture_rule",
                "label": "Fixture observation rule",
                "details": {"score": score},
            }
        ],
        explanation="Rule-based observation summary for public API tests.",
        repeat_status="new",
        score_breakdown={"observation_score": score, "bucket_scores": {primary_bucket: score}},
        input_snapshot={
            "symbol": symbol,
            "close": 980,
            "market_context": {"index_symbol": "TAIEX", "trend_state": "above_ma20"},
        },
        data_dates={"ohlcv": run.run_date.isoformat(), "institutional_flow": run.run_date.isoformat()},
    )
    session.add(candidate)
    session.flush()
    run.candidate_count = len(run.candidates)
    session.flush()
    return candidate


def test_daily_radar_run_endpoint_returns_409_without_final_raw_rows_and_does_not_call_service(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    called = False

    def fake_run_daily_radar(*args: Any, **kwargs: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(daily_radar_router, "run_daily_radar", fake_run_daily_radar)
    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 409
    assert response.json() == {"detail": "No final StockRawData rows are available for 2026-06-01."}
    assert called is False


def test_public_latest_daily_radar_returns_latest_completed_run_without_internal_token(
    public_daily_radar_client: TestClient,
    daily_radar_db_session: Session,
) -> None:
    older = _persist_daily_radar_run(
        daily_radar_db_session,
        run_date=date(2026, 6, 1),
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
    )
    _persist_daily_radar_candidate(daily_radar_db_session, older, symbol="2330.TW", score=82)
    latest = _persist_daily_radar_run(
        daily_radar_db_session,
        run_date=date(2026, 6, 2),
        created_at=datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc),
    )
    _persist_daily_radar_candidate(daily_radar_db_session, latest, symbol="2454.TW", score=88)
    _persist_daily_radar_candidate(daily_radar_db_session, latest, symbol="2317.TW", score=93)
    _persist_daily_radar_run(
        daily_radar_db_session,
        run_date=date(2026, 6, 3),
        status="running",
        created_at=datetime(2026, 6, 3, 9, 0, tzinfo=timezone.utc),
    )
    daily_radar_db_session.commit()

    response = public_daily_radar_client.get("/daily-radar/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_date"] == "2026-06-02"
    assert payload["status"] == "completed"
    assert payload["data_dates"] == {"ohlcv": "2026-06-02", "institutional_flow": "2026-06-02"}
    assert payload["market_context"] == {"index_symbol": "TAIEX", "trend_state": "above_ma20"}
    assert [candidate["symbol"] for candidate in payload["candidates"]] == ["2317.TW", "2454.TW"]
    first = payload["candidates"][0]
    assert first["bucket_scores"] == {"institutional_accumulation": 93, "price_volume_strengthening": 83}
    assert first["input_snapshot"]["symbol"] == "2317.TW"
    assert first["data_dates"]["ohlcv"] == "2026-06-02"


def test_public_daily_radar_by_date_uses_latest_public_run_and_bucket_limit_filters(
    public_daily_radar_client: TestClient,
    daily_radar_db_session: Session,
) -> None:
    _persist_daily_radar_run(
        daily_radar_db_session,
        run_date=date(2026, 6, 1),
        status="failed",
        created_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
    )
    stale = _persist_daily_radar_run(
        daily_radar_db_session,
        run_date=date(2026, 6, 1),
        status="stale_data",
        created_at=datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc),
    )
    _persist_daily_radar_candidate(
        daily_radar_db_session,
        stale,
        symbol="3034.TW",
        score=81,
        primary_bucket="support_retest",
        secondary_buckets=[],
    )
    _persist_daily_radar_candidate(daily_radar_db_session, stale, symbol="2303.TW", score=91)
    daily_radar_db_session.commit()

    response = public_daily_radar_client.get("/daily-radar/2026-06-01?bucket=support_retest&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_date"] == "2026-06-01"
    assert payload["status"] == "stale_data"
    assert [candidate["symbol"] for candidate in payload["candidates"]] == ["3034.TW"]


def test_public_daily_radar_symbol_history_returns_recent_public_candidates_with_limit_and_bucket(
    public_daily_radar_client: TestClient,
    daily_radar_db_session: Session,
) -> None:
    older = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 5, 30), status="completed")
    newer = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 2), status="stale_data")
    ignored = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 1), status="failed")
    _persist_daily_radar_candidate(daily_radar_db_session, older, symbol="2330.TW", score=72)
    _persist_daily_radar_candidate(daily_radar_db_session, newer, symbol="2330.TW", score=86)
    other_symbol_run = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 2), status="completed")
    _persist_daily_radar_candidate(daily_radar_db_session, other_symbol_run, symbol="2454.TW", score=90)
    _persist_daily_radar_candidate(daily_radar_db_session, ignored, symbol="2330.TW", score=99)
    daily_radar_db_session.commit()

    response = public_daily_radar_client.get(
        "/daily-radar/symbol/2330.TW?bucket=institutional_accumulation&limit=1"
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "symbol": "2330.TW",
            "name": "2330.TW fixture",
            "record_date": "2026-06-02",
            "primary_bucket": "institutional_accumulation",
            "secondary_buckets": ["price_volume_strengthening"],
            "observation_score": 86,
            "risk_labels": [],
            "repeat_status": "new",
            "bucket_scores": {"institutional_accumulation": 86, "price_volume_strengthening": 76},
            "matched_rules": [
                {
                    "rule_id": "fixture_rule",
                    "label": "Fixture observation rule",
                    "details": {"score": 86},
                }
            ],
            "score_breakdown": {"observation_score": 86, "bucket_scores": {"institutional_accumulation": 86}},
            "input_snapshot": {
                "symbol": "2330.TW",
                "close": 980,
                "market_context": {"index_symbol": "TAIEX", "trend_state": "above_ma20"},
            },
            "data_dates": {"ohlcv": "2026-06-02", "institutional_flow": "2026-06-02"},
        }
    ]


def test_public_daily_radar_no_data_cases_are_explicit_and_do_not_require_internal_token(
    public_daily_radar_client: TestClient,
) -> None:
    latest_response = public_daily_radar_client.get("/daily-radar/latest")
    date_response = public_daily_radar_client.get("/daily-radar/2026-06-01")
    history_response = public_daily_radar_client.get("/daily-radar/symbol/2330.TW")

    assert latest_response.status_code == 404
    assert latest_response.json() == {"detail": "No public Daily Radar run is available."}
    assert date_response.status_code == 404
    assert date_response.json() == {"detail": "No public Daily Radar run is available for 2026-06-01."}
    assert history_response.status_code == 200
    assert history_response.json() == []
