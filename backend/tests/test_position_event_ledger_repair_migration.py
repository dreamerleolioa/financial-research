from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
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
        migration._repair_synthetic_group_quantities()
        migration._repair_synthetic_group_quantities()

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


def test_repair_migration_restores_safe_synthetic_fully_closed_group_quantity() -> None:
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
                position_group_id="group-fully-closed",
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
                id=2,
                user_id=1,
                position_group_id="group-fully-closed",
                symbol="2330.TW",
                entry_price=900,
                quantity=60,
                entry_date=date(2026, 1, 1),
                is_active=False,
                exit_date=date(2026, 1, 10),
                exit_price=980,
                exit_quantity=60,
            ),
            UserPortfolio(
                id=3,
                user_id=1,
                position_group_id="group-fully-closed-mixed",
                symbol="2454.TW",
                entry_price=800,
                quantity=40,
                entry_date=date(2026, 2, 1),
                is_active=False,
                exit_date=date(2026, 2, 5),
                exit_price=850,
                exit_quantity=40,
            ),
            UserPortfolio(
                id=4,
                user_id=1,
                position_group_id="group-fully-closed-mixed",
                symbol="2454.TW",
                entry_price=800,
                quantity=60,
                entry_date=date(2026, 2, 1),
                is_active=False,
                exit_date=date(2026, 2, 10),
                exit_price=880,
                exit_quantity=60,
            ),
        ])
        session.flush()
        session.add_all([
            PositionEvent(
                user_id=1,
                position_group_id="group-fully-closed",
                symbol="2330.TW",
                event_type="initial_entry",
                event_date=date(2026, 1, 1),
                price=900,
                quantity=60,
                fees=0,
                taxes=0,
                source_portfolio_id=2,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-fully-closed",
                symbol="2330.TW",
                event_type="partial_exit",
                event_date=date(2026, 1, 5),
                price=950,
                quantity=40,
                fees=0,
                taxes=0,
                source_portfolio_id=1,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-fully-closed",
                symbol="2330.TW",
                event_type="full_exit",
                event_date=date(2026, 1, 10),
                price=980,
                quantity=60,
                fees=0,
                taxes=0,
                source_portfolio_id=2,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-fully-closed-mixed",
                symbol="2454.TW",
                event_type="initial_entry",
                event_date=date(2026, 2, 1),
                price=800,
                quantity=60,
                fees=0,
                taxes=0,
                source_portfolio_id=4,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-fully-closed-mixed",
                symbol="2454.TW",
                event_type="partial_exit",
                event_date=date(2026, 2, 5),
                price=850,
                quantity=40,
                fees=0,
                taxes=0,
                source_portfolio_id=3,
                source="user_recorded_at_event_time",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-fully-closed-mixed",
                symbol="2454.TW",
                event_type="full_exit",
                event_date=date(2026, 2, 10),
                price=880,
                quantity=60,
                fees=0,
                taxes=0,
                source_portfolio_id=4,
                source="synthetic_from_portfolio_row",
            ),
        ])
        session.commit()

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._repair_synthetic_group_quantities()
        migration._repair_synthetic_group_quantities()

        safe_events = list(session.execute(
            select(PositionEvent)
            .where(PositionEvent.position_group_id == "group-fully-closed")
            .order_by(PositionEvent.event_date, PositionEvent.id)
        ).scalars().all())
        mixed_events = list(session.execute(
            select(PositionEvent)
            .where(PositionEvent.position_group_id == "group-fully-closed-mixed")
            .order_by(PositionEvent.event_date, PositionEvent.id)
        ).scalars().all())

        assert safe_events[0].quantity == 100
        assert ledger_open_quantity(safe_events) == 0
        assert mixed_events[0].quantity == 60


def test_repair_migration_requires_exact_portfolio_source_coverage() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()

    def portfolio_row(
        *,
        row_id: int,
        group_id: str,
        symbol: str,
        quantity: int,
        is_active: bool,
        exit_day: int | None = None,
        exit_quantity: int | None = None,
    ) -> UserPortfolio:
        return UserPortfolio(
            id=row_id,
            user_id=1,
            position_group_id=group_id,
            symbol=symbol,
            entry_price=900,
            quantity=quantity,
            entry_date=date(2026, 3, 1),
            is_active=is_active,
            exit_date=date(2026, 3, exit_day) if exit_day is not None else None,
            exit_price=950 if exit_day is not None else None,
            exit_quantity=(quantity if exit_quantity is None else exit_quantity)
            if exit_day is not None
            else None,
        )

    def synthetic_event(
        *,
        group_id: str,
        symbol: str,
        event_type: str,
        event_day: int,
        quantity: int,
        source_portfolio_id: int,
    ) -> PositionEvent:
        return PositionEvent(
            user_id=1,
            position_group_id=group_id,
            symbol=symbol,
            event_type=event_type,
            event_date=date(2026, 3, event_day),
            price=900,
            quantity=quantity,
            fees=0,
            taxes=0,
            source_portfolio_id=source_portfolio_id,
            source="synthetic_from_portfolio_row",
        )

    with Session(engine) as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add_all([
            portfolio_row(
                row_id=10,
                group_id="group-active-omitted-source",
                symbol="2330.TW",
                quantity=60,
                is_active=True,
            ),
            portfolio_row(
                row_id=11,
                group_id="group-active-omitted-source",
                symbol="2330.TW",
                quantity=40,
                is_active=False,
                exit_day=5,
            ),
            portfolio_row(
                row_id=12,
                group_id="group-active-omitted-source",
                symbol="2330.TW",
                quantity=20,
                is_active=False,
                exit_day=6,
            ),
            portfolio_row(
                row_id=20,
                group_id="group-closed-duplicate-source",
                symbol="2454.TW",
                quantity=20,
                is_active=False,
                exit_day=7,
            ),
            portfolio_row(
                row_id=21,
                group_id="group-closed-duplicate-source",
                symbol="2454.TW",
                quantity=20,
                is_active=False,
                exit_day=8,
            ),
            portfolio_row(
                row_id=22,
                group_id="group-closed-duplicate-source",
                symbol="2454.TW",
                quantity=60,
                is_active=False,
                exit_day=10,
            ),
            portfolio_row(
                row_id=30,
                group_id="group-active-row-quantity-mismatch",
                symbol="2317.TW",
                quantity=60,
                is_active=True,
            ),
            portfolio_row(
                row_id=31,
                group_id="group-active-row-quantity-mismatch",
                symbol="2317.TW",
                quantity=40,
                is_active=False,
                exit_day=5,
                exit_quantity=30,
            ),
            portfolio_row(
                row_id=40,
                group_id="group-active-event-quantity-mismatch",
                symbol="2308.TW",
                quantity=60,
                is_active=True,
            ),
            portfolio_row(
                row_id=41,
                group_id="group-active-event-quantity-mismatch",
                symbol="2308.TW",
                quantity=40,
                is_active=False,
                exit_day=5,
            ),
        ])
        session.flush()
        session.add_all([
            synthetic_event(
                group_id="group-active-omitted-source",
                symbol="2330.TW",
                event_type="initial_entry",
                event_day=1,
                quantity=60,
                source_portfolio_id=10,
            ),
            synthetic_event(
                group_id="group-active-omitted-source",
                symbol="2330.TW",
                event_type="partial_exit",
                event_day=5,
                quantity=40,
                source_portfolio_id=11,
            ),
            synthetic_event(
                group_id="group-closed-duplicate-source",
                symbol="2454.TW",
                event_type="initial_entry",
                event_day=1,
                quantity=60,
                source_portfolio_id=22,
            ),
            synthetic_event(
                group_id="group-active-row-quantity-mismatch",
                symbol="2317.TW",
                event_type="initial_entry",
                event_day=1,
                quantity=60,
                source_portfolio_id=30,
            ),
            synthetic_event(
                group_id="group-active-row-quantity-mismatch",
                symbol="2317.TW",
                event_type="partial_exit",
                event_day=5,
                quantity=30,
                source_portfolio_id=31,
            ),
            synthetic_event(
                group_id="group-active-event-quantity-mismatch",
                symbol="2308.TW",
                event_type="initial_entry",
                event_day=1,
                quantity=60,
                source_portfolio_id=40,
            ),
            synthetic_event(
                group_id="group-active-event-quantity-mismatch",
                symbol="2308.TW",
                event_type="partial_exit",
                event_day=5,
                quantity=30,
                source_portfolio_id=41,
            ),
            synthetic_event(
                group_id="group-closed-duplicate-source",
                symbol="2454.TW",
                event_type="partial_exit",
                event_day=7,
                quantity=20,
                source_portfolio_id=20,
            ),
            synthetic_event(
                group_id="group-closed-duplicate-source",
                symbol="2454.TW",
                event_type="partial_exit",
                event_day=8,
                quantity=20,
                source_portfolio_id=20,
            ),
            synthetic_event(
                group_id="group-closed-duplicate-source",
                symbol="2454.TW",
                event_type="full_exit",
                event_day=10,
                quantity=60,
                source_portfolio_id=22,
            ),
        ])
        session.commit()

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._repair_synthetic_group_quantities()
        migration._repair_synthetic_group_quantities()

        omitted_events = list(session.execute(
            select(PositionEvent)
            .where(PositionEvent.position_group_id == "group-active-omitted-source")
            .order_by(PositionEvent.event_date, PositionEvent.id)
        ).scalars().all())
        duplicate_events = list(session.execute(
            select(PositionEvent)
            .where(PositionEvent.position_group_id == "group-closed-duplicate-source")
            .order_by(PositionEvent.event_date, PositionEvent.id)
        ).scalars().all())
        row_mismatch_events = list(session.execute(
            select(PositionEvent)
            .where(PositionEvent.position_group_id == "group-active-row-quantity-mismatch")
            .order_by(PositionEvent.event_date, PositionEvent.id)
        ).scalars().all())
        event_mismatch_events = list(session.execute(
            select(PositionEvent)
            .where(PositionEvent.position_group_id == "group-active-event-quantity-mismatch")
            .order_by(PositionEvent.event_date, PositionEvent.id)
        ).scalars().all())

        assert omitted_events[0].quantity == 60
        assert ledger_open_quantity(omitted_events) == 20
        assert duplicate_events[0].quantity == 60
        assert ledger_open_quantity(duplicate_events) == -40
        assert row_mismatch_events[0].quantity == 60
        assert ledger_open_quantity(row_mismatch_events) == 30
        assert event_mismatch_events[0].quantity == 60
        assert ledger_open_quantity(event_mismatch_events) == 30


@pytest.mark.parametrize(
    (
        "case_name",
        "active_quantity",
        "active_entry_price",
        "active_entry_date",
        "initial_event_price",
        "initial_event_date",
    ),
    [
        ("negative-active-quantity", -10, 900, date(2026, 4, 1), 900, date(2026, 4, 1)),
        ("zero-active-quantity", 0, 900, date(2026, 4, 1), 900, date(2026, 4, 1)),
        ("entry-price-mismatch", 60, 950, date(2026, 4, 1), 900, date(2026, 4, 1)),
        ("entry-date-mismatch", 60, 900, date(2026, 4, 2), 900, date(2026, 4, 1)),
    ],
)
def test_repair_migration_rejects_invalid_active_source_facts(
    case_name: str,
    active_quantity: int,
    active_entry_price: int,
    active_entry_date: date,
    initial_event_price: int,
    initial_event_date: date,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()
    group_id = f"group-{case_name}"

    with Session(engine) as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add_all([
            UserPortfolio(
                id=1,
                user_id=1,
                position_group_id=group_id,
                symbol="2330.TW",
                entry_price=active_entry_price,
                quantity=active_quantity,
                entry_date=active_entry_date,
                is_active=True,
            ),
            UserPortfolio(
                id=2,
                user_id=1,
                position_group_id=group_id,
                symbol="2330.TW",
                entry_price=initial_event_price,
                quantity=40,
                entry_date=initial_event_date,
                is_active=False,
                exit_date=date(2026, 4, 5),
                exit_price=950,
                exit_quantity=40,
            ),
        ])
        session.flush()
        session.add_all([
            PositionEvent(
                user_id=1,
                position_group_id=group_id,
                symbol="2330.TW",
                event_type="initial_entry",
                event_date=initial_event_date,
                price=initial_event_price,
                quantity=60,
                fees=0,
                taxes=0,
                source_portfolio_id=1,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id=group_id,
                symbol="2330.TW",
                event_type="partial_exit",
                event_date=date(2026, 4, 5),
                price=950,
                quantity=40,
                fees=0,
                taxes=0,
                source_portfolio_id=2,
                source="synthetic_from_portfolio_row",
            ),
        ])
        session.commit()

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._repair_synthetic_group_quantities()
        migration._repair_synthetic_group_quantities()

        initial_event = session.execute(
            select(PositionEvent).where(
                PositionEvent.position_group_id == group_id,
                PositionEvent.event_type == "initial_entry",
            )
        ).scalar_one()
        assert initial_event.quantity == 60


def test_repair_migration_skips_postgresql_integer_overflow_totals() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()
    postgres_integer_max = 2_147_483_647
    assert migration.POSTGRES_INTEGER_MAX == postgres_integer_max

    with Session(engine) as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add_all([
            UserPortfolio(
                id=1,
                user_id=1,
                position_group_id="group-active-overflow",
                symbol="2330.TW",
                entry_price=900,
                quantity=postgres_integer_max,
                entry_date=date(2026, 5, 1),
                is_active=True,
            ),
            UserPortfolio(
                id=2,
                user_id=1,
                position_group_id="group-active-overflow",
                symbol="2330.TW",
                entry_price=900,
                quantity=40,
                entry_date=date(2026, 5, 1),
                is_active=False,
                exit_date=date(2026, 5, 5),
                exit_price=950,
                exit_quantity=40,
            ),
            UserPortfolio(
                id=3,
                user_id=1,
                position_group_id="group-closed-overflow",
                symbol="2454.TW",
                entry_price=800,
                quantity=40,
                entry_date=date(2026, 6, 1),
                is_active=False,
                exit_date=date(2026, 6, 5),
                exit_price=850,
                exit_quantity=40,
            ),
            UserPortfolio(
                id=4,
                user_id=1,
                position_group_id="group-closed-overflow",
                symbol="2454.TW",
                entry_price=800,
                quantity=postgres_integer_max - 7,
                entry_date=date(2026, 6, 1),
                is_active=False,
                exit_date=date(2026, 6, 10),
                exit_price=880,
                exit_quantity=postgres_integer_max - 7,
            ),
        ])
        session.flush()
        session.add_all([
            PositionEvent(
                user_id=1,
                position_group_id="group-active-overflow",
                symbol="2330.TW",
                event_type="initial_entry",
                event_date=date(2026, 5, 1),
                price=900,
                quantity=postgres_integer_max,
                fees=0,
                taxes=0,
                source_portfolio_id=1,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-active-overflow",
                symbol="2330.TW",
                event_type="partial_exit",
                event_date=date(2026, 5, 5),
                price=950,
                quantity=40,
                fees=0,
                taxes=0,
                source_portfolio_id=2,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-closed-overflow",
                symbol="2454.TW",
                event_type="initial_entry",
                event_date=date(2026, 6, 1),
                price=800,
                quantity=postgres_integer_max - 7,
                fees=0,
                taxes=0,
                source_portfolio_id=4,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-closed-overflow",
                symbol="2454.TW",
                event_type="partial_exit",
                event_date=date(2026, 6, 5),
                price=850,
                quantity=40,
                fees=0,
                taxes=0,
                source_portfolio_id=3,
                source="synthetic_from_portfolio_row",
            ),
            PositionEvent(
                user_id=1,
                position_group_id="group-closed-overflow",
                symbol="2454.TW",
                event_type="full_exit",
                event_date=date(2026, 6, 10),
                price=880,
                quantity=postgres_integer_max - 7,
                fees=0,
                taxes=0,
                source_portfolio_id=4,
                source="synthetic_from_portfolio_row",
            ),
        ])
        session.commit()

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._repair_synthetic_group_quantities()
        migration._repair_synthetic_group_quantities()

        initial_events = {
            event.position_group_id: event
            for event in session.execute(
                select(PositionEvent).where(PositionEvent.event_type == "initial_entry")
            ).scalars()
        }
        assert initial_events["group-active-overflow"].quantity == postgres_integer_max
        assert initial_events["group-closed-overflow"].quantity == postgres_integer_max - 7


def _add_active_split_group(
    session: Session,
    *,
    group_id: str,
    active_symbol: str = "2330.TW",
    closed_symbol: str = "2330.TW",
    initial_symbol: str = "2330.TW",
    exit_symbol: str = "2330.TW",
    active_quantity: int = 60,
    active_updated_at: datetime = datetime(2026, 7, 1, tzinfo=timezone.utc),
    initial_created_at: datetime = datetime(2026, 7, 2, tzinfo=timezone.utc),
) -> None:
    session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    session.add_all([
        UserPortfolio(
            id=1,
            user_id=1,
            position_group_id=group_id,
            symbol=active_symbol,
            entry_price=900,
            quantity=active_quantity,
            entry_date=date(2026, 7, 1),
            is_active=True,
            created_at=active_updated_at,
            updated_at=active_updated_at,
        ),
        UserPortfolio(
            id=2,
            user_id=1,
            position_group_id=group_id,
            symbol=closed_symbol,
            entry_price=900,
            quantity=40,
            entry_date=date(2026, 7, 1),
            is_active=False,
            exit_date=date(2026, 7, 5),
            exit_price=950,
            exit_quantity=40,
            created_at=active_updated_at,
            updated_at=active_updated_at,
        ),
    ])
    session.flush()
    session.add_all([
        PositionEvent(
            user_id=1,
            position_group_id=group_id,
            symbol=initial_symbol,
            event_type="initial_entry",
            event_date=date(2026, 7, 1),
            price=900,
            quantity=60,
            fees=0,
            taxes=0,
            source_portfolio_id=1,
            source="synthetic_from_portfolio_row",
            created_at=initial_created_at,
            updated_at=initial_created_at,
        ),
        PositionEvent(
            user_id=1,
            position_group_id=group_id,
            symbol=exit_symbol,
            event_type="partial_exit",
            event_date=date(2026, 7, 5),
            price=950,
            quantity=40,
            fees=0,
            taxes=0,
            source_portfolio_id=2,
            source="synthetic_from_portfolio_row",
            created_at=initial_created_at,
            updated_at=initial_created_at,
        ),
    ])
    session.commit()


def _initial_event(session: Session, group_id: str) -> PositionEvent:
    return session.execute(
        select(PositionEvent).where(
            PositionEvent.position_group_id == group_id,
            PositionEvent.event_type == "initial_entry",
        )
    ).scalar_one()


def test_repair_migration_skips_active_group_updated_after_synthetic_backfill() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()

    with Session(engine) as session:
        _add_active_split_group(
            session,
            group_id="group-post-backfill-put",
            active_quantity=80,
            active_updated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
            initial_created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._repair_synthetic_group_quantities()
        migration._repair_synthetic_group_quantities()

        assert _initial_event(session, "group-post-backfill-put").quantity == 60


def test_repair_migration_skips_groups_with_mixed_symbols() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()

    with Session(engine) as session:
        _add_active_split_group(
            session,
            group_id="group-mixed-symbols",
            closed_symbol="2454.TW",
            exit_symbol="2454.TW",
        )

        migration.op = SimpleNamespace(get_bind=lambda: session.connection())
        migration._repair_synthetic_group_quantities()
        migration._repair_synthetic_group_quantities()

        assert _initial_event(session, "group-mixed-symbols").quantity == 60


def test_repair_migration_does_not_overwrite_concurrent_manual_correction() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()

    with Session(engine) as session:
        _add_active_split_group(session, group_id="group-concurrent-correction")
        connection = session.connection()

        class ConcurrentCorrectionBind:
            injected = False

            def execute(self, statement, parameters=None):
                sql = str(statement)
                if not self.injected and "UPDATE position_event" in sql and "SET quantity" in sql:
                    connection.execute(
                        text(
                            """
                            UPDATE position_event
                            SET quantity = 80,
                                source = 'manual_record_correction',
                                updated_at = :updated_at
                            WHERE position_group_id = :position_group_id
                              AND event_type = 'initial_entry'
                            """
                        ),
                        {
                            "updated_at": datetime(2026, 7, 3, tzinfo=timezone.utc),
                            "position_group_id": "group-concurrent-correction",
                        },
                    )
                    self.injected = True
                if parameters is None:
                    return connection.execute(statement)
                return connection.execute(statement, parameters)

        migration.op = SimpleNamespace(get_bind=ConcurrentCorrectionBind)
        migration._repair_synthetic_group_quantities()

        session.expire_all()
        initial_event = _initial_event(session, "group-concurrent-correction")
        assert initial_event.quantity == 80
        assert initial_event.source == "manual_record_correction"


def test_repair_migration_skips_group_when_source_portfolio_changes_after_snapshot() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__])
    migration = _load_repair_migration()

    with Session(engine) as session:
        _add_active_split_group(session, group_id="group-concurrent-portfolio-correction")
        connection = session.connection()

        class ConcurrentPortfolioCorrectionBind:
            injected = False

            def execute(self, statement, parameters=None):
                sql = str(statement)
                if not self.injected and "UPDATE user_portfolio" in sql:
                    connection.execute(
                        text(
                            """
                            UPDATE user_portfolio
                            SET entry_price = 950,
                                updated_at = :updated_at
                            WHERE position_group_id = :position_group_id
                              AND is_active = 1
                            """
                        ),
                        {
                            "updated_at": datetime(2026, 7, 3, tzinfo=timezone.utc),
                            "position_group_id": "group-concurrent-portfolio-correction",
                        },
                    )
                    self.injected = True
                if parameters is None:
                    return connection.execute(statement)
                return connection.execute(statement, parameters)

        bind = ConcurrentPortfolioCorrectionBind()
        migration.op = SimpleNamespace(get_bind=lambda: bind)
        migration._repair_synthetic_group_quantities()

        session.expire_all()
        active_row = session.get(UserPortfolio, 1)
        initial_event = _initial_event(session, "group-concurrent-portfolio-correction")
        assert bind.injected is True
        assert float(active_row.entry_price) == 950
        assert initial_event.quantity == 60
