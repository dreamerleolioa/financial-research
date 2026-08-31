"""add active ETF holding snapshots

Revision ID: 9a0b1c2d3e4f
Revises: 8a9b0c1d2e3f
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "9a0b1c2d3e4f"
down_revision: str | Sequence[str] | None = "8a9b0c1d2e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "active_etf_funds",
        sa.Column("fund_code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("issuer", sa.String(length=100), nullable=True),
        sa.Column("market", sa.String(length=20), server_default="TW", nullable=False),
        sa.Column("source_provider", sa.String(length=30), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("official_url", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("market IN ('TW', 'TWO')", name="ck_active_etf_funds_market"),
        sa.PrimaryKeyConstraint("fund_code"),
    )
    op.create_index("idx_active_etf_funds_enabled", "active_etf_funds", ["enabled"])

    op.create_table(
        "active_etf_holding_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fund_code", sa.String(length=10), nullable=False),
        sa.Column("data_date", sa.Date(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_provider", sa.String(length=30), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=40), nullable=False),
        sa.Column("holding_count", sa.Integer(), nullable=False),
        sa.Column("skipped_instrument_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("holding_count >= 0", name="ck_active_etf_snapshot_holding_count"),
        sa.CheckConstraint(
            "skipped_instrument_count >= 0",
            name="ck_active_etf_snapshot_skipped_instrument_count",
        ),
        sa.ForeignKeyConstraint(
            ["fund_code"],
            ["active_etf_funds.fund_code"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fund_code", "data_date", name="uq_active_etf_snapshot_fund_date"),
    )
    op.create_index(
        "idx_active_etf_snapshots_data_date",
        "active_etf_holding_snapshots",
        ["data_date"],
    )
    op.create_index(
        "idx_active_etf_snapshots_fund_date",
        "active_etf_holding_snapshots",
        ["fund_code", "data_date"],
    )

    op.create_table(
        "active_etf_holdings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.Column("weight_pct", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("position_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("position_order >= 0", name="ck_active_etf_holding_position_order"),
        sa.CheckConstraint("shares >= 0", name="ck_active_etf_holding_shares_nonnegative"),
        sa.CheckConstraint(
            "weight_pct >= 0 AND weight_pct <= 100",
            name="ck_active_etf_holding_weight_pct",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["active_etf_holding_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "symbol",
            name="uq_active_etf_holding_snapshot_symbol",
        ),
    )
    op.create_index("idx_active_etf_holdings_snapshot_id", "active_etf_holdings", ["snapshot_id"])
    op.create_index("idx_active_etf_holdings_symbol", "active_etf_holdings", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_active_etf_holdings_symbol", table_name="active_etf_holdings")
    op.drop_index("idx_active_etf_holdings_snapshot_id", table_name="active_etf_holdings")
    op.drop_table("active_etf_holdings")
    op.drop_index("idx_active_etf_snapshots_fund_date", table_name="active_etf_holding_snapshots")
    op.drop_index("idx_active_etf_snapshots_data_date", table_name="active_etf_holding_snapshots")
    op.drop_table("active_etf_holding_snapshots")
    op.drop_index("idx_active_etf_funds_enabled", table_name="active_etf_funds")
    op.drop_table("active_etf_funds")
