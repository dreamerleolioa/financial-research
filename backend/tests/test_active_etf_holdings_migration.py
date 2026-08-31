from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

from ai_stock_sentinel.db.models import (
    ActiveEtfFund,
    ActiveEtfHolding,
    ActiveEtfHoldingSnapshot,
)


def _load_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1]
        .joinpath("alembic", "versions")
        .glob("*_add_active_etf_holding_snapshots.py")
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("active_etf_holdings_migration", migration_paths[0])
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


def test_active_etf_holdings_migration_creates_replayable_snapshot_tables() -> None:
    sql = _render_migration_sql("upgrade")

    assert "CREATE TABLE active_etf_funds" in sql
    assert "CREATE TABLE active_etf_holding_snapshots" in sql
    assert "CREATE TABLE active_etf_holdings" in sql
    assert "CONSTRAINT uq_active_etf_snapshot_fund_date UNIQUE" in sql
    assert "CONSTRAINT uq_active_etf_holding_snapshot_symbol UNIQUE" in sql
    assert "CONSTRAINT ck_active_etf_holding_weight_pct CHECK" in sql
    assert "REFERENCES active_etf_funds (fund_code) ON DELETE RESTRICT" in sql
    assert "REFERENCES active_etf_holding_snapshots (id) ON DELETE CASCADE" in sql
    assert "JSONB" in sql


def test_active_etf_holdings_migration_downgrade_removes_children_first() -> None:
    sql = _render_migration_sql("downgrade")

    holdings_position = sql.index("DROP TABLE active_etf_holdings")
    snapshots_position = sql.index("DROP TABLE active_etf_holding_snapshots")
    funds_position = sql.index("DROP TABLE active_etf_funds")
    assert holdings_position < snapshots_position < funds_position


def test_active_etf_models_keep_one_canonical_snapshot_and_integer_row_identity() -> None:
    snapshot_constraints = {
        constraint.name for constraint in ActiveEtfHoldingSnapshot.__table__.constraints
    }

    assert ActiveEtfFund.__table__.primary_key.columns.keys() == ["fund_code"]
    assert ActiveEtfHoldingSnapshot.__table__.primary_key.columns.keys() == ["id"]
    assert ActiveEtfHolding.__table__.primary_key.columns.keys() == ["id"]
    assert str(ActiveEtfHolding.__table__.c.id.type) == "INTEGER"
    assert "uq_active_etf_snapshot_fund_date" in snapshot_constraints
