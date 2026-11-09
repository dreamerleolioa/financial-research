"""Durable Radar intents, work memberships and fenced attempts.

Revision ID: de5f6a7b8c9d
Revises: cd4e5f6a7b8c
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "de5f6a7b8c9d"
down_revision = "cd4e5f6a7b8c"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(), primary_key=True)


def _created(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade():
    for name, type_ in (("sdk_operation", sa.String(60)), ("sdk_symbol", sa.String(40)),
                        ("sdk_start_date", sa.Date()), ("sdk_end_date", sa.Date())):
        op.add_column("radar_source_snapshots", sa.Column(name, type_, nullable=True))
    op.create_index("idx_radar_source_sdk_interval", "radar_source_snapshots",
        ["sdk_operation", "sdk_symbol", "sdk_start_date", "sdk_end_date", "captured_at"])
    op.create_table("radar_jobs", _id(),
        sa.Column("job_key", sa.String(64), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("lanes", postgresql.JSONB(), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_advanced_at", sa.DateTime(timezone=True)),
        _created("created_at"), _created("updated_at"),
        sa.UniqueConstraint("job_key", name="uq_radar_job_key"))
    op.create_index("idx_radar_job_due", "radar_jobs", ["next_attempt_at", "id"])
    op.create_table("radar_work_items", _id(),
        sa.Column("work_key", sa.String(64), nullable=False),
        sa.Column("identity", postgresql.JSONB(), nullable=False),
        sa.Column("stage", sa.String(60), nullable=False),
        sa.Column("lane", sa.String(32), nullable=False),
        sa.Column("run_date", sa.Date()),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("input_revision", sa.String(64), nullable=False),
        sa.Column("dependencies", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(120)),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        _created("created_at"), _created("updated_at"),
        sa.UniqueConstraint("work_key", name="uq_radar_work_key"),
        sa.CheckConstraint("state IN ('pending','waiting_dependency','waiting_provider','running','ready','terminal',"
            "'needs_review','failed_internal','superseded')", name="ck_radar_work_state"),
        sa.CheckConstraint("(state = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)", name="ck_radar_work_lease"),
        sa.CheckConstraint("attempt_count >= 0 AND length(work_key) = 64 AND length(input_revision) = 64", name="ck_radar_work_identity"))
    op.create_index("idx_radar_work_due", "radar_work_items", ["state", "retry_at", "priority", "id"])
    op.create_table("radar_job_work",
        sa.Column("job_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("radar_jobs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("work_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("radar_work_items.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False))
    op.create_index("idx_radar_job_work_work", "radar_job_work", ["work_id"])
    op.create_table("radar_work_attempts", _id(),
        sa.Column("work_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("radar_work_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("input_revision", sa.String(64), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(120)),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("lease_token", name="uq_radar_attempt_token"))
    op.create_index("idx_radar_attempt_work", "radar_work_attempts", ["work_id", "id"])


def downgrade():
    for table in ("radar_work_attempts", "radar_job_work", "radar_work_items", "radar_jobs"):
        op.drop_table(table)
    op.drop_index("idx_radar_source_sdk_interval", table_name="radar_source_snapshots")
    for column in ("sdk_end_date", "sdk_start_date", "sdk_symbol", "sdk_operation"):
        op.drop_column("radar_source_snapshots", column)
