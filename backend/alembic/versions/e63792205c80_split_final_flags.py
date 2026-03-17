"""split_final_flags

Revision ID: e63792205c80
Revises: 42f76246acfa
Create Date: 2026-03-16 17:49:42.594620

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e63792205c80'
down_revision: Union[str, Sequence[str], None] = '42f76246acfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    - Rename is_final -> analysis_is_final on daily_analysis_log and stock_analysis_cache
    - Add raw_data_is_final BOOLEAN NOT NULL DEFAULT FALSE to stock_raw_data
    - Backfill all existing stock_raw_data rows to raw_data_is_final = TRUE
    """
    op.alter_column("daily_analysis_log", "is_final", new_column_name="analysis_is_final")
    op.alter_column("stock_analysis_cache", "is_final", new_column_name="analysis_is_final")
    op.add_column(
        "stock_raw_data",
        sa.Column("raw_data_is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE stock_raw_data SET raw_data_is_final = TRUE")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("stock_raw_data", "raw_data_is_final")
    op.alter_column("stock_analysis_cache", "analysis_is_final", new_column_name="is_final")
    op.alter_column("daily_analysis_log", "analysis_is_final", new_column_name="is_final")
