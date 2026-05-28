"""add analysis_type to stock_analysis_cache

Revision ID: 9b7d2a4c1e3f
Revises: 20cfe7301260
Create Date: 2026-05-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b7d2a4c1e3f"
down_revision: Union[str, Sequence[str], None] = "20cfe7301260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_analysis_cache",
        sa.Column("analysis_type", sa.String(length=20), nullable=True),
    )
    op.execute("UPDATE stock_analysis_cache SET analysis_type = 'general' WHERE analysis_type IS NULL")
    op.alter_column("stock_analysis_cache", "analysis_type", nullable=False)

    op.drop_constraint("uq_cache_symbol_date", "stock_analysis_cache", type_="unique")
    op.create_unique_constraint(
        "uq_cache_symbol_date_type",
        "stock_analysis_cache",
        ["symbol", "record_date", "analysis_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_cache_symbol_date_type", "stock_analysis_cache", type_="unique")

    op.execute(
        """
        DELETE FROM stock_analysis_cache AS cache
        USING stock_analysis_cache AS general_cache
        WHERE cache.symbol = general_cache.symbol
          AND cache.record_date = general_cache.record_date
          AND cache.analysis_type <> 'general'
          AND general_cache.analysis_type = 'general'
        """
    )
    op.execute(
        """
        DELETE FROM stock_analysis_cache AS older_cache
        USING stock_analysis_cache AS newer_cache
        WHERE older_cache.symbol = newer_cache.symbol
          AND older_cache.record_date = newer_cache.record_date
          AND older_cache.id < newer_cache.id
        """
    )

    op.drop_column("stock_analysis_cache", "analysis_type")
    op.create_unique_constraint(
        "uq_cache_symbol_date",
        "stock_analysis_cache",
        ["symbol", "record_date"],
    )
