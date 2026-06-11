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
    update_background_chip_context_cache,
)
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
    assert "CONSTRAINT uq_shared_background_context_symbol_type UNIQUE (symbol, context_type)" in sql
    assert "CONSTRAINT ck_shared_background_context_freshness CHECK" in sql
    assert "CREATE INDEX idx_shared_background_context_symbol" in sql
    assert "CREATE INDEX idx_shared_background_context_context_type" in sql
    assert "CREATE INDEX idx_shared_background_context_as_of_date" in sql
    assert "CREATE INDEX idx_shared_background_context_freshness" in sql


def test_background_context_migration_downgrade_drops_indexes_before_table() -> None:
    sql = _render_migration_sql("downgrade")

    assert sql.index("DROP INDEX idx_shared_background_context_freshness") < sql.index(
        "DROP TABLE shared_background_contexts"
    )


def test_upsert_shared_background_context_updates_latest_symbol_type_cache(db_session: Session) -> None:
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
    db_session.commit()

    assert second.id == first.id
    rows = db_session.query(SharedBackgroundContext).all()
    assert len(rows) == 1
    assert rows[0].freshness == "fresh"
    assert rows[0].payload == {"major_holder_ratio": 0.61}
    assert rows[0].applicable_consumers == ["daily_radar", "individual_analysis"]
    assert rows[0].replay_key == "background_context:2330.TW:weekly_major_holders:2026-05-31"


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


def test_chip_context_workflow_uses_internal_endpoint_and_existing_secrets() -> None:
    workflow = Path(__file__).parents[2].joinpath(".github", "workflows", "daily-radar-chip-context.yml")
    text = workflow.read_text(encoding="utf-8")

    assert "schedule:" in text
    assert "/internal/daily-radar/chip-context/update" in text
    assert "${{ secrets.ZEABUR_BACKEND_URL }}" in text
    assert "${{ secrets.DAILY_RADAR_INTERNAL_TOKEN }}" in text
    assert "Authorization: Bearer ${DAILY_RADAR_INTERNAL_TOKEN}" in text
    assert "sk-" not in text
    assert "token=" not in text.lower()
