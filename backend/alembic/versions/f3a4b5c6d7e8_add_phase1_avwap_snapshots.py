"""add phase1 avwap snapshots

Revision ID: f3a4b5c6d7e8
Revises: e1f2a3b4c5d6
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "phase1_avwap_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("data_date", sa.Date(), nullable=False),
        sa.Column("dataset", sa.String(length=50), server_default="TaiwanStockPrice", nullable=False),
        sa.Column("adjustment_mode", sa.String(length=20), server_default="unadjusted", nullable=False),
        sa.Column("source_provider", sa.String(length=30), server_default="finmind", nullable=False),
        sa.Column("source_granularity", sa.String(length=20), server_default="daily", nullable=False),
        sa.Column("is_final", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("freshness", sa.String(length=20), server_default="fresh", nullable=False),
        sa.Column("missing_reason", sa.String(length=120), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "freshness IN ('fresh', 'stale', 'missing', 'unknown')",
            name="ck_phase1_avwap_snapshot_freshness",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "data_date",
            "dataset",
            "adjustment_mode",
            name="uq_phase1_avwap_symbol_date_dataset_mode",
        ),
    )
    op.create_index("idx_phase1_avwap_snapshots_symbol", "phase1_avwap_snapshots", ["symbol"], unique=False)
    op.create_index("idx_phase1_avwap_snapshots_data_date", "phase1_avwap_snapshots", ["data_date"], unique=False)
    op.create_index("idx_phase1_avwap_snapshots_freshness", "phase1_avwap_snapshots", ["freshness"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_phase1_avwap_snapshots_freshness", table_name="phase1_avwap_snapshots")
    op.drop_index("idx_phase1_avwap_snapshots_data_date", table_name="phase1_avwap_snapshots")
    op.drop_index("idx_phase1_avwap_snapshots_symbol", table_name="phase1_avwap_snapshots")
    op.drop_constraint(
        "uq_phase1_avwap_symbol_date_dataset_mode",
        "phase1_avwap_snapshots",
        type_="unique",
    )
    op.drop_table("phase1_avwap_snapshots")
