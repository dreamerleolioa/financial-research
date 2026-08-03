"""repair synthetic split ledger quantity

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1b2c3d4e5f6a"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _repair_synthetic_active_group_quantities() -> None:
    """Repair only the deterministic shape emitted by the original ledger migration."""
    bind = op.get_bind()
    active_rows = bind.execute(sa.text(
        """
        SELECT id, user_id, position_group_id, quantity
        FROM user_portfolio
        WHERE is_active = true
          AND position_group_id IS NOT NULL
        ORDER BY id
        """
    )).fetchall()

    active_group_counts: dict[tuple[int | None, str], int] = {}
    for row in active_rows:
        key = (row.user_id, row.position_group_id)
        active_group_counts[key] = active_group_counts.get(key, 0) + 1

    for active_row in active_rows:
        key = (active_row.user_id, active_row.position_group_id)
        if active_group_counts[key] != 1:
            continue

        events = bind.execute(
            sa.text(
                """
                SELECT id, event_type, quantity, source, source_portfolio_id
                FROM position_event
                WHERE user_id = :user_id
                  AND position_group_id = :position_group_id
                ORDER BY event_date, created_at, id
                """
            ),
            {
                "user_id": active_row.user_id,
                "position_group_id": active_row.position_group_id,
            },
        ).fetchall()
        initial_events = [event for event in events if event.event_type == "initial_entry"]
        exit_events = [event for event in events if event.event_type == "partial_exit"]
        is_safe_synthetic_shape = (
            len(initial_events) == 1
            and bool(exit_events)
            and len(events) == len(exit_events) + 1
            and initial_events[0].source == "synthetic_from_portfolio_row"
            and initial_events[0].source_portfolio_id == active_row.id
            and all(event.source == "synthetic_from_portfolio_row" for event in exit_events)
        )
        if not is_safe_synthetic_shape:
            continue

        expected_initial_quantity = int(active_row.quantity) + sum(int(event.quantity) for event in exit_events)
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
    _repair_synthetic_active_group_quantities()


def downgrade() -> None:
    # The prior quantity cannot be reconstructed safely after later ledger mutations.
    pass
