from __future__ import annotations

import importlib.util
from datetime import date
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundContextPayload,
    build_background_context_labels,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.default_background_context import DefaultBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.finmind_background_context import (
    FINMIND_BACKGROUND_CONTEXT_CONSUMERS,
    FinMindBackgroundChipContextProvider,
)
from ai_stock_sentinel.daily_radar.tdcc_background_context import TdccWeeklyMajorHoldersProvider
from ai_stock_sentinel.daily_radar.repository import (
    BACKGROUND_CONTEXT_TYPES,
    get_shared_background_context_trace_by_symbol,
    upsert_shared_background_context,
)
from ai_stock_sentinel.db.models import SharedBackgroundContext
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

    Base.metadata.create_all(engine, tables=[SharedBackgroundContext.__table__])
    with Session(engine) as session:
        yield session


class FixtureBackgroundProvider:
    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> list[BackgroundContextPayload]:
        return [
            BackgroundContextPayload(
                symbol=symbol,
                context_type=context_type,
                applicable_consumers=("daily_radar",),
                source={"domain": "background_context", "provider": "fixture_provider", "market": market},
                as_of_date=run_date,
                freshness="fresh",
                payload={"fixture": True},
                missing_reason=None,
                replay_key=f"background_context:{symbol}:{context_type}:{run_date.isoformat()}",
            )
            for symbol in symbols
            for context_type in context_types
        ]


class _FakeFinMindResponse:
    def __init__(self, body: dict, *, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _FakeTextResponse:
    def __init__(self, text: str) -> None:
        self.content = text.encode("utf-8-sig")

    def raise_for_status(self) -> None:
        return None


def _load_background_context_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob("*_add_shared_background_contexts.py")
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("shared_background_contexts_migration", migration_paths[0])
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_migration_sql(direction: str) -> str:
    migration = _load_background_context_migration()
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


def test_background_context_migration_creates_consumer_neutral_cache_table() -> None:
    sql = _render_migration_sql("upgrade")

    assert "CREATE TABLE shared_background_contexts" in sql
    assert "symbol VARCHAR(20) NOT NULL" in sql
    assert "context_type VARCHAR(50) NOT NULL" in sql
    assert "applicable_consumers JSONB NOT NULL" in sql
    assert "source JSONB NOT NULL" in sql
    assert "payload JSONB NOT NULL" in sql
    assert "missing_reason VARCHAR(120)" in sql
    assert "replay_key VARCHAR(240) NOT NULL" in sql
    assert "CONSTRAINT uq_shared_background_context_symbol_type_replay UNIQUE (symbol, context_type, replay_key)" in sql
    assert "CONSTRAINT ck_shared_background_context_freshness CHECK" in sql
    assert "CREATE INDEX idx_shared_background_context_symbol" in sql
    assert "CREATE INDEX idx_shared_background_context_context_type" in sql
    assert "CREATE INDEX idx_shared_background_context_as_of_date" in sql
    assert "CREATE INDEX idx_shared_background_context_freshness" in sql
    assert "CREATE INDEX idx_shared_background_context_replay_key" in sql


def test_background_context_migration_downgrade_drops_indexes_before_table() -> None:
    sql = _render_migration_sql("downgrade")

    assert sql.index("DROP INDEX idx_shared_background_context_freshness") < sql.index(
        "DROP TABLE shared_background_contexts"
    )


def test_upsert_shared_background_context_preserves_history_and_updates_same_replay(db_session: Session) -> None:
    first = upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 5, 24),
        freshness="stale",
        payload={"major_holder_ratio": 0.58},
        missing_reason="source_lagging",
    )
    second = upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["daily_radar", "individual_analysis"],
        source={"domain": "background_context", "provider": "fixture_v2"},
        as_of_date=date(2026, 5, 31),
        freshness="fresh",
        payload={"major_holder_ratio": 0.61},
        missing_reason=None,
    )
    third = upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["daily_radar", "analyze"],
        source={"domain": "background_context", "provider": "fixture_v3"},
        as_of_date=date(2026, 5, 31),
        freshness="fresh",
        payload={"major_holder_ratio": 0.63},
        missing_reason=None,
    )
    db_session.commit()

    assert second.id != first.id
    assert third.id == second.id
    rows = db_session.query(SharedBackgroundContext).all()
    assert len(rows) == 2
    latest_trace = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders"],
    )["2330.TW"][0]
    assert latest_trace["payload"] == {"major_holder_ratio": 0.63}
    assert latest_trace["applicable_consumers"] == ["daily_radar", "analyze"]
    assert latest_trace["replay_key"] == "background_context:2330.TW:weekly_major_holders:2026-05-31"


def test_point_in_time_trace_selects_latest_context_on_or_before_reference_date(db_session: Session) -> None:
    upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["lifecycle_review"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 1, 5),
        freshness="fresh",
        payload={"major_holder_ratio": 0.57},
    )
    upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["lifecycle_review"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 2, 1),
        freshness="fresh",
        payload={"major_holder_ratio": 0.72},
    )
    db_session.commit()

    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders"],
        consumer="lifecycle_review",
        reference_date=date(2026, 1, 20),
        point_in_time=True,
    )

    assert traces["2330.TW"][0]["as_of_date"] == "2026-01-05"
    assert traces["2330.TW"][0]["payload"] == {"major_holder_ratio": 0.57}


def test_point_in_time_trace_reports_future_context_only_when_no_historical_context_exists(db_session: Session) -> None:
    upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["lifecycle_review"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 2, 1),
        freshness="fresh",
        payload={"major_holder_ratio": 0.72},
    )
    db_session.commit()

    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders"],
        consumer="lifecycle_review",
        reference_date=date(2026, 1, 20),
        point_in_time=True,
    )

    trace = traces["2330.TW"][0]
    assert trace["freshness"] == "missing"
    assert trace["missing_reason"] == "future_context_excluded"
    assert trace["source"]["excluded_as_of_date"] == "2026-02-01"


def test_trace_respects_applicable_consumers(db_session: Session) -> None:
    upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 5, 31),
        freshness="fresh",
        payload={"major_holder_ratio": 0.61},
    )
    db_session.commit()

    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders"],
        consumer="analyze",
    )

    trace = traces["2330.TW"][0]
    assert trace["freshness"] == "missing"
    assert trace["missing_reason"] == "context_not_applicable_to_consumer"
    assert trace["source"]["status"] == "not_applicable"
    assert trace["applicable_consumers"] == ["analyze"]


def test_read_trace_returns_fresh_stale_and_missing_contexts(db_session: Session) -> None:
    upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 5, 31),
        freshness="fresh",
        payload={"major_holder_ratio": 0.61},
    )
    upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="lending",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 5, 20),
        freshness="stale",
        payload={"borrow_balance": 1200},
        missing_reason="source_stale",
    )
    db_session.commit()

    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW", "2454.TW"],
        context_types=BACKGROUND_CONTEXT_TYPES,
    )

    by_type = {context["context_type"]: context for context in traces["2330.TW"]}
    assert by_type["weekly_major_holders"]["freshness"] == "fresh"
    assert by_type["lending"]["freshness"] == "stale"
    assert by_type["lending"]["missing_reason"] == "source_stale"
    assert by_type["full_margin"]["freshness"] == "missing"
    assert by_type["full_margin"]["missing_reason"] == "context_cache_missing"
    assert {context["freshness"] for context in traces["2454.TW"]} == {"missing"}


def test_point_in_time_read_prefers_same_reference_missing_context_over_older_fresh_row(
    db_session: Session,
) -> None:
    upsert_shared_background_context(
        db_session,
        symbol="2377.TW",
        context_type="lending",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture_cache"},
        as_of_date=date(2026, 6, 10),
        freshness="fresh",
        payload={"latest_daily_lending_volume": 125.0},
        replay_key="background_context:2377.TW:lending:2026-06-10",
    )
    upsert_shared_background_context(
        db_session,
        symbol="2377.TW",
        context_type="lending",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture_cache"},
        as_of_date=None,
        freshness="missing",
        payload={},
        missing_reason="finmind_no_data",
        replay_key="background_context:2377.TW:lending:2026-06-15:missing:finmind_no_data",
    )
    db_session.commit()

    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2377.TW"],
        context_types=["lending"],
        reference_date=date(2026, 6, 15),
        point_in_time=True,
    )

    trace = traces["2377.TW"][0]
    assert trace["freshness"] == "missing"
    assert trace["missing_reason"] == "finmind_no_data"
    assert trace["replay_key"] == "background_context:2377.TW:lending:2026-06-15:missing:finmind_no_data"


def test_point_in_time_read_does_not_let_older_missing_context_hide_newer_fresh_row(
    db_session: Session,
) -> None:
    upsert_shared_background_context(
        db_session,
        symbol="2377.TW",
        context_type="full_margin",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture_cache"},
        as_of_date=None,
        freshness="missing",
        payload={},
        missing_reason="finmind_access_required",
        replay_key="background_context:2377.TW:full_margin:2026-06-10:missing:finmind_access_required",
    )
    upsert_shared_background_context(
        db_session,
        symbol="2377.TW",
        context_type="full_margin",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture_cache"},
        as_of_date=date(2026, 6, 12),
        freshness="fresh",
        payload={"latest_margin_balance": 18005.0},
        replay_key="background_context:2377.TW:full_margin:2026-06-12",
    )
    db_session.commit()

    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2377.TW"],
        context_types=["full_margin"],
        reference_date=date(2026, 6, 15),
        point_in_time=True,
    )

    trace = traces["2377.TW"][0]
    assert trace["freshness"] == "fresh"
    assert trace["payload"] == {"latest_margin_balance": 18005.0}
    assert trace["replay_key"] == "background_context:2377.TW:full_margin:2026-06-12"


def test_shared_background_context_trace_has_no_daily_radar_ui_or_ranking_fields(db_session: Session) -> None:
    upsert_shared_background_context(
        db_session,
        symbol="2330.TW",
        context_type="full_margin",
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=None,
        freshness="missing",
        payload={},
        missing_reason="provider_not_configured",
    )
    db_session.commit()

    trace = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW"],
        context_types=["full_margin"],
    )["2330.TW"][0]

    assert set(trace) == {
        "context_type",
        "source",
        "as_of_date",
        "freshness",
        "missing_reason",
        "replay_key",
        "applicable_consumers",
        "payload",
    }
    assert "observation_score" not in trace
    assert "primary_bucket" not in trace
    assert "ui_label" not in trace
    assert "recommended_action" not in trace


def test_background_context_labels_are_consumer_neutral_and_include_missing_trace() -> None:
    labels = build_background_context_labels(
        [
            {
                "context_type": "weekly_major_holders",
                "source": {"domain": "background_context", "provider": "fixture"},
                "as_of_date": "2026-05-31",
                "freshness": "fresh",
                "missing_reason": None,
                "replay_key": "background_context:2330.TW:weekly_major_holders:2026-05-31",
                "applicable_consumers": ["daily_radar"],
                "payload": {"major_holder_ratio": 0.61},
            },
            {
                "context_type": "full_margin",
                "source": {"domain": "background_context", "provider": "fixture"},
                "as_of_date": None,
                "freshness": "missing",
                "missing_reason": "context_cache_missing",
                "replay_key": "background_context:2330.TW:full_margin:missing",
                "applicable_consumers": ["daily_radar"],
                "payload": {},
            },
        ]
    )

    assert labels == [
        {
            "context_type": "weekly_major_holders",
            "label": "大戶持股集中背景",
            "source": {"domain": "background_context", "provider": "fixture"},
            "as_of_date": "2026-05-31",
            "freshness": "fresh",
            "missing_reason": None,
            "replay_key": "background_context:2330.TW:weekly_major_holders:2026-05-31",
            "applicable_consumers": ["daily_radar"],
        },
        {
            "context_type": "full_margin",
            "label": "完整融資融券背景資料未更新",
            "source": {"domain": "background_context", "provider": "fixture"},
            "as_of_date": None,
            "freshness": "missing",
            "missing_reason": "context_cache_missing",
            "replay_key": "background_context:2330.TW:full_margin:missing",
            "applicable_consumers": ["daily_radar"],
        },
    ]
    for label in labels:
        assert "observation_score" not in label
        assert "primary_bucket" not in label
        assert "recommended_action" not in label


def test_update_background_chip_context_cache_fixture_flow_writes_selected_symbols(db_session: Session) -> None:
    result = update_background_chip_context_cache(
        db_session,
        run_date=date(2026, 6, 2),
        market="TW",
        provider=FixtureBackgroundProvider(),
        symbols=["2330.TW", "2330.TW", "2454.TW"],
        context_types=["weekly_major_holders", "full_margin"],
    )
    db_session.commit()

    assert result["status"] == "completed"
    assert result["symbol_count"] == 2
    assert result["records_written"] == 4
    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders", "full_margin"],
    )
    assert [context["freshness"] for context in traces["2330.TW"]] == ["fresh", "fresh"]


def test_finmind_background_provider_builds_full_margin_and_lending_payloads() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, timeout: int):
        calls.append(dict(params))
        if params["dataset"] == "TaiwanStockMarginPurchaseShortSale":
            return _FakeFinMindResponse(
                {
                    "status": 200,
                    "data": [
                        {
                            "date": "2026-06-09",
                            "stock_id": "2330",
                            "MarginPurchaseTodayBalance": 1100,
                            "MarginPurchaseYesterdayBalance": 1000,
                            "ShortSaleTodayBalance": 70,
                            "ShortSaleYesterdayBalance": 50,
                        },
                        {
                            "date": "2026-06-10",
                            "stock_id": "2330",
                            "MarginPurchaseTodayBalance": 1200,
                            "MarginPurchaseYesterdayBalance": 1100,
                            "ShortSaleTodayBalance": 80,
                            "ShortSaleYesterdayBalance": 70,
                        },
                    ],
                }
            )
        if params["dataset"] == "TaiwanStockSecuritiesLending":
            return _FakeFinMindResponse(
                {
                    "status": 200,
                    "data": [
                        {"date": "2026-06-09", "stock_id": "2330", "volume": 100},
                        {"date": "2026-06-09", "stock_id": "2330", "volume": 200},
                        {"date": "2026-06-10", "stock_id": "2330", "volume": 50},
                        {"date": "2026-06-10", "stock_id": "2330", "volume": 75},
                    ],
                }
            )
        raise AssertionError(f"unexpected dataset {params['dataset']}")

    provider = FinMindBackgroundChipContextProvider(
        api_token="test-token",
        request_get=fake_get,
        lookback_trading_days=5,
    )

    payloads = list(
        provider.fetch(
            symbols=["2330.TW"],
            context_types=["full_margin", "lending", "weekly_major_holders"],
            run_date=date(2026, 6, 11),
            market="TW",
        )
    )

    by_type = {payload.context_type: payload for payload in payloads}
    assert set(by_type) == {"full_margin", "lending", "weekly_major_holders"}
    assert [call["dataset"] for call in calls] == [
        "TaiwanStockMarginPurchaseShortSale",
        "TaiwanStockSecuritiesLending",
    ]

    full_margin = by_type["full_margin"]
    assert full_margin.freshness == "fresh"
    assert full_margin.as_of_date == date(2026, 6, 10)
    assert full_margin.applicable_consumers == FINMIND_BACKGROUND_CONTEXT_CONSUMERS
    assert full_margin.payload["latest_margin_balance"] == 1200.0
    assert full_margin.payload["margin_balance_delta"] == 200.0
    assert full_margin.payload["margin_balance_delta_pct"] == pytest.approx(20.0)
    assert full_margin.payload["latest_short_balance"] == 80.0
    assert full_margin.payload["short_balance_delta"] == 30.0
    assert full_margin.replay_key == "background_context:2330.TW:full_margin:2026-06-10"

    lending = by_type["lending"]
    assert lending.freshness == "fresh"
    assert lending.as_of_date == date(2026, 6, 10)
    assert lending.payload["latest_daily_lending_volume"] == 125.0
    assert lending.payload["period_lending_volume"] == 425.0
    assert lending.payload["lending_volume_delta"] == -175.0
    assert lending.payload["daily_point_count"] == 2
    assert lending.replay_key == "background_context:2330.TW:lending:2026-06-10"

    weekly = by_type["weekly_major_holders"]
    assert weekly.freshness == "missing"
    assert weekly.missing_reason == "provider_deferred"
    assert weekly.payload == {}


def test_finmind_background_provider_marks_dataset_errors_as_missing() -> None:
    def fake_get(url: str, *, params: dict, timeout: int):
        return _FakeFinMindResponse(
            {"status": 400, "msg": "Your level is free. Please update your user level."}
        )

    provider = FinMindBackgroundChipContextProvider(api_token="test-token", request_get=fake_get)

    payload = next(
        iter(
            provider.fetch(
                symbols=["2330.TW"],
                context_types=["full_margin"],
                run_date=date(2026, 6, 11),
                market="TW",
            )
        )
    )

    assert payload.context_type == "full_margin"
    assert payload.freshness == "missing"
    assert payload.missing_reason == "finmind_access_required"
    assert payload.source["dataset"] == "TaiwanStockMarginPurchaseShortSale"


def test_finmind_background_provider_retries_expired_managed_token(monkeypatch) -> None:
    class FakeTokenManager:
        def __init__(self) -> None:
            self.tokens = ["expired-token", "fresh-token"]
            self.invalidated = False

        @property
        def token(self) -> str:
            return self.tokens[1] if self.invalidated else self.tokens[0]

        def invalidate(self) -> None:
            self.invalidated = True

    token_manager = FakeTokenManager()
    requests_seen: list[dict] = []

    def fake_get(url: str, *, params: dict, timeout: int):
        requests_seen.append(dict(params))
        if params.get("token") == "expired-token":
            return _FakeFinMindResponse({"status": 402, "msg": "token expired"}, status_code=402)
        return _FakeFinMindResponse(
            {
                "status": 200,
                "data": [
                    {
                        "date": "2026-06-10",
                        "stock_id": "2330",
                        "MarginPurchaseTodayBalance": 1200,
                        "MarginPurchaseYesterdayBalance": 1100,
                        "ShortSaleTodayBalance": 80,
                        "ShortSaleYesterdayBalance": 70,
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "ai_stock_sentinel.daily_radar.finmind_background_context.get_token_manager",
        lambda: token_manager,
    )
    provider = FinMindBackgroundChipContextProvider(request_get=fake_get)

    payload = next(
        iter(
            provider.fetch(
                symbols=["2330.TW"],
                context_types=["full_margin"],
                run_date=date(2026, 6, 11),
                market="TW",
            )
        )
    )

    assert token_manager.invalidated is True
    assert [request["token"] for request in requests_seen] == ["expired-token", "fresh-token"]
    assert payload.freshness == "fresh"
    assert payload.payload["latest_margin_balance"] == 1200.0


def test_finmind_background_provider_fatal_request_errors_fail_update(db_session: Session) -> None:
    def fake_get(url: str, *, params: dict, timeout: int):
        raise RuntimeError("network down")

    provider = FinMindBackgroundChipContextProvider(api_token="test-token", request_get=fake_get)

    result = update_background_chip_context_cache(
        db_session,
        run_date=date(2026, 6, 11),
        market="TW",
        provider=provider,
        symbols=["2330.TW"],
        context_types=["full_margin"],
    )

    assert result["status"] == "failed"
    assert result["records_written"] == 0
    assert result["errors"][0]["code"] == "background_context_provider_failed"
    assert result["errors"][0]["error_type"] == "_FinMindDatasetError"


def test_tdcc_weekly_major_holders_provider_parses_distribution_once_for_selected_symbols() -> None:
    calls: list[str] = []
    csv_text = """資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
20260605,2330  ,1,2160807,254859798,0.98
20260605,2330  ,9,3997,279407327,1.07
20260605,2330  ,12,563,276059662,1.06
20260605,2330  ,13,348,240919588,0.92
20260605,2330  ,14,209,186766182,0.72
20260605,2330  ,15,1499,22151774520,85.42
20260605,2330  ,17,2678648,25932524521,100.00
20260605,2454  ,12,100,1000000,2.50
20260605,2454  ,15,10,9000000,22.50
20260605,2454  ,17,2000,40000000,100.00
"""

    def fake_get(url: str, *, timeout: int, headers: dict):
        calls.append(url)
        assert headers["User-Agent"] == "ai-stock-sentinel/1.0"
        return _FakeTextResponse(csv_text)

    provider = TdccWeeklyMajorHoldersProvider(request_get=fake_get)

    payloads = list(
        provider.fetch(
            symbols=["2330.TW", "2454.TW", "9999.TW"],
            context_types=["weekly_major_holders"],
            run_date=date(2026, 6, 11),
            market="TW",
        )
    )

    assert len(calls) == 1
    by_symbol = {payload.symbol: payload for payload in payloads}
    tsmc = by_symbol["2330.TW"]
    assert tsmc.context_type == "weekly_major_holders"
    assert tsmc.freshness == "fresh"
    assert tsmc.as_of_date == date(2026, 6, 5)
    assert tsmc.applicable_consumers == FINMIND_BACKGROUND_CONTEXT_CONSUMERS
    assert tsmc.source["tls_verify"] is True
    assert tsmc.source["tls_hostname_check"] is True
    assert tsmc.source["tls_x509_strict"] is False
    assert tsmc.payload["holder_level_schema_version"] == "tdcc-holder-level-v2"
    assert tsmc.payload["holder_level_schema"] == {
        "12": "approximately 400 to 600 lots",
        "13": "approximately 600 to 800 lots",
        "14": "approximately 800 to 1000 lots",
        "15": "approximately 1000 lots or more",
    }
    assert tsmc.payload["thousand_lot_holder_levels"] == [15]
    assert tsmc.payload["large_holder_400_lot_plus_levels"] == [12, 13, 14, 15]
    assert tsmc.payload["retail_100_lot_or_less_levels"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert tsmc.payload["thousand_lot_holder_ratio"] == pytest.approx(85.42)
    assert tsmc.payload["large_holder_400_lot_plus_ratio"] == pytest.approx(88.12)
    assert tsmc.payload["retail_100_lot_or_less_ratio"] == pytest.approx(2.05)
    assert tsmc.payload["major_holder_levels"] == [12, 13, 14, 15]
    assert tsmc.payload["major_holder_ratio"] == pytest.approx(88.12)
    assert tsmc.payload["major_holder_people"] == 2619
    assert tsmc.payload["major_holder_shares"] == 22855519952
    assert tsmc.payload["retail_holder_ratio"] == pytest.approx(2.05)
    assert tsmc.payload["total_people"] == 2678648
    assert tsmc.payload["total_shares"] == 25932524521
    assert tsmc.replay_key == "background_context:2330.TW:weekly_major_holders:2026-06-05"
    assert by_symbol["2454.TW"].payload["major_holder_ratio"] == pytest.approx(25.0)
    assert by_symbol["2454.TW"].payload["thousand_lot_holder_ratio"] == pytest.approx(22.5)
    assert by_symbol["2454.TW"].payload["large_holder_400_lot_plus_ratio"] == pytest.approx(25.0)
    assert by_symbol["9999.TW"].freshness == "missing"
    assert by_symbol["9999.TW"].missing_reason == "tdcc_symbol_not_found"


def test_tdcc_weekly_major_holders_provider_fails_closed_on_certificate_verify_failure(
    db_session: Session,
) -> None:
    requests_seen: list[dict] = []

    def fake_get(url: str, **kwargs):
        requests_seen.append(dict(kwargs))
        raise RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Missing Subject Key Identifier")

    provider = TdccWeeklyMajorHoldersProvider(request_get=fake_get)

    result = update_background_chip_context_cache(
        db_session,
        run_date=date(2026, 6, 11),
        market="TW",
        provider=provider,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders"],
    )

    assert requests_seen == [{"timeout": 30, "headers": {"User-Agent": "ai-stock-sentinel/1.0"}}]
    assert result["status"] == "failed"
    assert result["records_written"] == 0
    assert result["errors"][0]["code"] == "background_context_provider_failed"
    assert result["errors"][0]["error_type"] == "_TdccDatasetError"
    assert "CERTIFICATE_VERIFY_FAILED" in result["errors"][0]["message"]


def test_default_background_provider_writes_finmind_and_tdcc_contexts(db_session: Session) -> None:
    class FakeFinMindProvider:
        def fetch(self, *, symbols: list[str], context_types: list[str], run_date: date, market: str):
            return [
                BackgroundContextPayload(
                    symbol=symbol,
                    context_type=context_type,
                    applicable_consumers=FINMIND_BACKGROUND_CONTEXT_CONSUMERS,
                    source={"domain": "background_context", "provider": "fake_finmind", "market": market},
                    as_of_date=run_date,
                    freshness="fresh",
                    payload={"provider": "fake_finmind"},
                    missing_reason=None,
                    replay_key=f"background_context:{symbol}:{context_type}:{run_date.isoformat()}",
                )
                for symbol in symbols
                for context_type in context_types
            ]

    class FakeTdccProvider:
        def fetch(self, *, symbols: list[str], context_types: list[str], run_date: date, market: str):
            return [
                BackgroundContextPayload(
                    symbol=symbol,
                    context_type="weekly_major_holders",
                    applicable_consumers=FINMIND_BACKGROUND_CONTEXT_CONSUMERS,
                    source={"domain": "background_context", "provider": "fake_tdcc", "market": market},
                    as_of_date=run_date,
                    freshness="fresh",
                    payload={"major_holder_ratio": 42.0},
                    missing_reason=None,
                    replay_key=f"background_context:{symbol}:weekly_major_holders:{run_date.isoformat()}",
                )
                for symbol in symbols
            ]

    provider = DefaultBackgroundChipContextProvider(
        finmind_provider=FakeFinMindProvider(),  # type: ignore[arg-type]
        tdcc_provider=FakeTdccProvider(),  # type: ignore[arg-type]
    )

    result = update_background_chip_context_cache(
        db_session,
        run_date=date(2026, 6, 11),
        market="TW",
        provider=provider,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders", "lending", "full_margin"],
    )
    db_session.commit()

    assert result["status"] == "completed"
    assert result["records_written"] == 3
    traces = get_shared_background_context_trace_by_symbol(
        db_session,
        symbols=["2330.TW"],
        context_types=["weekly_major_holders", "lending", "full_margin"],
    )
    by_type = {context["context_type"]: context for context in traces["2330.TW"]}
    assert by_type["weekly_major_holders"]["source"]["provider"] == "fake_tdcc"
    assert by_type["weekly_major_holders"]["payload"] == {"major_holder_ratio": 42.0}
    assert by_type["lending"]["source"]["provider"] == "fake_finmind"
    assert by_type["full_margin"]["source"]["provider"] == "fake_finmind"


def test_chip_context_workflow_uses_internal_endpoint_and_existing_secrets() -> None:
    workflow = Path(__file__).parents[2].joinpath(".github", "workflows", "daily-radar-chip-context.yml")
    text = workflow.read_text(encoding="utf-8")

    assert "schedule:" in text
    assert "/internal/daily-radar/chip-context/update" in text
    assert "${{ secrets.ZEABUR_BACKEND_URL }}" in text
    assert "${{ secrets.DAILY_RADAR_INTERNAL_TOKEN }}" in text
    assert "Authorization: Bearer ${DAILY_RADAR_INTERNAL_TOKEN}" in text
    assert "context_group:" in text
    assert "default: daily" in text
    assert "- daily" in text
    assert "- weekly" in text
    assert "- all" in text
    assert "0 23 * * 1-5" not in text
    assert "30 23 * * 6" in text
    assert "inputs.context_group == 'daily'" in text
    assert "inputs.context_group == 'weekly'" in text
    assert "inputs.context_group == 'all'" in text
    assert "github.event.schedule == '30 23 * * 6'" in text
    assert 'CHIP_CONTEXT_PAYLOAD: \'{"market":"TW","context_types":["lending","full_margin"]}\'' in text
    assert 'CHIP_CONTEXT_PAYLOAD: \'{"market":"TW","context_types":["weekly_major_holders"]}\'' in text
    assert "--data \"${CHIP_CONTEXT_PAYLOAD}\"" in text
    assert ".status == \"completed\"" in text
    assert "sk-" not in text
    assert "token=" not in text.lower()


def test_daily_radar_workflow_splits_data_fetching_steps_by_taipei_schedule() -> None:
    workflow = Path(__file__).parents[2].joinpath(".github", "workflows", "daily-radar.yml")
    text = workflow.read_text(encoding="utf-8")

    assert "GitHub cron uses UTC. Taiwan time = UTC+8." in text
    assert 'cron: "0 10 * * 1-5"' in text  # 18:00 TWT prepare universe
    assert 'cron: "0 11 * * 1-5"' in text  # 19:00 TWT AVWAP
    assert 'cron: "0 12 * * 1-5"' in text  # 20:00 TWT lending
    assert 'cron: "30 13 * * 1-5"' in text  # 21:30 TWT full margin
    assert 'cron: "30 14 * * 1-5"' in text  # 22:30 TWT OHLCV
    assert 'cron: "30 15 * * 1-5"' in text  # 23:30 TWT market context
    assert 'cron: "30 16 * * 1-5"' in text  # 00:30 TWT next day scoring
    assert 'cron: "0 23 * * 1-5"' in text  # 07:00 TWT next day AVWAP repair
    assert "intended Taiwan trading date" in text
    assert "run_date:" in text
    assert "DAILY_RADAR_RUN_DATE: ${{ github.event.inputs.run_date || '' }}" in text
    assert text.count("DAILY_RADAR_SCHEDULE: ${{ github.event.schedule || '' }}") == 8
    assert text.count("scheduled_run_date()") == 8
    assert text.count('run_date="$(scheduled_run_date)"') == 8
    assert text.count('elif [[ -n "${DAILY_RADAR_RUN_DATE}" ]]') == 8
    assert 'date -u -d "1 day ago" +%F' in text
    assert 'run_date="$(TZ=Asia/Taipei date +%F)"' in text
    assert "daily_radar_payload=" in text
    assert '\\"run_date\\":\\"${run_date}\\"' in text
    assert "/internal/daily-radar/prepare-universe" in text
    assert "/internal/daily-radar/refresh-avwap" in text
    assert "/internal/daily-radar/refresh-lending" in text
    assert "/internal/daily-radar/refresh-full-margin" in text
    assert "/internal/daily-radar/refresh-ohlcv" in text
    assert "/internal/daily-radar/refresh-market-context" in text
    assert "/internal/daily-radar/run-scoring" in text
    assert "repair-avwap-and-rescore" in text
    assert "missing_symbol_reasons" in text
    assert "skipped_symbol_reasons" in text
    assert "date -d 'yesterday'" not in text
    assert "DAILY_RADAR_PAYLOAD" not in text
    assert '--data "${daily_radar_payload}"' in text
    assert "DAILY_RADAR_ENDPOINT: /internal/daily-radar/run\n" not in text
