"""add watchlist sort order

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_watchlist",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id
                    ORDER BY created_at DESC, id DESC
                ) - 1 AS new_sort_order
            FROM user_watchlist
        )
        UPDATE user_watchlist
        SET sort_order = ordered.new_sort_order
        FROM ordered
        WHERE user_watchlist.id = ordered.id
        """
    )
    op.create_index(
        "idx_user_watchlist_user_sort_order",
        "user_watchlist",
        ["user_id", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_watchlist_user_sort_order", table_name="user_watchlist")
    op.drop_column("user_watchlist", "sort_order")
