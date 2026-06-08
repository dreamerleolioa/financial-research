"""add single trade review phase 1

Revision ID: c3d4e5f6a7b8
Revises: ab12cd34ef56
Create Date: 2026-06-08 00:00:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_portfolio", sa.Column("position_group_id", sa.String(length=36), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM user_portfolio WHERE position_group_id IS NULL")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE user_portfolio SET position_group_id = :position_group_id WHERE id = :id"),
            {"position_group_id": str(uuid.uuid4()), "id": row.id},
        )

    op.alter_column(
        "user_portfolio",
        "position_group_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )

    op.create_table(
        "trade_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("position_group_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("review_version", sa.String(length=30), nullable=False),
        sa.Column("review_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["user_portfolio.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", name="uq_trade_review_portfolio_id"),
    )


def downgrade() -> None:
    op.drop_constraint("uq_trade_review_portfolio_id", "trade_review", type_="unique")
    op.drop_table("trade_review")
    op.drop_column("user_portfolio", "position_group_id")
