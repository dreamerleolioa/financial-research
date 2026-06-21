"""update phase1 avwap twse defaults

Revision ID: f5c6d7e8f9a0
Revises: f4b5c6d7e8f9
Create Date: 2026-06-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


OLD_PHASE1_DATASET = "TaiwanStockPrice"
NEW_PHASE1_DATASET = "phase1_daily_ohlcv_amount"


revision: str = "f5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "f4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text(f"""
        UPDATE phase1_avwap_snapshots AS legacy
        SET
            dataset = '{NEW_PHASE1_DATASET}',
            payload = jsonb_set(
                legacy.payload,
                '{{dataset}}',
                to_jsonb('{NEW_PHASE1_DATASET}'::text),
                true
            )
        WHERE legacy.dataset = '{OLD_PHASE1_DATASET}'
          AND NOT EXISTS (
              SELECT 1
              FROM phase1_avwap_snapshots AS current
              WHERE current.symbol = legacy.symbol
                AND current.data_date = legacy.data_date
                AND current.dataset = '{NEW_PHASE1_DATASET}'
                AND current.adjustment_mode = legacy.adjustment_mode
          )
    """))
    op.alter_column(
        "phase1_avwap_snapshots",
        "dataset",
        existing_type=sa.String(length=50),
        server_default=NEW_PHASE1_DATASET,
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
    op.execute(sa.text(f"""
        UPDATE phase1_avwap_snapshots AS legacy
        SET
            dataset = '{OLD_PHASE1_DATASET}',
            payload = jsonb_set(
                legacy.payload,
                '{{dataset}}',
                to_jsonb('{OLD_PHASE1_DATASET}'::text),
                true
            )
        WHERE legacy.dataset = '{NEW_PHASE1_DATASET}'
          AND legacy.source_provider = 'finmind'
          AND NOT EXISTS (
              SELECT 1
              FROM phase1_avwap_snapshots AS current
              WHERE current.symbol = legacy.symbol
                AND current.data_date = legacy.data_date
                AND current.dataset = '{OLD_PHASE1_DATASET}'
                AND current.adjustment_mode = legacy.adjustment_mode
          )
    """))
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
        server_default=OLD_PHASE1_DATASET,
        existing_nullable=False,
    )
