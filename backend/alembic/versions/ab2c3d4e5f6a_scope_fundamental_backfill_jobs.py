"""Separate date/stage-bound Radar EPS work from maintenance jobs.

Revision ID: ab2c3d4e5f6a
Revises: aa1b2c3d4e5f
"""
import sqlalchemy as sa
from alembic import op

revision = "ab2c3d4e5f6a"
down_revision = "aa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing snapshots/cursors remain intact; legacy work keeps maintenance semantics.
    op.add_column("fundamental_backfill_jobs", sa.Column("purpose", sa.String(20), nullable=False, server_default="maintenance"))
    op.add_column("fundamental_backfill_jobs", sa.Column("radar_stage", sa.String(8), nullable=True))
    op.create_check_constraint("ck_fundamental_backfill_job_purpose", "fundamental_backfill_jobs",
        "(purpose = 'maintenance' AND radar_stage IS NULL) OR "
        "(purpose = 'radar_ttm_eps' AND raw_pool_date IS NOT NULL "
        "AND radar_stage IS NOT NULL AND radar_stage IN ('prepared', 'raw_pool'))")
    op.create_index("uq_fundamental_running_radar_job", "fundamental_backfill_jobs",
        ["raw_pool_date", "radar_stage"], unique=True,
        postgresql_where=sa.text("status = 'running' AND purpose = 'radar_ttm_eps'"))


def downgrade() -> None:
    op.drop_index("uq_fundamental_running_radar_job", table_name="fundamental_backfill_jobs")
    op.drop_constraint("ck_fundamental_backfill_job_purpose", "fundamental_backfill_jobs", type_="check")
    op.drop_column("fundamental_backfill_jobs", "radar_stage")
    op.drop_column("fundamental_backfill_jobs", "purpose")
