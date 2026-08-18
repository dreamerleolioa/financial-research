"""add fundamental backfill jobs

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "5f6a7b8c9d0e"
down_revision: str | Sequence[str] | None = "4e5f6a7b8c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fundamental_backfill_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("raw_pool_date", sa.Date(), nullable=True),
        sa.Column("symbols", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("next_after_symbol", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed')",
            name="ck_fundamental_backfill_job_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_fundamental_backfill_job_created_at",
        "fundamental_backfill_jobs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_fundamental_backfill_job_created_at",
        table_name="fundamental_backfill_jobs",
    )
    op.drop_table("fundamental_backfill_jobs")
