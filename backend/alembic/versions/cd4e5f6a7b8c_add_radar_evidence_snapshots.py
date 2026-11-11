"""Append-only Radar evidence and published input manifests.

Revision ID: cd4e5f6a7b8c
Revises: bc3d4e5f6a7b
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "cd4e5f6a7b8c"
down_revision = "bc3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("radar_source_snapshots",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expires_at > captured_at", name="ck_radar_source_expiry"),
        sa.CheckConstraint("length(request_hash) = 64 AND length(payload_hash) = 64", name="ck_radar_source_hashes"))
    op.create_index("idx_radar_source_request_captured", "radar_source_snapshots", ["request_hash", "captured_at", "id"])
    op.create_table("radar_input_manifests",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("fingerprint", name="uq_radar_input_fingerprint"),
        sa.CheckConstraint("length(fingerprint) = 64", name="ck_radar_input_hash"))
    op.create_index("idx_radar_input_date_market", "radar_input_manifests", ["run_date", "market"])
    op.create_table("radar_run_inputs",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("daily_radar_runs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("manifest_id", sa.BigInteger(), sa.ForeignKey("radar_input_manifests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("output_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("output_count >= 0", name="ck_radar_run_input_output_count"))
    op.create_index("idx_radar_run_input_manifest", "radar_run_inputs", ["manifest_id"])


def downgrade():
    op.drop_table("radar_run_inputs")
    op.drop_table("radar_input_manifests")
    op.drop_table("radar_source_snapshots")
