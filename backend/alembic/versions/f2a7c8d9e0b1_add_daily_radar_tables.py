"""add daily radar tables

Revision ID: f2a7c8d9e0b1
Revises: 9b7d2a4c1e3f
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f2a7c8d9e0b1"
down_revision: Union[str, Sequence[str], None] = "9b7d2a4c1e3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_radar_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("universe_count", sa.Integer(), nullable=False),
        sa.Column("prefilter_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_daily_radar_runs_run_date",
        "daily_radar_runs",
        ["run_date"],
        unique=False,
    )

    op.create_table(
        "daily_radar_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("primary_bucket", sa.String(length=40), nullable=False),
        sa.Column("secondary_buckets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("observation_score", sa.Integer(), nullable=False),
        sa.Column("bucket_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_labels", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("matched_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("repeat_status", sa.String(length=20), nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("data_dates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["daily_radar_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "symbol", name="uq_daily_radar_candidates_run_symbol"),
    )
    op.create_index(
        "idx_daily_radar_candidates_symbol",
        "daily_radar_candidates",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        "idx_daily_radar_candidates_primary_bucket",
        "daily_radar_candidates",
        ["primary_bucket"],
        unique=False,
    )
    op.create_index(
        "idx_daily_radar_candidates_observation_score",
        "daily_radar_candidates",
        ["observation_score"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_daily_radar_candidates_observation_score",
        table_name="daily_radar_candidates",
    )
    op.drop_index(
        "idx_daily_radar_candidates_primary_bucket",
        table_name="daily_radar_candidates",
    )
    op.drop_index(
        "idx_daily_radar_candidates_symbol",
        table_name="daily_radar_candidates",
    )
    op.drop_constraint(
        "uq_daily_radar_candidates_run_symbol",
        "daily_radar_candidates",
        type_="unique",
    )
    op.drop_table("daily_radar_candidates")
    op.drop_index("idx_daily_radar_runs_run_date", table_name="daily_radar_runs")
    op.drop_table("daily_radar_runs")
