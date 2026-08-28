"""add daily radar shadow selection fields

Revision ID: 7a8b9c0d1e2f
Revises: 6a7b8c9d0e1f
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "7a8b9c0d1e2f"
down_revision: str | Sequence[str] | None = "6a7b8c9d0e1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_radar_forward_validation_results",
        sa.Column("evaluation_as_of_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "daily_radar_candidates",
        sa.Column("selection_status", sa.String(length=20), server_default="selected", nullable=False),
    )
    op.add_column(
        "daily_radar_candidates",
        sa.Column("prefilter_status", sa.String(length=20), server_default="accepted", nullable=False),
    )
    op.add_column(
        "daily_radar_candidates",
        sa.Column("prefilter_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "daily_radar_candidates",
        sa.Column("shadow_cohort", sa.String(length=30), nullable=True),
    )
    op.create_check_constraint(
        "ck_daily_radar_candidate_selection_status",
        "daily_radar_candidates",
        "selection_status IN ('selected', 'shadow')",
    )
    op.create_index(
        "idx_daily_radar_candidates_selection_status",
        "daily_radar_candidates",
        ["selection_status"],
    )
    op.create_check_constraint(
        "ck_daily_radar_candidate_shadow_cohort",
        "daily_radar_candidates",
        "(selection_status = 'selected' AND shadow_cohort IS NULL) OR "
        "(selection_status = 'shadow' AND shadow_cohort IN ('comparable', 'eligibility_audit'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_daily_radar_candidate_shadow_cohort",
        "daily_radar_candidates",
        type_="check",
    )
    op.drop_index("idx_daily_radar_candidates_selection_status", table_name="daily_radar_candidates")
    op.drop_constraint(
        "ck_daily_radar_candidate_selection_status",
        "daily_radar_candidates",
        type_="check",
    )
    op.drop_column("daily_radar_candidates", "prefilter_reasons")
    op.drop_column("daily_radar_candidates", "shadow_cohort")
    op.drop_column("daily_radar_candidates", "prefilter_status")
    op.drop_column("daily_radar_candidates", "selection_status")
    op.drop_column("daily_radar_forward_validation_results", "evaluation_as_of_date")
