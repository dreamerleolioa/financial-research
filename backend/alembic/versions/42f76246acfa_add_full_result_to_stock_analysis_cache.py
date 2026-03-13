"""add_full_result_to_stock_analysis_cache

Revision ID: 42f76246acfa
Revises: 4e09fb1d0f78
Create Date: 2026-03-13 09:49:47.975899

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '42f76246acfa'
down_revision: Union[str, Sequence[str], None] = '4e09fb1d0f78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "stock_analysis_cache",
        sa.Column("full_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("stock_analysis_cache", "full_result")
