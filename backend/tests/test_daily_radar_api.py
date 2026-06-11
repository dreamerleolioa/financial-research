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
from ai_stock_sentinel.daily_radar.background_context import BackgroundContextPayload
from ai_stock_sentinel.daily_radar.repository import upsert_shared_background_context
from ai_stock_sentinel.daily_radar.universe import InstitutionalLeaderRow
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun, SharedBackgroundContext, StockRawData
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


class FakeUniverseProvider:
    def __init__(
        self,
        *,
        same_day: list[InstitutionalLeaderRow] | None = None,
        recent: list[InstitutionalLeaderRow] | None = None,
    ) -> None:
        self.same_day = same_day if same_day is not None else [InstitutionalLeaderRow("2330.TW", 1, 91.0)]
        self.recent = recent if recent is not None else []
        self.calls: list[dict[str, Any]] = []

    def same_day_institutional_leaders(self, *, run_date: date, market: str, limit: int) -> list[InstitutionalLeaderRow]:
        self.calls.append({"track": "same_day", "run_date": run_date, "market": market, "limit": limit})
        return self.same_day[:limit]

    def recent_accumulation_leaders(self, *, run_date: date, market: str, limit: int) -> list[InstitutionalLeaderRow]:
        self.calls.append({"track": "recent", "run_date": run_date, "market": market, "limit": limit})
        return self.recent[:limit]


class FakeBatchTechnicalFetcher:
    def __init__(self, payloads: dict[str, dict[str, Any]] | None = None) -> None:
        self.payloads = payloads or {}
        self.calls: list[tuple[list[str], date]] = []

    def fetch(self, symbols: list[str], *, run_date: date) -> dict[str, dict[str, Any]]:
        self.calls.append((list(symbols), run_date))
        return {symbol: self.payloads.get(symbol) or _technical_payload(symbol, run_date) for symbol in symbols}


class FakeMarketIndexContextProvider:
    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.context = context or _market_context()
        self.calls: list[dict[str, Any]] = []

    def build(self, *, run_date: date, market: str) -> dict[str, Any]:
        self.calls.append({"run_date": run_date, "market": market})
        return dict(self.context)


class RaisingUniverseProvider(FakeUniverseProvider):
    def __init__(self, message: str = "simulated FinMind outage") -> None:
        super().__init__()
        self.message = message

    def same_day_institutional_leaders(self, *, run_date: date, market: str, limit: int) -> list[InstitutionalLeaderRow]:
        raise RuntimeError(self.message)


class RaisingBatchTechnicalFetcher(FakeBatchTechnicalFetcher):
    def fetch(self, symbols: list[str], *, run_date: date) -> dict[str, dict[str, Any]]:
        self.calls.append((list(symbols), run_date))
        raise RuntimeError("simulated yfinance outage")


class FakeBackgroundChipContextProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> list[BackgroundContextPayload]:
        self.calls.append(
            {
                "symbols": list(symbols),
                "context_types": list(context_types),
                "run_date": run_date,
                "market": market,
            }
        )
        return [
            BackgroundContextPayload(
                symbol=symbol,
                context_type=context_type,
                applicable_consumers=("daily_radar",),
                source={"domain": "background_context", "provider": "fixture_provider"},
                as_of_date=run_date,
                freshness="fresh",
                payload={"label": f"{context_type}_fixture"},
                missing_reason=None,
                replay_key=f"background_context:{symbol}:{context_type}:{run_date.isoformat()}",
            )
            for symbol in symbols
            for context_type in context_types
        ]


class RaisingBackgroundChipContextProvider:
    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> list[BackgroundContextPayload]:
        raise RuntimeError("simulated chip context outage")


def _clear_daily_radar_api_overrides() -> None:
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    for dependency in (
        get_db,
        daily_radar_router.get_daily_radar_universe_provider,
        daily_radar_router.get_daily_radar_technical_fetcher,
        daily_radar_router.get_daily_radar_market_context_provider,
        daily_radar_router.get_daily_radar_background_chip_context_provider,
    ):
        api.app.dependency_overrides.pop(dependency, None)


def _api_client(
    monkeypatch,
    db_session: Session,
    run: SimpleNamespace | None = None,
    *,
    universe_provider: FakeUniverseProvider | None = None,
    technical_fetcher: FakeBatchTechnicalFetcher | None = None,
    market_context_provider: FakeMarketIndexContextProvider | None = None,
    raise_server_exceptions: bool = True,
    run_error: Exception | None = None,
) -> TestClient:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    captured: dict[str, Any] = {}
    provider = universe_provider or FakeUniverseProvider()
    fetcher = technical_fetcher or FakeBatchTechnicalFetcher()
    context_provider = market_context_provider or FakeMarketIndexContextProvider()

    def fake_run_daily_radar(run_date: date, market: str, **kwargs: Any) -> SimpleNamespace:
        captured["run_date"] = run_date
        captured["market"] = market
        captured.update(kwargs)
        if run_error is not None:
            raise run_error
        return run or _daily_radar_run(run_date=run_date, market=market)

    monkeypatch.setattr(daily_radar_router, "run_daily_radar", fake_run_daily_radar)
    monkeypatch.setattr(daily_radar_router, "_backend_today", lambda: date(2026, 6, 1))
    api.app.dependency_overrides[get_db] = lambda: db_session
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_universe_provider] = lambda: provider
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_technical_fetcher] = lambda: fetcher
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_market_context_provider] = lambda: context_provider
    client = TestClient(api.app, raise_server_exceptions=raise_server_exceptions)
    client.captured_daily_radar_call = captured  # type: ignore[attr-defined]
    client.fake_universe_provider = provider  # type: ignore[attr-defined]
    client.fake_technical_fetcher = fetcher  # type: ignore[attr-defined]
    client.fake_market_context_provider = context_provider  # type: ignore[attr-defined]
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


def _technical_payload(symbol: str, run_date: date) -> dict[str, Any]:
    return {
        "name": f"{symbol} fixture",
        "ohlcv": {
            "open": 100.0,
            "high": 108.0,
            "low": 98.0,
            "close": 106.0,
            "previous_close": 102.0,
            "volume": 4_000_000,
            "avg_volume_20": 2_000_000,
        },
        "indicators": {
            "ma5": 104.0,
            "ma20": 101.0,
            "ma60": 96.0,
            "rsi14": 62.0,
            "bias20": 4.95,
            "volume_ratio": 2.0,
            "missing_trading_days_60": 0,
            "mfi14": 64.0,
            "macd": 1.2,
            "macd_signal": 0.8,
            "macd_histogram": 0.4,
            "kd_k": 72.0,
            "kd_d": 65.0,
            "atr14": 3.2,
            "support_level": 95.0,
            "resistance_level": 110.0,
            "obv": 12_000_000,
            "obv_trend": "rising",
        },
        "data_dates": {"ohlcv": run_date.isoformat(), "technical_indicators": run_date.isoformat()},
    }


def _market_context() -> dict[str, Any]:
    return {
        "record_date": "2026-05-29",
        "data_dates": {"market_index": "2026-05-29"},
        "market": {
            "index_symbol": "TAIEX",
            "yfinance_symbol": "^TWII",
            "regime": "constructive",
            "freshness": "fresh",
            "data_date": "2026-05-29",
            "close": 21872.0,
            "previous_close": 21640.0,
            "ma20": 21480.0,
            "ma60": 20920.0,
            "above_ma20": True,
            "above_ma60": True,
            "volatility_state": "normal",
            "market_risk_flags": [],
        },
    }


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
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    captured = client.captured_daily_radar_call  # type: ignore[attr-defined]
    assert captured["run_date"] == date(2026, 5, 29)
    assert captured["market"] == "US"
    assert captured["session"] is daily_radar_db_session
    assert captured["cache_rows"] == [raw_row]
    assert client.fake_market_context_provider.calls == [{"run_date": date(2026, 5, 29), "market": "US"}]  # type: ignore[attr-defined]
    assert captured["market_context"] == _market_context()
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


def test_daily_radar_run_endpoint_reads_background_context_cache_without_provider_calls(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    raw_row = _persist_raw_data(daily_radar_db_session, record_date=date(2026, 5, 29))
    upsert_shared_background_context(
        daily_radar_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture_cache"},
        as_of_date=date(2026, 5, 25),
        freshness="fresh",
        payload={"top_holders_stable": True},
        replay_key="background_context:2330.TW:weekly_major_holders:2026-05-25",
    )
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    api.app.dependency_overrides[daily_radar_router.get_daily_radar_background_chip_context_provider] = (
        lambda: RaisingBackgroundChipContextProvider()
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-05-29", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    captured = client.captured_daily_radar_call  # type: ignore[attr-defined]
    assert captured["cache_rows"] == [raw_row]
    contexts = captured["background_contexts_by_symbol"]["2330.TW"]
    weekly_context = next(context for context in contexts if context["context_type"] == "weekly_major_holders")
    assert weekly_context["freshness"] == "fresh"
    assert weekly_context["payload"] == {"top_holders_stable": True}
    missing_context = next(context for context in contexts if context["context_type"] == "lending")
    assert missing_context["freshness"] == "missing"
    assert missing_context["missing_reason"] == "context_cache_missing"


def test_daily_radar_run_endpoint_defaults_body_to_backend_today_and_tw(monkeypatch, daily_radar_db_session: Session) -> None:
    _persist_raw_data(daily_radar_db_session, record_date=date(2026, 6, 1))
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/run",
            headers={"X-Internal-Token": "test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

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
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "stale_data"


def test_daily_radar_run_endpoint_rejects_missing_token_on_real_route(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")

    response = TestClient(api.app).post("/internal/daily-radar/run")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_daily_radar_chip_context_update_endpoint_requires_internal_auth(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")

    response = TestClient(api.app).post("/internal/daily-radar/chip-context/update")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_daily_radar_chip_context_update_endpoint_writes_cache_records(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    provider = FakeBackgroundChipContextProvider()
    monkeypatch.setattr(daily_radar_router, "_backend_today", lambda: date(2026, 6, 2))
    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_background_chip_context_provider] = lambda: provider

    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/chip-context/update",
            json={
                "run_date": "2026-06-02",
                "market": "TW",
                "symbols": ["2330.TW", "2330.TW", "2454.TW"],
                "context_types": ["weekly_major_holders", "lending"],
            },
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert response.json() == {
        "status": "completed",
        "run_date": "2026-06-02",
        "market": "TW",
        "symbol_count": 2,
        "context_types": ["weekly_major_holders", "lending"],
        "records_written": 4,
        "errors": [],
    }
    assert provider.calls == [
        {
            "symbols": ["2330.TW", "2454.TW"],
            "context_types": ["weekly_major_holders", "lending"],
            "run_date": date(2026, 6, 2),
            "market": "TW",
        }
    ]
    rows = daily_radar_db_session.query(SharedBackgroundContext).all()
    assert {(row.symbol, row.context_type, row.freshness) for row in rows} == {
        ("2330.TW", "weekly_major_holders", "fresh"),
        ("2330.TW", "lending", "fresh"),
        ("2454.TW", "weekly_major_holders", "fresh"),
        ("2454.TW", "lending", "fresh"),
    }


def test_daily_radar_chip_context_update_endpoint_records_provider_failure(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_background_chip_context_provider] = (
        lambda: RaisingBackgroundChipContextProvider()
    )

    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/chip-context/update",
            json={"run_date": "2026-06-02", "symbols": ["2330.TW"]},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["records_written"] == 0
    assert body["errors"][0]["code"] == "background_context_provider_failed"


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
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            SharedBackgroundContext.__table__,
            StockRawData.__table__,
        ],
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
        _clear_daily_radar_api_overrides()


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


def test_daily_radar_run_endpoint_backfills_missing_selected_rows_and_passes_cache_rows(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    fetcher = FakeBatchTechnicalFetcher()
    provider = FakeUniverseProvider(
        same_day=[
            InstitutionalLeaderRow(
                "2330.TW",
                1,
                91.0,
                actor="foreign",
                net_buy=24_680.0,
                concentration=0.11,
                source_dates=("2026-06-01",),
            )
        ],
        recent=[
            InstitutionalLeaderRow(
                "2317.TW",
                1,
                1_000_000.5,
                actor="institutional",
                cumulative_net_buy=12_345.0,
                concentration=0.18,
                consecutive_buy_days=4,
                source_dates=("2026-05-27", "2026-05-28", "2026-05-29", "2026-06-01"),
                flow_state="consistent_accumulation",
            )
        ],
    )
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        universe_provider=provider,
        technical_fetcher=fetcher,
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert fetcher.calls == [(["2330.TW", "2317.TW"], date(2026, 6, 1))]
    captured = client.captured_daily_radar_call  # type: ignore[attr-defined]
    assert [row.symbol for row in captured["cache_rows"]] == ["2330.TW", "2317.TW"]
    assert captured["cache_rows"][0].technical["ohlcv"]["close"] == 106.0
    same_day_institutional = captured["cache_rows"][0].institutional
    assert same_day_institutional["same_day_actor"] == "foreign"
    assert same_day_institutional["same_day_net_buy"] == pytest.approx(24_680.0)
    assert same_day_institutional["foreign_net_shares"] == pytest.approx(24_680.0)
    assert "investment_trust_net_shares" not in same_day_institutional
    recent_institutional = captured["cache_rows"][1].institutional
    assert recent_institutional["flow_label"] == "institutional_accumulation"
    assert recent_institutional["flow_state"] == "consistent_accumulation"
    assert recent_institutional["institutional_universe_tracks"] == ["recent_accumulation"]
    assert recent_institutional["recent_accumulation_rank"] == 1
    assert recent_institutional["consecutive_buy_days"] == 4
    assert recent_institutional["consecutive_positive_days"] == 4
    assert recent_institutional["cumulative_net_buy"] == pytest.approx(12_345.0)
    assert recent_institutional["net_buy_cumulative"] == pytest.approx(12_345.0)
    assert recent_institutional["three_party_net_shares"] == pytest.approx(12_345.0)
    assert recent_institutional["recent_concentration"] == pytest.approx(0.18)
    assert recent_institutional["net_flow_to_avg_volume"] == pytest.approx(0.18)
    assert recent_institutional["recent_source_dates"] == ["2026-05-27", "2026-05-28", "2026-05-29", "2026-06-01"]
    assert recent_institutional["data_dates"]["institutional_flow"] == "2026-06-01"
    assert "foreign_net_cumulative" not in recent_institutional
    assert "foreign_net_shares" not in recent_institutional
    assert "investment_trust_net_shares" not in recent_institutional


def test_daily_radar_run_endpoint_fetches_all_missing_selected_symbols_in_one_batch(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    _persist_raw_data(daily_radar_db_session, symbol="2330.TW", record_date=date(2026, 6, 1))
    fetcher = FakeBatchTechnicalFetcher()
    provider = FakeUniverseProvider(
        same_day=[
            InstitutionalLeaderRow(
                "2330.TW",
                1,
                91.0,
                actor="foreign",
                net_buy=24_680.0,
                concentration=0.11,
                source_dates=("2026-06-01",),
            ),
            InstitutionalLeaderRow("2454.TW", 2, 82.0),
        ],
        recent=[InstitutionalLeaderRow("2317.TW", 1, 1_000_000.5)],
    )
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        universe_provider=provider,
        technical_fetcher=fetcher,
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert fetcher.calls == [(["2454.TW", "2317.TW"], date(2026, 6, 1))]
    assert [row.symbol for row in client.captured_daily_radar_call["cache_rows"]] == [  # type: ignore[attr-defined]
        "2330.TW",
        "2454.TW",
        "2317.TW",
    ]
    cached_institutional = client.captured_daily_radar_call["cache_rows"][0].institutional  # type: ignore[attr-defined]
    assert cached_institutional["same_day_actor"] == "foreign"
    assert cached_institutional["same_day_net_buy"] == pytest.approx(24_680.0)
    assert cached_institutional["foreign_net_shares"] == pytest.approx(24_680.0)
    assert cached_institutional["institutional_flow"]["same_day_net_buy"] == pytest.approx(24_680.0)
    backfilled_institutional = client.captured_daily_radar_call["cache_rows"][1].institutional  # type: ignore[attr-defined]
    assert "technical_record_missing" not in {
        metrics.get("reason")
        for metrics in backfilled_institutional["universe_track_metrics"].values()
        if isinstance(metrics, dict)
    }


def test_daily_radar_run_endpoint_adds_local_cache_daily_trigger_tracks_without_extra_fetch(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    run_date = date(2026, 6, 1)
    daily_radar_db_session.add(
        StockRawData(
            symbol="2454.TW",
            record_date=run_date,
            technical=_technical_payload("2454.TW", run_date),
            institutional={},
            fundamental={"margin": {}},
            raw_data_is_final=True,
        )
    )
    daily_radar_db_session.commit()
    fetcher = FakeBatchTechnicalFetcher()
    provider = FakeUniverseProvider(same_day=[InstitutionalLeaderRow("2330.TW", 1, 91.0)], recent=[])
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        universe_provider=provider,
        technical_fetcher=fetcher,
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert fetcher.calls == [(["2330.TW"], run_date)]
    captured_rows = client.captured_daily_radar_call["cache_rows"]  # type: ignore[attr-defined]
    assert [row.symbol for row in captured_rows] == ["2330.TW", "2454.TW"]
    trigger_payload = captured_rows[1].institutional
    assert trigger_payload["universe_primary_track"] == "price_volume"
    assert trigger_payload["institutional_universe_tracks"] == ["price_volume"]
    assert trigger_payload["flow_state"] == "technical_trigger"
    assert trigger_payload["universe_track_metrics"]["price_volume"]["matched"] is True
    assert trigger_payload["universe_track_metrics"]["support_retake"]["matched"] is False
    assert trigger_payload["scores"]["price_volume"] > 0


def test_daily_radar_run_endpoint_returns_409_for_empty_universe_and_does_not_call_service(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    provider = FakeUniverseProvider(same_day=[], recent=[])
    fetcher = FakeBatchTechnicalFetcher()
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        universe_provider=provider,
        technical_fetcher=fetcher,
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 409
    assert response.json() == {"detail": "Daily Radar universe is empty for TW on 2026-06-01."}
    assert client.captured_daily_radar_call == {}  # type: ignore[attr-defined]
    assert fetcher.calls == []


def test_daily_radar_run_endpoint_returns_json_503_when_universe_provider_fails(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    sensitive_url = "https://api.finmindtrade.com/api/v4/data?token=secret-token"
    provider = RaisingUniverseProvider(sensitive_url)
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        universe_provider=provider,
        raise_server_exceptions=False,
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": {
            "code": "daily_radar_run_failed",
            "message": "Daily Radar run failed before completion. Check backend logs for the root cause.",
            "stage": "universe_selection",
            "error_type": "RuntimeError",
        }
    }
    assert sensitive_url not in response.text
    assert "secret-token" not in response.text
    assert client.captured_daily_radar_call == {}  # type: ignore[attr-defined]


def test_daily_radar_run_endpoint_returns_json_503_when_raw_data_backfill_fails(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    fetcher = RaisingBatchTechnicalFetcher()
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        technical_fetcher=fetcher,
        raise_server_exceptions=False,
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": {
            "code": "daily_radar_run_failed",
            "message": "Daily Radar run failed before completion. Check backend logs for the root cause.",
            "stage": "raw_data_backfill",
            "error_type": "RuntimeError",
        }
    }
    assert fetcher.calls == [(["2330.TW"], date(2026, 6, 1))]
    assert client.captured_daily_radar_call == {}  # type: ignore[attr-defined]


def test_daily_radar_run_endpoint_returns_json_503_when_service_fails_before_completion(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    _persist_raw_data(daily_radar_db_session, record_date=date(2026, 6, 1))
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        raise_server_exceptions=False,
        run_error=RuntimeError("simulated service pre-run failure"),
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": {
            "code": "daily_radar_run_failed",
            "message": "Daily Radar run failed before completion. Check backend logs for the root cause.",
            "stage": "daily_radar_service",
            "error_type": "RuntimeError",
        }
    }


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
