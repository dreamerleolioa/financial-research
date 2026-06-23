from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
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
from ai_stock_sentinel.daily_radar.name_backfill import get_daily_radar_symbol_name_resolver
from ai_stock_sentinel.daily_radar.repository import upsert_shared_background_context
from ai_stock_sentinel.daily_radar.universe import InstitutionalLeaderRow
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarPreparedRun,
    DailyRadarRun,
    Phase1AvwapSnapshot,
    SharedBackgroundContext,
    StockRawData,
    User,
    UserPortfolio,
    UserWatchlist,
)
from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.phase1_avwap.calculator import DailyPriceBar


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


class FakePhase1AvwapDailyPriceProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        self.calls.append((symbol, start_date, end_date))
        return [
            DailyPriceBar(date(2026, 5, 27), 900, 910, 890, 905, 1000, 905000),
            DailyPriceBar(date(2026, 5, 28), 906, 920, 900, 918, 1000, 918000),
            DailyPriceBar(end_date, 920, 930, 910, 925, 1000, 925000),
        ]


class RaisingPhase1AvwapDailyPriceProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        self.calls.append((symbol, start_date, end_date))
        raise RuntimeError("simulated AVWAP outage")


class MissingPhase1AvwapDailyPriceProvider:
    source_provider = "test"
    source_dataset = "test_daily_price"

    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        self.calls.append((symbol, start_date, end_date))
        return [DailyPriceBar(date(2026, 5, 30), 900, 910, 890, 905, 1000, 905000)]


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
        daily_radar_router.get_phase1_avwap_daily_price_provider,
        get_daily_radar_symbol_name_resolver,
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
    background_context_provider: FakeBackgroundChipContextProvider | RaisingBackgroundChipContextProvider | None = None,
    phase1_avwap_provider: FakePhase1AvwapDailyPriceProvider | RaisingPhase1AvwapDailyPriceProvider | MissingPhase1AvwapDailyPriceProvider | None = None,
    raise_server_exceptions: bool = True,
    run_error: Exception | None = None,
) -> TestClient:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    captured: dict[str, Any] = {}
    provider = universe_provider or FakeUniverseProvider()
    fetcher = technical_fetcher or FakeBatchTechnicalFetcher()
    context_provider = market_context_provider or FakeMarketIndexContextProvider()
    chip_context_provider = background_context_provider or FakeBackgroundChipContextProvider()
    phase1_provider = phase1_avwap_provider or FakePhase1AvwapDailyPriceProvider()

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
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_background_chip_context_provider] = lambda: chip_context_provider
    api.app.dependency_overrides[daily_radar_router.get_phase1_avwap_daily_price_provider] = lambda: phase1_provider
    client = TestClient(api.app, raise_server_exceptions=raise_server_exceptions)
    client.captured_daily_radar_call = captured  # type: ignore[attr-defined]
    client.fake_universe_provider = provider  # type: ignore[attr-defined]
    client.fake_technical_fetcher = fetcher  # type: ignore[attr-defined]
    client.fake_market_context_provider = context_provider  # type: ignore[attr-defined]
    client.fake_background_context_provider = chip_context_provider  # type: ignore[attr-defined]
    client.fake_phase1_avwap_provider = phase1_provider  # type: ignore[attr-defined]
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


def _completed_segmented_step_statuses() -> dict[str, dict[str, Any]]:
    return {
        "refresh-avwap": {"status": "completed"},
        "refresh-lending": {"status": "completed"},
        "refresh-full-margin": {"status": "completed"},
        "refresh-ohlcv": {"status": "completed"},
        "refresh-market-context": {"status": "completed"},
    }


def _persist_raw_data(
    session: Session,
    *,
    symbol: str = "2330.TW",
    record_date: date = date(2026, 6, 1),
    is_final: bool = True,
    technical: dict[str, Any] | None = None,
) -> StockRawData:
    row = StockRawData(
        symbol=symbol,
        record_date=record_date,
        technical=technical or {"name": symbol, "ohlcv": {}, "indicators": {}},
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


def test_daily_radar_run_endpoint_refreshes_daily_chip_context_before_cache_read(
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
    upsert_shared_background_context(
        daily_radar_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "future_fixture_cache"},
        as_of_date=date(2026, 6, 2),
        freshness="fresh",
        payload={"top_holders_stable": False, "future_only": True},
        replay_key="background_context:2330.TW:weekly_major_holders:2026-06-02",
    )
    daily_radar_db_session.commit()
    chip_context_provider = FakeBackgroundChipContextProvider()
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        background_context_provider=chip_context_provider,
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
    assert chip_context_provider.calls == [
        {
            "symbols": ["2330.TW"],
            "context_types": ["lending", "full_margin"],
            "run_date": date(2026, 5, 29),
            "market": "TW",
        }
    ]
    captured = client.captured_daily_radar_call  # type: ignore[attr-defined]
    assert captured["cache_rows"] == [raw_row]
    contexts = captured["background_contexts_by_symbol"]["2330.TW"]
    weekly_context = next(context for context in contexts if context["context_type"] == "weekly_major_holders")
    assert weekly_context["freshness"] == "fresh"
    assert weekly_context["payload"] == {"top_holders_stable": True}
    assert weekly_context["as_of_date"] == "2026-05-25"
    lending_context = next(context for context in contexts if context["context_type"] == "lending")
    assert lending_context["freshness"] == "fresh"
    assert lending_context["payload"] == {"label": "lending_fixture"}
    full_margin_context = next(context for context in contexts if context["context_type"] == "full_margin")
    assert full_margin_context["freshness"] == "fresh"
    assert full_margin_context["payload"] == {"label": "full_margin_fixture"}


def test_daily_radar_run_endpoint_degrades_when_daily_chip_context_refresh_fails(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    raw_row = _persist_raw_data(daily_radar_db_session, record_date=date(2026, 5, 29))
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        background_context_provider=RaisingBackgroundChipContextProvider(),
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
    lending_context = next(context for context in contexts if context["context_type"] == "lending")
    assert lending_context["freshness"] == "missing"
    assert lending_context["missing_reason"] == "context_cache_missing"


def test_daily_radar_run_endpoint_skips_daily_chip_context_refresh_for_non_tw_market(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    _persist_raw_data(daily_radar_db_session, record_date=date(2026, 5, 29))
    chip_context_provider = FakeBackgroundChipContextProvider()
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        background_context_provider=chip_context_provider,
    )

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-05-29", "market": "US"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert chip_context_provider.calls == []


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


def test_daily_radar_run_endpoint_refreshes_phase1_avwap_for_selected_symbols(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    _persist_raw_data(daily_radar_db_session, record_date=date(2026, 6, 1))
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/run",
            json={"run_date": "2026-06-01"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    provider = client.fake_phase1_avwap_provider  # type: ignore[attr-defined]
    assert provider.calls == [("2330.TW", date(2026, 2, 1), date(2026, 6, 1))]
    snapshot = daily_radar_db_session.query(Phase1AvwapSnapshot).filter_by(symbol="2330.TW").one()
    assert snapshot.data_date == date(2026, 6, 1)
    assert snapshot.freshness == "fresh"
    assert snapshot.payload["anchors"]["swing_low_60d"]["available"] is True


def test_daily_radar_run_endpoint_continues_when_phase1_avwap_refresh_fails(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    _persist_raw_data(daily_radar_db_session, record_date=date(2026, 6, 1))
    phase1_provider = RaisingPhase1AvwapDailyPriceProvider()
    client = _api_client(
        monkeypatch,
        daily_radar_db_session,
        phase1_avwap_provider=phase1_provider,
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
    assert phase1_provider.calls == [("2330.TW", date(2026, 2, 1), date(2026, 6, 1))]
    assert daily_radar_db_session.query(Phase1AvwapSnapshot).all() == []


def test_daily_radar_prepare_universe_endpoint_persists_capped_selected_symbols(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    provider = FakeUniverseProvider(
        same_day=[
            InstitutionalLeaderRow("2330.TW", 1, 91.0),
            InstitutionalLeaderRow("2454.TW", 2, 88.0),
            InstitutionalLeaderRow("2317.TW", 3, 77.0),
        ],
    )
    client = _api_client(monkeypatch, daily_radar_db_session, universe_provider=provider)

    try:
        response = client.post(
            "/internal/daily-radar/prepare-universe",
            json={"run_date": "2026-06-01", "market": "TW", "max_symbols": 2},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "prepared"
    assert body["symbol_count"] == 2
    assert body["selected_symbols"] == ["2330.TW", "2454.TW"]
    prepared = daily_radar_db_session.query(DailyRadarPreparedRun).one()
    assert prepared.run_date == date(2026, 6, 1)
    assert prepared.selected_symbols == ["2330.TW", "2454.TW"]
    assert prepared.universe[0]["primary_track"] == "same_day_institutional"


def test_daily_radar_refresh_avwap_endpoint_uses_prepared_symbols(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[
            {
                "symbol": "2330.TW",
                "rank": 1,
                "primary_track": "same_day_institutional",
                "tracks": ["same_day_institutional"],
                "track_metrics": {"same_day_institutional": {"score": 91.0}},
            }
        ],
        symbol_count=1,
    )
    daily_radar_db_session.add(prepared)
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/refresh-avwap",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["step"] == "refresh-avwap"
    assert body["symbol_count"] == 1
    provider = client.fake_phase1_avwap_provider  # type: ignore[attr-defined]
    assert provider.calls == [("2330.TW", date(2026, 2, 1), date(2026, 6, 1))]


def test_daily_radar_refresh_avwap_endpoint_includes_active_holdings_and_watchlist(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    daily_radar_db_session.add(
        User(id=1, google_sub="user-1", email="user@example.com", name="User")
    )
    daily_radar_db_session.flush()
    daily_radar_db_session.add(
        UserPortfolio(
            user_id=1,
            symbol="2449.TW",
            entry_price=334.5,
            quantity=50,
            entry_date=date(2026, 6, 22),
            is_active=True,
        )
    )
    daily_radar_db_session.add(UserWatchlist(user_id=1, symbol="3035.TW", sort_order=1))
    daily_radar_db_session.add(UserWatchlist(user_id=1, symbol="AAPL", sort_order=2))
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[
            {
                "symbol": "2330.TW",
                "rank": 1,
                "primary_track": "same_day_institutional",
                "tracks": ["same_day_institutional"],
                "track_metrics": {"same_day_institutional": {"score": 91.0}},
            }
        ],
        symbol_count=1,
    )
    daily_radar_db_session.add(prepared)
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/refresh-avwap",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["symbol_count"] == 3
    assert body["skipped_symbols"] == ["AAPL"]
    assert body["skipped_symbol_reasons"] == {"AAPL": "unsupported_phase1_avwap_market"}
    daily_radar_db_session.refresh(prepared)
    assert prepared.step_statuses["refresh-avwap"]["skipped_symbol_reasons"] == {
        "AAPL": "unsupported_phase1_avwap_market"
    }
    provider = client.fake_phase1_avwap_provider  # type: ignore[attr-defined]
    assert provider.calls == [
        ("2330.TW", date(2026, 2, 1), date(2026, 6, 1)),
        ("2449.TW", date(2026, 2, 1), date(2026, 6, 1)),
        ("3035.TW", date(2026, 2, 1), date(2026, 6, 1)),
    ]


def test_daily_radar_refresh_avwap_endpoint_reports_missing_symbol_reasons(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[],
        symbol_count=1,
    )
    daily_radar_db_session.add(prepared)
    daily_radar_db_session.commit()
    provider = MissingPhase1AvwapDailyPriceProvider()
    client = _api_client(monkeypatch, daily_radar_db_session, phase1_avwap_provider=provider)

    try:
        response = client.post(
            "/internal/daily-radar/refresh-avwap",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["missing_symbols"] == ["2330.TW"]
    assert body["missing_symbol_reasons"] == {"2330.TW": "daily_price_row_missing_for_data_date"}
    daily_radar_db_session.refresh(prepared)
    assert prepared.step_statuses["refresh-avwap"]["missing_symbol_reasons"] == {
        "2330.TW": "daily_price_row_missing_for_data_date"
    }


def test_daily_radar_refresh_lending_reuses_same_day_fresh_cache(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[
            {
                "symbol": "2330.TW",
                "rank": 1,
                "primary_track": "same_day_institutional",
                "tracks": ["same_day_institutional"],
                "track_metrics": {"same_day_institutional": {"score": 91.0}},
            }
        ],
        symbol_count=1,
    )
    daily_radar_db_session.add(prepared)
    upsert_shared_background_context(
        daily_radar_db_session,
        symbol="2330.TW",
        context_type="lending",
        applicable_consumers=("daily_radar",),
        source={"domain": "background_context", "provider": "fixture_provider"},
        as_of_date=date(2026, 6, 1),
        freshness="fresh",
        payload={"label": "cached_lending"},
        replay_key="background_context:2330.TW:lending:2026-06-01",
    )
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/refresh-lending",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["records_written"] == 0
    assert response.json()["reused_symbols"] == ["2330.TW"]
    provider = client.fake_background_context_provider  # type: ignore[attr-defined]
    assert provider.calls == []
    daily_radar_db_session.refresh(prepared)
    assert prepared.step_statuses["refresh-lending"]["status"] == "completed"


def test_daily_radar_refresh_ohlcv_updates_prepared_universe_technical_tracks(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[
            {
                "symbol": "2330.TW",
                "rank": 1,
                "primary_track": "same_day_institutional",
                "tracks": ["same_day_institutional"],
                "track_metrics": {"same_day_institutional": {"score": 91.0}},
            }
        ],
        symbol_count=1,
    )
    daily_radar_db_session.add(prepared)
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/refresh-ohlcv",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    daily_radar_db_session.refresh(prepared)
    assert "price_volume" in prepared.universe[0]["tracks"]
    assert prepared.universe[0]["track_metrics"]["price_volume"]["matched"] is True
    assert prepared.step_statuses["refresh-ohlcv"]["status"] == "completed"


def test_daily_radar_run_scoring_requires_completed_refresh_steps(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[],
        symbol_count=1,
        market_context=_market_context(),
        step_statuses={
            "refresh-avwap": {"status": "completed"},
            "refresh-market-context": {"status": "completed"},
        },
    )
    daily_radar_db_session.add(prepared)
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/run-scoring",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "daily_radar_refresh_steps_incomplete"
    assert detail["incomplete_steps"] == ["refresh-lending", "refresh-full-margin", "refresh-ohlcv"]


def test_daily_radar_run_scoring_allows_failed_optional_avwap_step(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    _persist_raw_data(daily_radar_db_session, symbol="2330.TW", record_date=date(2026, 6, 1))
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[],
        symbol_count=1,
        market_context=_market_context(),
        step_statuses={
            "refresh-avwap": {
                "status": "failed",
                "missing_symbols": ["2330.TW"],
                "missing_symbol_reasons": {"2330.TW": "daily_price_row_missing_for_data_date"},
            },
            "refresh-lending": {"status": "completed"},
            "refresh-full-margin": {"status": "completed"},
            "refresh-ohlcv": {"status": "completed"},
            "refresh-market-context": {"status": "completed"},
        },
    )
    daily_radar_db_session.add(prepared)
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/run-scoring",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    captured = client.captured_daily_radar_call  # type: ignore[attr-defined]
    assert [row.symbol for row in captured["cache_rows"]] == ["2330.TW"]


def test_daily_radar_run_scoring_requires_prepared_market_context(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    prepared = DailyRadarPreparedRun(
        run_date=date(2026, 6, 1),
        market="TW",
        selected_symbols=["2330.TW"],
        universe=[],
        symbol_count=1,
        step_statuses=_completed_segmented_step_statuses(),
    )
    daily_radar_db_session.add(prepared)
    daily_radar_db_session.commit()
    client = _api_client(monkeypatch, daily_radar_db_session)

    try:
        response = client.post(
            "/internal/daily-radar/run-scoring",
            json={"run_date": "2026-06-01", "market": "TW"},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 409
    assert "market context is not prepared" in response.json()["detail"]


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


def test_daily_radar_weekly_chip_context_update_uses_holdings_watchlist_and_latest_candidates_when_symbols_omitted(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    provider = FakeBackgroundChipContextProvider()
    user = User(google_sub="user-1", email="user@example.com", name="User")
    other_user = User(google_sub="user-2", email="other@example.com", name="Other")
    daily_radar_db_session.add_all([user, other_user])
    daily_radar_db_session.flush()
    daily_radar_db_session.add_all(
        [
            UserPortfolio(
                user_id=user.id,
                symbol="2330.TW",
                entry_price=Decimal("900.00"),
                quantity=1,
                entry_date=date(2026, 6, 1),
                is_active=True,
            ),
            UserPortfolio(
                user_id=other_user.id,
                symbol="2303.TW",
                entry_price=Decimal("50.00"),
                quantity=1,
                entry_date=date(2026, 6, 1),
                is_active=True,
            ),
            UserPortfolio(
                user_id=user.id,
                symbol="9999.TW",
                entry_price=Decimal("10.00"),
                quantity=1,
                entry_date=date(2026, 1, 1),
                is_active=False,
            ),
            UserWatchlist(user_id=user.id, symbol="2454.TW", sort_order=1),
            UserWatchlist(user_id=other_user.id, symbol="2330.TW", sort_order=1),
        ]
    )
    run = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 2))
    _persist_daily_radar_candidate(daily_radar_db_session, run, symbol="2317.TW", score=88)
    _persist_daily_radar_candidate(daily_radar_db_session, run, symbol="2330.TW", score=86)
    daily_radar_db_session.commit()

    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_background_chip_context_provider] = lambda: provider

    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/chip-context/update",
            json={
                "run_date": "2026-06-02",
                "market": "TW",
                "context_types": ["weekly_major_holders"],
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
        "symbol_count": 4,
        "context_types": ["weekly_major_holders"],
        "records_written": 4,
        "errors": [],
    }
    assert provider.calls == [
        {
            "symbols": ["2303.TW", "2330.TW", "2454.TW", "2317.TW"],
            "context_types": ["weekly_major_holders"],
            "run_date": date(2026, 6, 2),
            "market": "TW",
        }
    ]
    rows = daily_radar_db_session.query(SharedBackgroundContext).all()
    assert {row.symbol for row in rows} == {"2303.TW", "2330.TW", "2454.TW", "2317.TW"}
    for row in rows:
        assert row.context_type == "weekly_major_holders"
        assert row.payload == {"label": "weekly_major_holders_fixture"}
        assert "user_id" not in row.payload
        assert "quantity" not in row.payload
        assert "entry_price" not in row.payload


def test_daily_radar_chip_context_update_keeps_daily_contexts_on_latest_candidates_when_context_types_default(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    provider = FakeBackgroundChipContextProvider()
    user = User(google_sub="user-1", email="user@example.com", name="User")
    daily_radar_db_session.add(user)
    daily_radar_db_session.flush()
    daily_radar_db_session.add_all(
        [
            UserPortfolio(
                user_id=user.id,
                symbol="2330.TW",
                entry_price=Decimal("900.00"),
                quantity=1,
                entry_date=date(2026, 6, 1),
                is_active=True,
            ),
            UserWatchlist(user_id=user.id, symbol="2454.TW", sort_order=1),
        ]
    )
    run = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 2))
    _persist_daily_radar_candidate(daily_radar_db_session, run, symbol="2317.TW", score=88)
    _persist_daily_radar_candidate(daily_radar_db_session, run, symbol="2330.TW", score=86)
    daily_radar_db_session.commit()

    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_background_chip_context_provider] = lambda: provider

    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/chip-context/update",
            json={
                "run_date": "2026-06-02",
                "market": "TW",
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
        "symbol_count": 3,
        "context_types": ["weekly_major_holders", "lending", "full_margin"],
        "records_written": 7,
        "errors": [],
    }
    assert provider.calls == [
        {
            "symbols": ["2330.TW", "2454.TW", "2317.TW"],
            "context_types": ["weekly_major_holders"],
            "run_date": date(2026, 6, 2),
            "market": "TW",
        },
        {
            "symbols": ["2317.TW", "2330.TW"],
            "context_types": ["lending", "full_margin"],
            "run_date": date(2026, 6, 2),
            "market": "TW",
        },
    ]
    rows = daily_radar_db_session.query(SharedBackgroundContext).all()
    assert {
        row.symbol
        for row in rows
        if row.context_type == "weekly_major_holders"
    } == {"2330.TW", "2454.TW", "2317.TW"}
    assert {
        row.symbol
        for row in rows
        if row.context_type in {"lending", "full_margin"}
    } == {"2317.TW", "2330.TW"}


def test_daily_radar_weekly_chip_context_update_reports_symbol_source_failures(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    from ai_stock_sentinel.daily_radar import background_context as background_context_module
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    provider = FakeBackgroundChipContextProvider()

    def raise_active_holdings(_session: Session) -> list[str]:
        raise RuntimeError("active holdings unavailable")

    monkeypatch.setattr(background_context_module, "_active_portfolio_symbols", raise_active_holdings)
    monkeypatch.setattr(background_context_module, "_watchlist_symbols", lambda _session: ["2454.TW"])
    monkeypatch.setattr(
        background_context_module,
        "_latest_daily_radar_symbols",
        lambda _session, *, market: ["2330.TW"],
    )
    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    api.app.dependency_overrides[daily_radar_router.get_daily_radar_background_chip_context_provider] = lambda: provider

    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/chip-context/update",
            json={
                "run_date": "2026-06-02",
                "market": "TW",
                "context_types": ["weekly_major_holders"],
            },
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["symbol_count"] == 2
    assert body["records_written"] == 2
    assert body["errors"] == [
        {
            "code": "background_context_symbol_source_failed",
            "source": "active_portfolio_holdings",
            "message": "active holdings unavailable",
            "error_type": "RuntimeError",
        }
    ]
    assert provider.calls == [
        {
            "symbols": ["2454.TW", "2330.TW"],
            "context_types": ["weekly_major_holders"],
            "run_date": date(2026, 6, 2),
            "market": "TW",
        }
    ]


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
            DailyRadarPreparedRun.__table__,
            DailyRadarCandidate.__table__,
            Phase1AvwapSnapshot.__table__,
            SharedBackgroundContext.__table__,
            StockRawData.__table__,
            User.__table__,
            UserPortfolio.__table__,
            UserWatchlist.__table__,
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
    name: str | None = None,
    primary_bucket: str = "institutional_accumulation",
    secondary_buckets: list[str] | None = None,
    background_context_labels: list[dict[str, Any]] | None = None,
) -> DailyRadarCandidate:
    input_snapshot: dict[str, Any] = {
        "symbol": symbol,
        "close": 980,
        "market_context": {"index_symbol": "TAIEX", "trend_state": "above_ma20"},
    }
    if background_context_labels is not None:
        input_snapshot["background_context_labels"] = background_context_labels
    candidate = DailyRadarCandidate(
        run_id=run.id,
        symbol=symbol,
        name=name if name is not None else f"{symbol} fixture",
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
        input_snapshot=input_snapshot,
        data_dates={"ohlcv": run.run_date.isoformat(), "institutional_flow": run.run_date.isoformat()},
    )
    session.add(candidate)
    session.flush()
    run.candidate_count = len(run.candidates)
    session.flush()
    return candidate


def test_internal_daily_radar_name_backfill_endpoint_updates_cloud_database_rows(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    run = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 16))
    candidate = _persist_daily_radar_candidate(
        daily_radar_db_session,
        run,
        symbol="2330.TW",
        name="2330.TW",
        score=88,
    )
    raw_row = _persist_raw_data(
        daily_radar_db_session,
        symbol="2330.TW",
        record_date=date(2026, 6, 16),
        technical={"name": "2330.TW", "ohlcv": {"close": 100.0}, "indicators": {}},
    )
    daily_radar_db_session.commit()
    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    api.app.dependency_overrides[get_daily_radar_symbol_name_resolver] = lambda: lambda symbol: {
        "2330.TW": "台積電"
    }.get(symbol)

    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/name-backfill",
            json={"limit": 100},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert response.json() == {
        "status": "completed",
        "dry_run": False,
        "scanned": 1,
        "updated_candidates": 1,
        "updated_raw_rows": 1,
        "unresolved_symbols": [],
    }
    daily_radar_db_session.refresh(candidate)
    daily_radar_db_session.refresh(raw_row)
    assert candidate.name == "台積電"
    assert raw_row.technical["name"] == "台積電"


def test_internal_daily_radar_name_backfill_endpoint_dry_run_does_not_write(
    monkeypatch,
    daily_radar_db_session: Session,
) -> None:
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    run = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 16))
    candidate = _persist_daily_radar_candidate(
        daily_radar_db_session,
        run,
        symbol="2330.TW",
        name="2330.TW",
        score=88,
    )
    raw_row = _persist_raw_data(
        daily_radar_db_session,
        symbol="2330.TW",
        record_date=date(2026, 6, 16),
        technical={"name": "2330.TW", "ohlcv": {"close": 100.0}, "indicators": {}},
    )
    daily_radar_db_session.commit()
    api.app.dependency_overrides[get_db] = lambda: daily_radar_db_session
    api.app.dependency_overrides[get_daily_radar_symbol_name_resolver] = lambda: lambda _symbol: "台積電"

    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/name-backfill",
            json={"dry_run": True},
            headers={"X-Internal-Token": "test-token"},
        )
    finally:
        _clear_daily_radar_api_overrides()

    assert response.status_code == 200
    assert response.json()["updated_candidates"] == 1
    assert response.json()["updated_raw_rows"] == 1
    daily_radar_db_session.refresh(candidate)
    daily_radar_db_session.refresh(raw_row)
    assert candidate.name == "2330.TW"
    assert raw_row.technical["name"] == "2330.TW"


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
    _persist_daily_radar_candidate(
        daily_radar_db_session,
        latest,
        symbol="2317.TW",
        score=93,
        background_context_labels=[
            {
                "context_type": "weekly_major_holders",
                "label": "大戶持股集中背景",
                "source": {"domain": "background_context", "provider": "fixture_cache"},
                "as_of_date": "2026-05-31",
                "freshness": "fresh",
                "missing_reason": None,
                "replay_key": "background_context:2317.TW:weekly_major_holders:2026-05-31",
                "applicable_consumers": ["daily_radar"],
            },
            {
                "context_type": "full_margin",
                "label": "完整融資融券背景資料未更新",
                "source": {"domain": "background_context", "provider": "fixture_cache"},
                "as_of_date": None,
                "freshness": "missing",
                "missing_reason": "context_cache_missing",
                "replay_key": "background_context:2317.TW:full_margin:missing",
                "applicable_consumers": ["daily_radar"],
            },
        ],
    )
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
    assert [label["context_type"] for label in first["background_context_labels"]] == [
        "weekly_major_holders",
        "full_margin",
    ]
    assert first["background_context_labels"][0]["freshness"] == "fresh"
    assert first["background_context_labels"][1]["freshness"] == "missing"
    assert first["background_context_labels"][1]["missing_reason"] == "context_cache_missing"
    assert payload["candidates"][1]["background_context_labels"] == []


def test_public_daily_radar_keeps_cached_symbol_name_offline(
    public_daily_radar_client: TestClient,
    daily_radar_db_session: Session,
    monkeypatch,
) -> None:
    from ai_stock_sentinel.daily_radar import router as daily_radar_router

    if hasattr(daily_radar_router, "resolve_symbol_name"):
        monkeypatch.setattr(daily_radar_router, "resolve_symbol_name", lambda _symbol: pytest.fail("public read must stay offline"))
    run = _persist_daily_radar_run(daily_radar_db_session, run_date=date(2026, 6, 2))
    _persist_daily_radar_candidate(
        daily_radar_db_session,
        run,
        symbol="2330.TW",
        name="2330.TW",
        score=88,
    )
    daily_radar_db_session.commit()

    response = public_daily_radar_client.get("/daily-radar/latest")

    assert response.status_code == 200
    assert response.json()["candidates"][0]["symbol"] == "2330.TW"
    assert response.json()["candidates"][0]["name"] == "2330.TW"

    history_response = public_daily_radar_client.get("/daily-radar/symbol/2330.TW")

    assert history_response.status_code == 200
    assert history_response.json()[0]["symbol"] == "2330.TW"
    assert history_response.json()[0]["name"] == "2330.TW"


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
            "background_context_labels": [],
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
