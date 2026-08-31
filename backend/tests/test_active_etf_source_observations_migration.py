from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

from ai_stock_sentinel.db.models import (
    ActiveEtfHoldingSnapshot,
    ActiveEtfSourceHolding,
    ActiveEtfSourceObservation,
)


def _load_migration() -> ModuleType:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "aa1b2c3d4e5f_add_active_etf_source_observations.py"
    )
    spec = importlib.util.spec_from_file_location("active_etf_source_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_migration_sql(direction: str) -> str:
    migration = _load_migration()
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


def test_source_observation_migration_archives_independent_provider_payloads() -> None:
    sql = _render_migration_sql("upgrade")

    assert "CREATE TABLE active_etf_source_observations" in sql
    assert "CREATE TABLE active_etf_source_holdings" in sql
    assert "raw_payload_gzip BYTEA NOT NULL" in sql
    assert "uq_active_etf_observation_fund_date_provider" in sql
    assert "uq_active_etf_source_holding_observation_symbol" in sql
    assert "verification_status" in sql
    assert "source_count" in sql
    assert "raw_size_bytes >= 0 AND raw_size_bytes <= 5000000" in sql


def test_source_observation_migration_drops_children_before_parent() -> None:
    sql = _render_migration_sql("downgrade")

    holdings_position = sql.index("DROP TABLE active_etf_source_holdings")
    observations_position = sql.index("DROP TABLE active_etf_source_observations")
    assert holdings_position < observations_position


def test_source_observation_models_preserve_provider_specific_rows() -> None:
    snapshot_constraints = {
        constraint.name for constraint in ActiveEtfHoldingSnapshot.__table__.constraints
    }
    observation_constraints = {
        constraint.name for constraint in ActiveEtfSourceObservation.__table__.constraints
    }

    assert ActiveEtfSourceObservation.__table__.primary_key.columns.keys() == ["id"]
    assert ActiveEtfSourceHolding.__table__.primary_key.columns.keys() == ["id"]
    assert "ck_active_etf_snapshot_verification_status" in snapshot_constraints
    assert "uq_active_etf_observation_fund_date_provider" in observation_constraints
