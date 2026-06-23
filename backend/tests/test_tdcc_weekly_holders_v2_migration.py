from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

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


def _load_tdcc_v2_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob(
            "*_backfill_tdcc_weekly_holders_v2_payload.py"
        )
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("tdcc_weekly_holders_v2_migration", migration_paths[0])
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tdcc_weekly_holders_v2_migration_backfills_valid_distribution_and_preserves_legacy_fields(
    db_session: Session,
) -> None:
    weekly = _context(
        symbol="2330.TW",
        context_type="weekly_major_holders",
        payload={
            "major_holder_ratio": 88.12,
            "distribution": [
                {"level": 1, "people": 2160807, "shares": 254859798, "ratio": 0.98},
                {"level": 9, "people": 3997, "shares": 279407327, "ratio": 1.07},
                {"level": 12, "people": 563, "shares": 276059662, "ratio": 1.06},
                {"level": 13, "people": 348, "shares": 240919588, "ratio": 0.92},
                {"level": 14, "people": 209, "shares": 186766182, "ratio": 0.72},
                {"level": 15, "people": 1499, "shares": 22151774520, "ratio": 85.42},
                {"level": 17, "people": 2678648, "shares": 25932524521, "ratio": 100.0},
            ],
        },
        replay_key="background_context:2330.TW:weekly_major_holders:2026-06-05",
    )
    non_weekly = _context(
        symbol="2330.TW",
        context_type="lending",
        payload={
            "distribution": [{"level": 15, "ratio": 85.42}],
        },
        replay_key="background_context:2330.TW:lending:2026-06-05",
    )
    db_session.add_all([weekly, non_weekly])
    db_session.flush()

    migration = _load_tdcc_v2_migration()
    migration._backfill_weekly_major_holders_payloads(db_session.connection())
    db_session.expire_all()

    updated_weekly = db_session.get(SharedBackgroundContext, weekly.id)
    assert updated_weekly is not None
    assert updated_weekly.replay_key == "background_context:2330.TW:weekly_major_holders:2026-06-05"
    assert updated_weekly.payload["holder_level_schema_version"] == "tdcc-holder-level-v2"
    assert updated_weekly.payload["thousand_lot_holder_ratio"] == pytest.approx(85.42)
    assert updated_weekly.payload["large_holder_400_lot_plus_ratio"] == pytest.approx(88.12)
    assert updated_weekly.payload["retail_100_lot_or_less_ratio"] == pytest.approx(2.05)
    assert updated_weekly.payload["major_holder_ratio"] == pytest.approx(88.12)

    untouched = db_session.get(SharedBackgroundContext, non_weekly.id)
    assert untouched is not None
    assert untouched.payload == {"distribution": [{"level": 15, "ratio": 85.42}]}


def test_tdcc_weekly_holders_v2_migration_skips_invalid_distribution_payloads(
    db_session: Session,
) -> None:
    missing_distribution = _context(
        symbol="1101.TW",
        context_type="weekly_major_holders",
        payload={"major_holder_ratio": 20.0},
        replay_key="background_context:1101.TW:weekly_major_holders:2026-06-05",
    )
    malformed_distribution = _context(
        symbol="1102.TW",
        context_type="weekly_major_holders",
        payload={"distribution": [{"level": "not-a-level", "ratio": 20.0}]},
        replay_key="background_context:1102.TW:weekly_major_holders:2026-06-05",
    )
    db_session.add_all([missing_distribution, malformed_distribution])
    db_session.flush()

    migration = _load_tdcc_v2_migration()
    migration._backfill_weekly_major_holders_payloads(db_session.connection())
    db_session.expire_all()

    skipped_missing = db_session.get(SharedBackgroundContext, missing_distribution.id)
    skipped_malformed = db_session.get(SharedBackgroundContext, malformed_distribution.id)
    assert skipped_missing is not None
    assert skipped_malformed is not None
    assert skipped_missing.payload == {"major_holder_ratio": 20.0}
    assert skipped_malformed.payload == {"distribution": [{"level": "not-a-level", "ratio": 20.0}]}


def test_tdcc_weekly_holders_v2_migration_is_idempotent(
    db_session: Session,
) -> None:
    weekly = _context(
        symbol="2454.TW",
        context_type="weekly_major_holders",
        payload={
            "major_holder_ratio": 25.0,
            "distribution": [
                {"level": "12", "ratio": "2.5"},
                {"level": "15", "ratio": "22.5"},
                {"level": "17", "ratio": "100.0"},
            ],
        },
        replay_key="background_context:2454.TW:weekly_major_holders:2026-06-05",
    )
    db_session.add(weekly)
    db_session.flush()

    migration = _load_tdcc_v2_migration()
    migration._backfill_weekly_major_holders_payloads(db_session.connection())
    db_session.expire_all()
    after_first = dict(db_session.get(SharedBackgroundContext, weekly.id).payload)  # type: ignore[union-attr]

    migration._backfill_weekly_major_holders_payloads(db_session.connection())
    db_session.expire_all()
    after_second_row = db_session.get(SharedBackgroundContext, weekly.id)
    assert after_second_row is not None

    assert db_session.query(SharedBackgroundContext).count() == 1
    assert after_second_row.replay_key == "background_context:2454.TW:weekly_major_holders:2026-06-05"
    assert after_second_row.payload == after_first
    assert after_second_row.payload["thousand_lot_holder_ratio"] == pytest.approx(22.5)
    assert after_second_row.payload["large_holder_400_lot_plus_ratio"] == pytest.approx(25.0)
    assert after_second_row.payload["retail_100_lot_or_less_ratio"] is None


def _context(
    *,
    symbol: str,
    context_type: str,
    payload: dict,
    replay_key: str,
) -> SharedBackgroundContext:
    return SharedBackgroundContext(
        symbol=symbol,
        context_type=context_type,
        applicable_consumers=["daily_radar"],
        source={"domain": "background_context", "provider": "fixture", "market": "TW"},
        as_of_date=date(2026, 6, 5),
        freshness="fresh",
        payload=payload,
        missing_reason=None,
        replay_key=replay_key,
    )
