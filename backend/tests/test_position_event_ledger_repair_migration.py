from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.db.models import PositionEvent, UserPortfolio
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.portfolio.application.events import ledger_open_quantity
from ai_stock_sentinel.user_models.user import User


def _load_repair_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob("*_repair_synthetic_split_ledger_quantity.py")
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("position_event_ledger_repair_migration", migration_paths[0])
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_repair_migration_restores_only_safe_synthetic_active_group_quantity() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()

    with Session(engine) as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add_all([
            UserPortfolio(
                id=1,
                user_id=1,
                position_group_id="group-safe-synthetic",
                symbol="2330.TW",
                entry_price=900,
                quantity=60,
                entry_date=date(2026, 1, 1),
                is_active=True,
            ),
            UserPortfolio(
                id=2,
                user_id=1,
                position_group_id="group-safe-synthetic",
                symbol="2330.TW",
                entry_price=900,
                quantity=40,
                entry_date=date(2026, 1, 1),
                is_active=False,
                exit_date=date(2026, 1, 5),
                exit_price=950,
                exit_quantity=40,
            ),
            UserPortfolio(
                id=3,
                user_id=1,
                position_group_id="group-mixed-source",
                symbol="2454.TW",
                entry_price=800,
                quantity=60,
                entry_date=date(2026, 2, 1),
                is_active=True,
            ),
            UserPortfolio(
                id=4,
                user_id=1,
                position_group_id="group-mixed-source",
                symbol="2454.TW",
                entry_price=800,
                quantity=40,
                entry_date=date(2026, 2, 1),
                is_active=False,
                exit_date=date(2026, 2, 5),
                exit_price=850,
                exit_quantity=40,
            ),
        ])
        session.flush()
        session.add_all([
            PositionEvent(
                user_id=1,
                position_group_id="group-safe-synthetic",
                symbol="2330.TW",
                event_type="initial_entry",
                event_date=date(2026, 1, 1),
                price=900,
                quantity=60,
                fees=0,
                taxes=0,
                source_portfolio_id=1,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-safe-synthetic",
                symbol="2330.TW",
                event_type="partial_exit",
                event_date=date(2026, 1, 5),
                price=950,
                quantity=40,
                fees=0,
                taxes=0,
                source_portfolio_id=2,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-mixed-source",
                symbol="2454.TW",
                event_type="initial_entry",
                event_date=date(2026, 2, 1),
                price=800,
                quantity=60,
                fees=0,
                taxes=0,
                source_portfolio_id=3,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-mixed-source",
                symbol="2454.TW",
                event_type="partial_exit",
                event_date=date(2026, 2, 5),
                price=850,
                quantity=40,
                fees=0,
                taxes=0,
                source_portfolio_id=4,
                source="user_recorded_at_event_time",
            ),
        ])
        session.commit()

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._repair_synthetic_active_group_quantities()
        migration._repair_synthetic_active_group_quantities()

        safe_events = list(session.execute(
            select(PositionEvent)
            .where(PositionEvent.position_group_id == "group-safe-synthetic")
            .order_by(PositionEvent.event_date, PositionEvent.id)
        ).scalars().all())
        mixed_initial = session.execute(
            select(PositionEvent).where(
                PositionEvent.position_group_id == "group-mixed-source",
                PositionEvent.event_type == "initial_entry",
            )
        ).scalar_one()

        assert safe_events[0].quantity == 100
        assert ledger_open_quantity(safe_events) == 60
        assert mixed_initial.quantity == 60
