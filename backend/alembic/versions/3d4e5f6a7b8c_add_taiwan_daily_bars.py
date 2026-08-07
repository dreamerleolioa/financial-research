"""add taiwan daily bars

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "3d4e5f6a7b8c"
down_revision: str | Sequence[str] | None = "2c3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "taiwan_daily_bars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("dataset", sa.String(length=50), nullable=False),
        sa.Column(
            "adjustment_mode",
            sa.String(length=20),
            server_default="unadjusted",
            nullable=False,
        ),
        sa.Column("source_provider", sa.String(length=30), nullable=False),
        sa.Column("source_dataset", sa.String(length=60), nullable=False),
        sa.Column("is_final", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("market IN ('TW', 'TWO')", name="ck_taiwan_daily_bar_market"),
        sa.CheckConstraint(
            "adjustment_mode IN ('unadjusted')",
            name="ck_taiwan_daily_bar_adjustment_mode",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "trade_date",
            "dataset",
            "adjustment_mode",
            name="uq_taiwan_daily_bar_symbol_date_dataset_mode",
        ),
    )
    op.create_index(
        "idx_taiwan_daily_bars_symbol_date",
        "taiwan_daily_bars",
        ["symbol", "trade_date"],
    )
    op.create_index(
        "idx_taiwan_daily_bars_date_market",
        "taiwan_daily_bars",
        ["trade_date", "market"],
    )


def downgrade() -> None:
    op.drop_index("idx_taiwan_daily_bars_date_market", table_name="taiwan_daily_bars")
    op.drop_index("idx_taiwan_daily_bars_symbol_date", table_name="taiwan_daily_bars")
    op.drop_table("taiwan_daily_bars")
