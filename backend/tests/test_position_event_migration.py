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
from ai_stock_sentinel.user_models.user import User


def _load_position_event_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob("*_add_position_event_ledger.py")
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("position_event_ledger_migration", migration_paths[0])
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_position_event_migration_backfill_is_synthetic_idempotent_and_intent_free() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_position_event_migration()

    with Session(engine) as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add(UserPortfolio(
            id=1,
            user_id=1,
            position_group_id="group-active-partial",
            symbol="2330.TW",
            entry_price=900,
            quantity=60,
            entry_date=date(2026, 1, 1),
            is_active=True,
        ))
        session.add(UserPortfolio(
            id=2,
            user_id=1,
            position_group_id="group-active-partial",
            symbol="2330.TW",
            entry_price=900,
            quantity=40,
            entry_date=date(2026, 1, 1),
            is_active=False,
            exit_date=date(2026, 1, 5),
            exit_price=950,
            exit_quantity=40,
            exit_fees=10,
            exit_taxes=5,
            notes="legacy note",
        ))
        session.add(UserPortfolio(
            id=3,
            user_id=1,
            position_group_id="group-full",
            symbol="2454.TW",
            entry_price=800,
            quantity=50,
            entry_date=date(2026, 2, 1),
            is_active=False,
            exit_date=date(2026, 2, 10),
            exit_price=850,
            exit_quantity=50,
        ))
        session.add(UserPortfolio(
            id=4,
            user_id=1,
            position_group_id="group-multi-closed",
            symbol="2317.TW",
            entry_price=100,
            quantity=30,
            entry_date=date(2026, 3, 1),
            is_active=False,
            exit_date=date(2026, 3, 5),
            exit_price=110,
            exit_quantity=30,
        ))
        session.add(UserPortfolio(
            id=5,
            user_id=1,
            position_group_id="group-multi-closed",
            symbol="2317.TW",
            entry_price=100,
            quantity=70,
            entry_date=date(2026, 3, 1),
            is_active=False,
            exit_date=date(2026, 3, 10),
            exit_price=120,
            exit_quantity=70,
        ))
        session.commit()

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._backfill_position_events()
        migration._backfill_position_events()
        events = session.execute(select(PositionEvent).order_by(PositionEvent.position_group_id, PositionEvent.event_type)).scalars().all()

    assert [(event.position_group_id, event.event_type) for event in events] == [
        ("group-active-partial", "initial_entry"),
        ("group-active-partial", "partial_exit"),
        ("group-full", "full_exit"),
        ("group-full", "initial_entry"),
        ("group-multi-closed", "full_exit"),
        ("group-multi-closed", "initial_entry"),
        ("group-multi-closed", "partial_exit"),
    ]
    assert {event.source for event in events} == {"synthetic_from_portfolio_row"}
    assert all(event.data_quality_note for event in events)
    assert all("Synthetic event generated from legacy user_portfolio row" in event.data_quality_note for event in events)
    assert all("reason" in event.data_quality_note and "plan" in event.data_quality_note and "intent" in event.data_quality_note for event in events)
    assert all("because" not in event.data_quality_note.lower() for event in events)
