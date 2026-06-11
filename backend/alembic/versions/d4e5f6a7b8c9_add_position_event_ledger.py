"""add position event ledger

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SYNTHETIC_NOTE = (
    "Synthetic event generated from legacy user_portfolio row during migration; "
    "captures recorded prices, dates, quantities, fees, and taxes only, without inferring reason, plan, or user intent."
)


def _event_exists(bind, row, event_type: str, event_date, quantity) -> bool:
    return bool(bind.execute(
        sa.text(
            """
            SELECT 1
            FROM position_event
            WHERE source_portfolio_id = :source_portfolio_id
              AND position_group_id = :position_group_id
              AND event_type = :event_type
              AND event_date = :event_date
              AND quantity = :quantity
              AND source = 'synthetic_from_portfolio_row'
            LIMIT 1
            """
        ),
        {
            "source_portfolio_id": row.id,
            "position_group_id": row.position_group_id,
            "event_type": event_type,
            "event_date": event_date,
            "quantity": quantity,
        },
    ).first())


def _insert_event(
    bind,
    row,
    *,
    event_type: str,
    event_date,
    price,
    quantity,
    fees=0,
    taxes=0,
) -> None:
    if event_date is None or price is None or quantity is None:
        return
    if _event_exists(bind, row, event_type, event_date, quantity):
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO position_event (
                user_id, position_group_id, symbol, event_type, event_date,
                price, quantity, fees, taxes, source_portfolio_id, note,
                source, data_quality_note, created_at, updated_at
            ) VALUES (
                :user_id, :position_group_id, :symbol, :event_type, :event_date,
                :price, :quantity, :fees, :taxes, :source_portfolio_id, :note,
                'synthetic_from_portfolio_row', :data_quality_note, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        ),
        {
            "user_id": row.user_id,
            "position_group_id": row.position_group_id,
            "symbol": row.symbol,
            "event_type": event_type,
            "event_date": event_date,
            "price": price,
            "quantity": quantity,
            "fees": fees or 0,
            "taxes": taxes or 0,
            "source_portfolio_id": row.id,
            "note": row.notes,
            "data_quality_note": SYNTHETIC_NOTE,
        },
    )


def _backfill_position_events() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        """
        SELECT id, user_id, position_group_id, symbol, entry_price, quantity, entry_date,
               is_active, exit_date, exit_price, exit_quantity, exit_fees, exit_taxes, notes
        FROM user_portfolio
        WHERE position_group_id IS NOT NULL
        ORDER BY id
        """
    )).fetchall()

    active_groups: set[str] = set()
    latest_closed_by_group: dict[str, tuple[object, int]] = {}
    for row in rows:
        if row.is_active:
            active_groups.add(row.position_group_id)
            continue
        latest_closed = latest_closed_by_group.get(row.position_group_id)
        sort_key = (row.exit_date or row.entry_date, row.id)
        if latest_closed is None or sort_key > latest_closed:
            latest_closed_by_group[row.position_group_id] = sort_key

    for row in rows:
        if row.is_active:
            _insert_event(
                bind,
                row,
                event_type="initial_entry",
                event_date=row.entry_date,
                price=row.entry_price,
                quantity=row.quantity,
            )
            continue

        closed_sort_key = (row.exit_date or row.entry_date, row.id)
        is_latest_closed_without_active = (
            row.position_group_id not in active_groups
            and latest_closed_by_group.get(row.position_group_id) == closed_sort_key
        )

        if not is_latest_closed_without_active:
            _insert_event(
                bind,
                row,
                event_type="partial_exit",
                event_date=row.exit_date,
                price=row.exit_price,
                quantity=row.exit_quantity or row.quantity,
                fees=row.exit_fees,
                taxes=row.exit_taxes,
            )
            continue

        _insert_event(
            bind,
            row,
            event_type="initial_entry",
            event_date=row.entry_date,
            price=row.entry_price,
            quantity=row.quantity,
        )
        _insert_event(
            bind,
            row,
            event_type="full_exit",
            event_date=row.exit_date,
            price=row.exit_price,
            quantity=row.exit_quantity or row.quantity,
            fees=row.exit_fees,
            taxes=row.exit_taxes,
        )


def upgrade() -> None:
    op.create_table(
        "position_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("position_group_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("fees", sa.Numeric(precision=10, scale=2), server_default="0", nullable=False),
        sa.Column("taxes", sa.Numeric(precision=10, scale=2), server_default="0", nullable=False),
        sa.Column("source_portfolio_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("data_quality_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('initial_entry', 'add_entry', 'partial_exit', 'full_exit', 'manual_adjustment')",
            name="ck_position_event_event_type",
        ),
        sa.CheckConstraint(
            "source IN ('synthetic_from_portfolio_row', 'user_backfilled', 'user_recorded_at_event_time', 'manual_record_correction', 'not_recorded')",
            name="ck_position_event_source",
        ),
        sa.ForeignKeyConstraint(["source_portfolio_id"], ["user_portfolio.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_position_event_user_id", "position_event", ["user_id"])
    op.create_index("idx_position_event_position_group_id", "position_event", ["position_group_id"])
    op.create_index("idx_position_event_symbol", "position_event", ["symbol"])
    op.create_index("idx_position_event_event_date", "position_event", ["event_date"])
    op.create_index("idx_position_event_user_group_date", "position_event", ["user_id", "position_group_id", "event_date"])

    _backfill_position_events()


def downgrade() -> None:
    op.drop_index("idx_position_event_user_group_date", table_name="position_event")
    op.drop_index("idx_position_event_event_date", table_name="position_event")
    op.drop_index("idx_position_event_symbol", table_name="position_event")
    op.drop_index("idx_position_event_position_group_id", table_name="position_event")
    op.drop_index("idx_position_event_user_id", table_name="position_event")
    op.drop_table("position_event")
