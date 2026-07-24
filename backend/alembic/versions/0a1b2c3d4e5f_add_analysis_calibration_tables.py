"""add general analysis calibration tables

Revision ID: 0a1b2c3d4e5f
Revises: f7a8b9c0d1e2
Create Date: 2026-07-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0a1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_calibration_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("analysis_type", sa.String(length=20), server_default="general", nullable=False),
        sa.Column("market", sa.String(length=20), server_default="TW", nullable=False),
        sa.Column("benchmark_symbol", sa.String(length=40), server_default="TAIEX", nullable=False),
        sa.Column("strategy_version", sa.String(length=20), nullable=False),
        sa.Column("confidence_config_version", sa.String(length=60), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("replay_input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signal_confidence", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("strategy_type", sa.String(length=30), nullable=True),
        sa.Column("conviction_level", sa.String(length=20), nullable=True),
        sa.Column("action_tag", sa.String(length=20), nullable=True),
        sa.Column("analysis_is_final", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_type",
            "market",
            "symbol",
            "record_date",
            "strategy_version",
            "input_hash",
            name="uq_analysis_calibration_sample_identity",
        ),
    )
    op.create_index(
        "idx_analysis_calibration_samples_market",
        "analysis_calibration_samples",
        ["market"],
        unique=False,
    )
    op.create_index(
        "idx_analysis_calibration_samples_record_date",
        "analysis_calibration_samples",
        ["record_date"],
        unique=False,
    )
    op.create_index(
        "idx_analysis_calibration_samples_symbol",
        "analysis_calibration_samples",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        "idx_analysis_calibration_samples_strategy_version",
        "analysis_calibration_samples",
        ["strategy_version"],
        unique=False,
    )

    op.create_table(
        "analysis_forward_validation_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("validation_version", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("benchmark_symbol", sa.String(length=40), nullable=True),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("skip_reason", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["sample_id"], ["analysis_calibration_samples.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sample_id",
            "window_days",
            "validation_version",
            name="uq_analysis_forward_validation_sample_window_version",
        ),
    )
    op.create_index(
        "idx_analysis_forward_validation_sample_id",
        "analysis_forward_validation_results",
        ["sample_id"],
        unique=False,
    )
    op.create_index(
        "idx_analysis_forward_validation_window_days",
        "analysis_forward_validation_results",
        ["window_days"],
        unique=False,
    )
    op.create_index(
        "idx_analysis_forward_validation_status",
        "analysis_forward_validation_results",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_analysis_forward_validation_version",
        "analysis_forward_validation_results",
        ["validation_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_analysis_forward_validation_version",
        table_name="analysis_forward_validation_results",
    )
    op.drop_index(
        "idx_analysis_forward_validation_status",
        table_name="analysis_forward_validation_results",
    )
    op.drop_index(
        "idx_analysis_forward_validation_window_days",
        table_name="analysis_forward_validation_results",
    )
    op.drop_index(
        "idx_analysis_forward_validation_sample_id",
        table_name="analysis_forward_validation_results",
    )
    op.drop_table("analysis_forward_validation_results")
    op.drop_index(
        "idx_analysis_calibration_samples_strategy_version",
        table_name="analysis_calibration_samples",
    )
    op.drop_index(
        "idx_analysis_calibration_samples_symbol",
        table_name="analysis_calibration_samples",
    )
    op.drop_index(
        "idx_analysis_calibration_samples_market",
        table_name="analysis_calibration_samples",
    )
    op.drop_index(
        "idx_analysis_calibration_samples_record_date",
        table_name="analysis_calibration_samples",
    )
    op.drop_table("analysis_calibration_samples")
