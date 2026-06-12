"""add daily radar forward validation results

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_radar_forward_validation_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("validation_version", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("benchmark_symbol", sa.String(length=40), nullable=True),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("skip_reason", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["daily_radar_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_id",
            "window_days",
            "validation_version",
            name="uq_daily_radar_forward_validation_candidate_window_version",
        ),
    )
    op.create_index(
        "idx_daily_radar_forward_validation_candidate_id",
        "daily_radar_forward_validation_results",
        ["candidate_id"],
        unique=False,
    )
    op.create_index(
        "idx_daily_radar_forward_validation_window_days",
        "daily_radar_forward_validation_results",
        ["window_days"],
        unique=False,
    )
    op.create_index(
        "idx_daily_radar_forward_validation_status",
        "daily_radar_forward_validation_results",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_daily_radar_forward_validation_version",
        "daily_radar_forward_validation_results",
        ["validation_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_daily_radar_forward_validation_version", table_name="daily_radar_forward_validation_results")
    op.drop_index("idx_daily_radar_forward_validation_status", table_name="daily_radar_forward_validation_results")
    op.drop_index("idx_daily_radar_forward_validation_window_days", table_name="daily_radar_forward_validation_results")
    op.drop_index("idx_daily_radar_forward_validation_candidate_id", table_name="daily_radar_forward_validation_results")
    op.drop_constraint(
        "uq_daily_radar_forward_validation_candidate_window_version",
        "daily_radar_forward_validation_results",
        type_="unique",
    )
    op.drop_table("daily_radar_forward_validation_results")
