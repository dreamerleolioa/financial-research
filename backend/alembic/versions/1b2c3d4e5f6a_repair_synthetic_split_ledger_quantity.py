"""repair synthetic split ledger quantity

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1b2c3d4e5f6a"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_exact_source_coverage(events: Sequence[Any], expected_row_ids: set[int]) -> bool:
    source_portfolio_ids = [event.source_portfolio_id for event in events]
    return (
        len(source_portfolio_ids) == len(expected_row_ids)
        and set(source_portfolio_ids) == expected_row_ids
    )


def _repair_synthetic_group_quantities() -> None:
    """Repair only deterministic active and fully-closed shapes from the original migration."""
    bind = op.get_bind()
    portfolio_rows = bind.execute(sa.text(
        """
        SELECT id, user_id, position_group_id, quantity, is_active
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
        SELECT id, user_id, position_group_id, event_type, quantity, source, source_portfolio_id
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
        initial_events = [event for event in events if event.event_type == "initial_entry"]
        partial_exits = [event for event in events if event.event_type == "partial_exit"]
        full_exits = [event for event in events if event.event_type == "full_exit"]
        group_row_ids = {row.id for row in group_rows}

        expected_initial_quantity: int | None = None
        if len(active_rows) == 1:
            active_row = active_rows[0]
            inactive_row_ids = {row.id for row in group_rows if not row.is_active}
            is_safe_active_shape = (
                len(initial_events) == 1
                and bool(partial_exits)
                and not full_exits
                and len(events) == len(partial_exits) + 1
                and initial_events[0].source == "synthetic_from_portfolio_row"
                and initial_events[0].source_portfolio_id == active_row.id
                and all(
                    event.source == "synthetic_from_portfolio_row"
                    for event in partial_exits
                )
                and _has_exact_source_coverage(partial_exits, inactive_row_ids)
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
                and _has_exact_source_coverage(exit_events, group_row_ids)
            )
            if is_safe_fully_closed_shape:
                expected_initial_quantity = sum(int(event.quantity) for event in exit_events)

        if expected_initial_quantity is None:
            continue
        if int(initial_events[0].quantity) == expected_initial_quantity:
            continue

        bind.execute(
            sa.text(
                """
                UPDATE position_event
                SET quantity = :quantity,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :event_id
                """
            ),
            {
                "quantity": expected_initial_quantity,
                "event_id": initial_events[0].id,
            },
        )


def upgrade() -> None:
    _repair_synthetic_group_quantities()


def downgrade() -> None:
    # The prior quantity cannot be reconstructed safely after later ledger mutations.
    pass
