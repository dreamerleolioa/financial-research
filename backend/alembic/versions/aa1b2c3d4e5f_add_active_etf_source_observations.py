"""add active ETF source observations

Revision ID: aa1b2c3d4e5f
Revises: 9a0b1c2d3e4f
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "aa1b2c3d4e5f"
down_revision: str | Sequence[str] | None = "9a0b1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "active_etf_holding_snapshots",
        sa.Column(
            "verification_status",
            sa.String(length=20),
            server_default="single_source",
            nullable=False,
        ),
    )
    op.add_column(
        "active_etf_holding_snapshots",
        sa.Column("source_count", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "active_etf_holding_snapshots",
        sa.Column(
            "verification_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_active_etf_snapshot_verification_status",
        "active_etf_holding_snapshots",
        "verification_status IN ('verified', 'single_source', 'conflict')",
    )
    op.create_check_constraint(
        "ck_active_etf_snapshot_source_count",
        "active_etf_holding_snapshots",
        "source_count >= 1",
    )

    op.create_table(
        "active_etf_source_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fund_code", sa.String(length=10), nullable=False),
        sa.Column("data_date", sa.Date(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_provider", sa.String(length=30), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=60), nullable=False),
        sa.Column("raw_payload_gzip", sa.LargeBinary(), nullable=False),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=False),
        sa.Column("holding_count", sa.Integer(), nullable=False),
        sa.Column("skipped_instrument_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("holding_count >= 0", name="ck_active_etf_observation_holding_count"),
        sa.CheckConstraint(
            "raw_size_bytes >= 0 AND raw_size_bytes <= 5000000",
            name="ck_active_etf_observation_raw_size",
        ),
        sa.CheckConstraint(
            "skipped_instrument_count >= 0",
            name="ck_active_etf_observation_skipped_count",
        ),
        sa.ForeignKeyConstraint(
            ["fund_code"],
            ["active_etf_funds.fund_code"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fund_code",
            "data_date",
            "source_provider",
            name="uq_active_etf_observation_fund_date_provider",
        ),
    )
    op.create_index(
        "idx_active_etf_observations_data_date",
        "active_etf_source_observations",
        ["data_date"],
    )
    op.create_index(
        "idx_active_etf_observations_fund_date",
        "active_etf_source_observations",
        ["fund_code", "data_date"],
    )

    op.create_table(
        "active_etf_source_holdings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.Column("weight_pct", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("position_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("position_order >= 0", name="ck_active_etf_source_holding_position_order"),
        sa.CheckConstraint("shares >= 0", name="ck_active_etf_source_holding_shares"),
        sa.CheckConstraint(
            "weight_pct >= 0 AND weight_pct <= 100",
            name="ck_active_etf_source_holding_weight_pct",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["active_etf_source_observations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "observation_id",
            "symbol",
            name="uq_active_etf_source_holding_observation_symbol",
        ),
    )
    op.create_index(
        "idx_active_etf_source_holdings_observation",
        "active_etf_source_holdings",
        ["observation_id"],
    )
    op.create_index(
        "idx_active_etf_source_holdings_symbol",
        "active_etf_source_holdings",
        ["symbol"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_active_etf_source_holdings_symbol",
        table_name="active_etf_source_holdings",
    )
    op.drop_index(
        "idx_active_etf_source_holdings_observation",
        table_name="active_etf_source_holdings",
    )
    op.drop_table("active_etf_source_holdings")
    op.drop_index(
        "idx_active_etf_observations_fund_date",
        table_name="active_etf_source_observations",
    )
    op.drop_index(
        "idx_active_etf_observations_data_date",
        table_name="active_etf_source_observations",
    )
    op.drop_table("active_etf_source_observations")
    op.drop_constraint(
        "ck_active_etf_snapshot_source_count",
        "active_etf_holding_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "ck_active_etf_snapshot_verification_status",
        "active_etf_holding_snapshots",
        type_="check",
    )
    op.drop_column("active_etf_holding_snapshots", "verification_details")
    op.drop_column("active_etf_holding_snapshots", "source_count")
    op.drop_column("active_etf_holding_snapshots", "verification_status")
