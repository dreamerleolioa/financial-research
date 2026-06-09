"""add position lifecycle review

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "position_lifecycle_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("position_group_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("review_version", sa.String(length=40), nullable=False),
        sa.Column("review_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "position_group_id",
            "review_version",
            name="uq_position_lifecycle_review_user_group_version",
        ),
    )
    op.create_index("idx_position_lifecycle_review_user_id", "position_lifecycle_review", ["user_id"])
    op.create_index("idx_position_lifecycle_review_position_group_id", "position_lifecycle_review", ["position_group_id"])
    op.create_index("idx_position_lifecycle_review_symbol", "position_lifecycle_review", ["symbol"])
    op.create_index("idx_position_lifecycle_review_user_group", "position_lifecycle_review", ["user_id", "position_group_id"])


def downgrade() -> None:
    op.drop_index("idx_position_lifecycle_review_user_group", table_name="position_lifecycle_review")
    op.drop_index("idx_position_lifecycle_review_symbol", table_name="position_lifecycle_review")
    op.drop_index("idx_position_lifecycle_review_position_group_id", table_name="position_lifecycle_review")
    op.drop_index("idx_position_lifecycle_review_user_id", table_name="position_lifecycle_review")
    op.drop_constraint(
        "uq_position_lifecycle_review_user_group_version",
        "position_lifecycle_review",
        type_="unique",
    )
    op.drop_table("position_lifecycle_review")
