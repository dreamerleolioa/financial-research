"""add taiwan institutional flow archive

Revision ID: 6a7b8c9d0e1f
Revises: 5f6a7b8c9d0e
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "6a7b8c9d0e1f"
down_revision: str | Sequence[str] | None = "5f6a7b8c9d0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "taiwan_institutional_report_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("dataset", sa.String(length=60), nullable=False),
        sa.Column("source_provider", sa.String(length=30), nullable=False),
        sa.Column("source_dataset", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "market IN ('TW', 'TWO')",
            name="ck_taiwan_institutional_snapshot_market",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_taiwan_institutional_snapshot_status",
        ),
        sa.CheckConstraint(
            "row_count >= 0",
            name="ck_taiwan_institutional_snapshot_row_count",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR payload_hash IS NOT NULL",
            name="ck_taiwan_institutional_snapshot_completed_hash",
        ),
        sa.CheckConstraint(
            "status != 'failed' OR row_count = 0",
            name="ck_taiwan_institutional_snapshot_failed_rows",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market",
            "trade_date",
            "dataset",
            name="uq_taiwan_institutional_snapshot_market_date_dataset",
        ),
    )
    op.create_index(
        "idx_taiwan_institutional_snapshots_date_market",
        "taiwan_institutional_report_snapshots",
        ["trade_date", "market"],
    )
    op.create_index(
        "idx_taiwan_institutional_snapshots_status",
        "taiwan_institutional_report_snapshots",
        ["status"],
    )
    op.create_table(
        "taiwan_institutional_flows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("dataset", sa.String(length=60), nullable=False),
        sa.Column("foreign_net_shares", sa.BigInteger(), nullable=False),
        sa.Column("investment_trust_net_shares", sa.BigInteger(), nullable=False),
        sa.Column("dealer_net_shares", sa.BigInteger(), nullable=False),
        sa.Column("total_net_shares", sa.BigInteger(), nullable=False),
        sa.Column(
            "row_origin",
            sa.String(length=30),
            server_default="reported",
            nullable=False,
        ),
        sa.CheckConstraint(
            "market IN ('TW', 'TWO')",
            name="ck_taiwan_institutional_flow_market",
        ),
        sa.CheckConstraint(
            "row_origin IN ('reported', 'complete_report_zero_fill')",
            name="ck_taiwan_institutional_flow_row_origin",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["taiwan_institutional_report_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "trade_date",
            "dataset",
            name="uq_taiwan_institutional_flow_symbol_date_dataset",
        ),
    )
    op.create_index(
        "idx_taiwan_institutional_flows_symbol_date",
        "taiwan_institutional_flows",
        ["symbol", "trade_date"],
    )
    op.create_index(
        "idx_taiwan_institutional_flows_date_market",
        "taiwan_institutional_flows",
        ["trade_date", "market"],
    )
    op.create_index(
        "idx_taiwan_institutional_flows_snapshot_id",
        "taiwan_institutional_flows",
        ["snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_taiwan_institutional_flows_snapshot_id",
        table_name="taiwan_institutional_flows",
    )
    op.drop_index(
        "idx_taiwan_institutional_flows_date_market",
        table_name="taiwan_institutional_flows",
    )
    op.drop_index(
        "idx_taiwan_institutional_flows_symbol_date",
        table_name="taiwan_institutional_flows",
    )
    op.drop_table("taiwan_institutional_flows")
    op.drop_index(
        "idx_taiwan_institutional_snapshots_status",
        table_name="taiwan_institutional_report_snapshots",
    )
    op.drop_index(
        "idx_taiwan_institutional_snapshots_date_market",
        table_name="taiwan_institutional_report_snapshots",
    )
    op.drop_table("taiwan_institutional_report_snapshots")
