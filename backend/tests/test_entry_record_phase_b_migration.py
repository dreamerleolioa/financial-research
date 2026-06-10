from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_mock_engine


def _load_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob("*_add_entry_record_phase_b_lifecycle_fields.py")
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("entry_record_phase_b_migration", migration_paths[0])
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _render_migration_sql(direction: str) -> str:
    statements: list[str] = []
    engine = create_mock_engine("postgresql://", lambda sql, *_multiparams, **_params: statements.append(str(sql.compile(dialect=engine.dialect))))
    migration_context = MigrationContext.configure(engine.connect())
    operations = Operations(migration_context)
    migration = _load_migration()
    original_op = migration.op
    migration.op = operations
    try:
        getattr(migration, direction)()
    finally:
        migration.op = original_op
    return "\n".join(statements)


def test_entry_record_phase_b_migration_adds_nullable_plan_columns_and_constraints() -> None:
    sql = _render_migration_sql("upgrade")

    assert "ADD COLUMN default_stop_rule" in sql
    assert "ADD COLUMN add_entry_condition" in sql
    assert "DROP CONSTRAINT ck_position_lifecycle_plan_holding_period" in sql
    assert "ck_position_lifecycle_plan_holding_period" in sql
    assert "planned_holding_period IS NULL OR planned_holding_period IN ('short_term', 'swing', 'medium_term', 'long_term', 'not_recorded')" in sql
    assert "ck_position_lifecycle_plan_default_stop_rule" in sql
    assert "ck_position_lifecycle_plan_add_entry_condition" in sql
    assert "break_ma20" in sql
    assert "pullback_holds_ma20" in sql
    assert "ck_position_event_reason_code" in sql
    assert "event_or_news_catalyst" in sql
    assert "UPDATE position_lifecycle_plan" not in sql


def test_entry_record_phase_b_migration_drops_constraints_then_columns() -> None:
    sql = _render_migration_sql("downgrade")

    assert "ck_position_lifecycle_plan_add_entry_condition" in sql
    assert "ck_position_lifecycle_plan_default_stop_rule" in sql
    assert "DROP COLUMN add_entry_condition" in sql
    assert "DROP COLUMN default_stop_rule" in sql
    assert "DROP CONSTRAINT ck_position_lifecycle_plan_holding_period" in sql
    assert "planned_holding_period IS NULL OR planned_holding_period IN ('short_term', 'swing', 'medium_term', 'long_term')" in sql
    assert "planned_holding_period IN ('short_term', 'swing', 'medium_term', 'long_term', 'not_recorded')" not in sql
