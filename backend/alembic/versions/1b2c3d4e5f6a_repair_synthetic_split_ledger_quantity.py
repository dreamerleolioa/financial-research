"""repair synthetic split ledger quantity

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-08-03 00:00:00.000000

"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1b2c3d4e5f6a"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POSTGRES_INTEGER_MAX = 2_147_483_647


def _is_valid_position_event_quantity(quantity: int) -> bool:
    """Keep repaired values within the PostgreSQL INTEGER storage contract."""
    return 0 < quantity <= POSTGRES_INTEGER_MAX


def _has_exact_exit_coverage(events: Sequence[Any], expected_rows: Sequence[Any]) -> bool:
    """Require one matching exit event for every row without quantity ambiguity."""
    expected_quantities: dict[int, int] = {}
    for row in expected_rows:
        row_quantity = int(row.quantity)
        exit_quantity = int(row.exit_quantity) if row.exit_quantity is not None else row_quantity
        if row_quantity <= 0 or exit_quantity <= 0 or exit_quantity != row_quantity:
            return False
        expected_quantities[int(row.id)] = exit_quantity

    observed_sources = [event.source_portfolio_id for event in events]
    if (
        len(observed_sources) != len(expected_quantities)
        or len(set(observed_sources)) != len(observed_sources)
    ):
        return False
    return all(
        event.source_portfolio_id in expected_quantities
        and int(event.quantity) > 0
        and int(event.quantity) == expected_quantities[event.source_portfolio_id]
        for event in events
    )


def _has_consistent_initial_facts(initial_event: Any, expected_rows: Sequence[Any]) -> bool:
    """Reject legacy groups whose entry facts cannot be proven from the synthetic event."""
    try:
        initial_price = Decimal(str(initial_event.price))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if not initial_price.is_finite() or initial_price <= 0 or int(initial_event.quantity) <= 0:
        return False

    for row in expected_rows:
        try:
            row_price = Decimal(str(row.entry_price))
        except (InvalidOperation, TypeError, ValueError):
            return False
        if (
            not row_price.is_finite()
            or row_price != initial_price
            or row.entry_date != initial_event.event_date
        ):
            return False
    return True


def _as_utc_datetime(value: Any) -> datetime | None:
    """Normalize raw SQLite strings and PostgreSQL datetimes for safety checks."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _active_row_predates_initial_event(active_row: Any, initial_event: Any) -> bool:
    """Reject active rows that may have been edited after synthetic backfill."""
    row_updated_at = _as_utc_datetime(active_row.updated_at)
    event_created_at = _as_utc_datetime(initial_event.created_at)
    return (
        row_updated_at is not None
        and event_created_at is not None
        and row_updated_at <= event_created_at
    )


def _has_single_symbol(group_rows: Sequence[Any], events: Sequence[Any]) -> bool:
    """Require every persisted fact in a legacy group to identify one symbol."""
    items = [*group_rows, *events]
    symbols = [
        str(item.symbol).strip()
        for item in items
        if item.symbol is not None and str(item.symbol).strip()
    ]
    return len(symbols) == len(items) and len(set(symbols)) == 1


def _repair_synthetic_group_quantities() -> None:
    """Repair only deterministic active and fully-closed shapes from the original migration."""
    bind = op.get_bind()
    portfolio_rows = bind.execute(sa.text(
        """
        SELECT id, user_id, position_group_id, symbol, entry_price, quantity, entry_date,
               exit_quantity, is_active, updated_at
        FROM user_portfolio
        WHERE position_group_id IS NOT NULL
        ORDER BY id
        """
    )).fetchall()

    rows_by_group: dict[tuple[int | None, str], list[Any]] = {}
    for row in portfolio_rows:
        key = (row.user_id, row.position_group_id)
        rows_by_group.setdefault(key, []).append(row)

    event_rows = bind.execute(sa.text(
        """
        SELECT id, user_id, position_group_id, symbol, event_type, event_date, price, quantity,
               source, source_portfolio_id, created_at, updated_at
        FROM position_event
        WHERE position_group_id IS NOT NULL
        ORDER BY user_id, position_group_id, event_date, created_at, id
        """
    )).fetchall()
    events_by_group: dict[tuple[int | None, str], list[Any]] = {}
    for event in event_rows:
        key = (event.user_id, event.position_group_id)
        events_by_group.setdefault(key, []).append(event)

    for (user_id, position_group_id), group_rows in rows_by_group.items():
        active_rows = [row for row in group_rows if row.is_active]
        if len(active_rows) > 1:
            continue

        events = events_by_group.get((user_id, position_group_id), [])
        if not _has_single_symbol(group_rows, events):
            continue
        initial_events = [event for event in events if event.event_type == "initial_entry"]
        partial_exits = [event for event in events if event.event_type == "partial_exit"]
        full_exits = [event for event in events if event.event_type == "full_exit"]
        expected_initial_quantity: int | None = None
        if len(active_rows) == 1:
            active_row = active_rows[0]
            inactive_rows = [row for row in group_rows if not row.is_active]
            is_safe_active_shape = (
                len(initial_events) == 1
                and bool(partial_exits)
                and not full_exits
                and len(events) == len(partial_exits) + 1
                and initial_events[0].source == "synthetic_from_portfolio_row"
                and initial_events[0].source_portfolio_id == active_row.id
                and int(active_row.quantity) > 0
                and _active_row_predates_initial_event(active_row, initial_events[0])
                and all(
                    event.source == "synthetic_from_portfolio_row"
                    for event in partial_exits
                )
                and _has_exact_exit_coverage(partial_exits, inactive_rows)
                and _has_consistent_initial_facts(initial_events[0], group_rows)
            )
            if is_safe_active_shape:
                expected_initial_quantity = int(active_row.quantity) + sum(
                    int(event.quantity) for event in partial_exits
                )
        else:
            exit_events = partial_exits + full_exits
            is_safe_fully_closed_shape = (
                len(initial_events) == 1
                and bool(partial_exits)
                and len(full_exits) == 1
                and len(events) == len(exit_events) + 1
                and events[-1].id == full_exits[0].id
                and initial_events[0].source == "synthetic_from_portfolio_row"
                and initial_events[0].source_portfolio_id == full_exits[0].source_portfolio_id
                and all(
                    event.source == "synthetic_from_portfolio_row"
                    for event in exit_events
                )
                and _has_exact_exit_coverage(exit_events, group_rows)
                and _has_consistent_initial_facts(initial_events[0], group_rows)
            )
            if is_safe_fully_closed_shape:
                expected_initial_quantity = sum(int(event.quantity) for event in exit_events)

        if (
            expected_initial_quantity is None
            or not _is_valid_position_event_quantity(expected_initial_quantity)
        ):
            continue
        if int(initial_events[0].quantity) == expected_initial_quantity:
            continue

        initial_event = initial_events[0]
        result = bind.execute(
            sa.text(
                """
                UPDATE position_event
                SET quantity = :quantity,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :event_id
                  AND (user_id = :user_id OR (user_id IS NULL AND :user_id IS NULL))
                  AND position_group_id = :position_group_id
                  AND source = :observed_source
                  AND source_portfolio_id = :source_portfolio_id
                  AND quantity = :observed_quantity
                  AND price = :observed_price
                  AND event_date = :observed_event_date
                  AND created_at = :observed_created_at
                  AND updated_at = :observed_updated_at
                """
            ),
            {
                "quantity": expected_initial_quantity,
                "event_id": initial_event.id,
                "user_id": initial_event.user_id,
                "position_group_id": initial_event.position_group_id,
                "observed_source": initial_event.source,
                "source_portfolio_id": initial_event.source_portfolio_id,
                "observed_quantity": initial_event.quantity,
                "observed_price": initial_event.price,
                "observed_event_date": initial_event.event_date,
                "observed_created_at": initial_event.created_at,
                "observed_updated_at": initial_event.updated_at,
            },
        )
        if result.rowcount == 0:
            continue


def upgrade() -> None:
    _repair_synthetic_group_quantities()


def downgrade() -> None:
    # The prior quantity cannot be reconstructed safely after later ledger mutations.
    pass
