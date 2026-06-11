"""add shared background contexts

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-06-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shared_background_contexts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("context_type", sa.String(length=50), nullable=False),
        sa.Column("applicable_consumers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("freshness", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_reason", sa.String(length=120), nullable=True),
        sa.Column("replay_key", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "freshness IN ('fresh', 'stale', 'missing', 'unknown')",
            name="ck_shared_background_context_freshness",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "context_type", name="uq_shared_background_context_symbol_type"),
    )
    op.create_index(
        "idx_shared_background_context_symbol",
        "shared_background_contexts",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        "idx_shared_background_context_context_type",
        "shared_background_contexts",
        ["context_type"],
        unique=False,
    )
    op.create_index(
        "idx_shared_background_context_as_of_date",
        "shared_background_contexts",
        ["as_of_date"],
        unique=False,
    )
    op.create_index(
        "idx_shared_background_context_freshness",
        "shared_background_contexts",
        ["freshness"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_shared_background_context_freshness", table_name="shared_background_contexts")
    op.drop_index("idx_shared_background_context_as_of_date", table_name="shared_background_contexts")
    op.drop_index("idx_shared_background_context_context_type", table_name="shared_background_contexts")
    op.drop_index("idx_shared_background_context_symbol", table_name="shared_background_contexts")
    op.drop_constraint(
        "uq_shared_background_context_symbol_type",
        "shared_background_contexts",
        type_="unique",
    )
    op.drop_table("shared_background_contexts")
