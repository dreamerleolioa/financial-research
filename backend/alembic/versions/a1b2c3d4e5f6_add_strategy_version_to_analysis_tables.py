"""add strategy_version to analysis tables

Revision ID: a1b2c3d4e5f6
Revises: e63792205c80
Create Date: 2026-03-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e63792205c80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add strategy_version column to daily_analysis_log and stock_analysis_cache."""
    op.add_column(
        "daily_analysis_log",
        sa.Column("strategy_version", sa.String(20), nullable=True),
    )
    op.add_column(
        "stock_analysis_cache",
        sa.Column("strategy_version", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    """Remove strategy_version column from daily_analysis_log and stock_analysis_cache."""
    op.drop_column("stock_analysis_cache", "strategy_version")
    op.drop_column("daily_analysis_log", "strategy_version")
