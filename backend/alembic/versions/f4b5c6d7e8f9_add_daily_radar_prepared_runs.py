"""add daily radar prepared runs

Revision ID: f4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_radar_prepared_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="prepared", nullable=False),
        sa.Column("selected_symbols", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("universe", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("symbol_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("market_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_date", "market", name="uq_daily_radar_prepared_run_date_market"),
    )
    op.create_index(
        "idx_daily_radar_prepared_runs_run_date",
        "daily_radar_prepared_runs",
        ["run_date"],
        unique=False,
    )
    op.create_index(
        "idx_daily_radar_prepared_runs_market",
        "daily_radar_prepared_runs",
        ["market"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_daily_radar_prepared_runs_market", table_name="daily_radar_prepared_runs")
    op.drop_index("idx_daily_radar_prepared_runs_run_date", table_name="daily_radar_prepared_runs")
    op.drop_constraint(
        "uq_daily_radar_prepared_run_date_market",
        "daily_radar_prepared_runs",
        type_="unique",
    )
    op.drop_table("daily_radar_prepared_runs")
