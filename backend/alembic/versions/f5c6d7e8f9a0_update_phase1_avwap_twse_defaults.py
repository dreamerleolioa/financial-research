"""update phase1 avwap twse defaults

Revision ID: f5c6d7e8f9a0
Revises: f4b5c6d7e8f9
Create Date: 2026-06-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "f4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "phase1_avwap_snapshots",
        "dataset",
        existing_type=sa.String(length=50),
        server_default="phase1_daily_ohlcv_amount",
        existing_nullable=False,
    )
    op.alter_column(
        "phase1_avwap_snapshots",
        "source_provider",
        existing_type=sa.String(length=30),
        server_default="twse",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "phase1_avwap_snapshots",
        "source_provider",
        existing_type=sa.String(length=30),
        server_default="finmind",
        existing_nullable=False,
    )
    op.alter_column(
        "phase1_avwap_snapshots",
        "dataset",
        existing_type=sa.String(length=50),
        server_default="TaiwanStockPrice",
        existing_nullable=False,
    )
