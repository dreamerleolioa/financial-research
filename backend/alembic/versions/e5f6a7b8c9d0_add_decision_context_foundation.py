"""add decision context foundation

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REASON_CATEGORIES = (
    "technical",
    "institutional_flow",
    "fundamental",
    "news",
    "risk_control",
    "plan_execution",
    "emotional",
    "record_correction",
    "not_recorded",
)

REASON_CODES = (
    "breakout_confirmation",
    "pullback_held_support",
    "pullback_held_ma20",
    "institutional_flow_strengthened",
    "fundamental_thesis_improved",
    "planned_scale_in",
    "averaging_down",
    "chasing_momentum",
    "manual_record_correction",
    "target_reached",
    "trailing_stop_hit",
    "support_broken",
    "ma20_lost",
    "institutional_flow_weakened",
    "fundamental_thesis_broken",
    "news_risk_increased",
    "risk_reduction",
    "profit_protection",
    "planned_scale_out",
    "stop_loss",
    "emotional_exit",
)

PLAN_ADHERENCE_VALUES = ("yes", "partial", "no", "not_recorded")
CONFIDENCE_LEVELS = ("high", "medium", "low", "not_recorded")
PLAN_SOURCES = (
    "synthetic_from_portfolio_row",
    "user_backfilled",
    "user_recorded_at_event_time",
    "manual_record_correction",
    "not_recorded",
)
SETUP_TYPES = (
    "breakout",
    "pullback",
    "mean_reversion",
    "value_revaluation",
    "earnings_or_event",
    "momentum_continuation",
    "long_term_accumulation",
    "defensive_rebalance",
    "other",
)
HOLDING_PERIODS = ("short_term", "swing", "medium_term", "long_term")


def _nullable_in_constraint(column: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column} IS NULL OR {column} IN ({quoted_values})"


def _in_constraint(column: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted_values})"


def upgrade() -> None:
    op.add_column("position_event", sa.Column("reason_category", sa.String(length=40), nullable=True))
    op.add_column("position_event", sa.Column("reason_code", sa.String(length=50), nullable=True))
    op.add_column("position_event", sa.Column("plan_adherence", sa.String(length=20), nullable=True))
    op.add_column("position_event", sa.Column("confidence_level", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_position_event_reason_category",
        "position_event",
        _nullable_in_constraint("reason_category", REASON_CATEGORIES),
    )
    op.create_check_constraint(
        "ck_position_event_reason_code",
        "position_event",
        _nullable_in_constraint("reason_code", REASON_CODES),
    )
    op.create_check_constraint(
        "ck_position_event_plan_adherence",
        "position_event",
        _nullable_in_constraint("plan_adherence", PLAN_ADHERENCE_VALUES),
    )
    op.create_check_constraint(
        "ck_position_event_confidence_level",
        "position_event",
        _nullable_in_constraint("confidence_level", CONFIDENCE_LEVELS),
    )

    op.create_table(
        "position_lifecycle_plan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("position_group_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("source_portfolio_id", sa.Integer(), nullable=True),
        sa.Column("thesis", sa.Text(), nullable=True),
        sa.Column("setup_type", sa.String(length=40), nullable=True),
        sa.Column("planned_holding_period", sa.String(length=20), nullable=True),
        sa.Column("planned_invalidation", sa.Text(), nullable=True),
        sa.Column("planned_stop_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("planned_target_or_scale_out_rule", sa.Text(), nullable=True),
        sa.Column("planned_risk_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("planned_risk_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("position_sizing_rationale", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_after_entry", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_nullable_in_constraint("setup_type", SETUP_TYPES), name="ck_position_lifecycle_plan_setup_type"),
        sa.CheckConstraint(_nullable_in_constraint("planned_holding_period", HOLDING_PERIODS), name="ck_position_lifecycle_plan_holding_period"),
        sa.CheckConstraint(_in_constraint("source", PLAN_SOURCES), name="ck_position_lifecycle_plan_source"),
        sa.ForeignKeyConstraint(["source_portfolio_id"], ["user_portfolio.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position_group_id", name="uq_position_lifecycle_plan_group"),
    )
    op.create_index("idx_position_lifecycle_plan_user_id", "position_lifecycle_plan", ["user_id"])
    op.create_index("idx_position_lifecycle_plan_position_group_id", "position_lifecycle_plan", ["position_group_id"])
    op.create_index("idx_position_lifecycle_plan_symbol", "position_lifecycle_plan", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_position_lifecycle_plan_symbol", table_name="position_lifecycle_plan")
    op.drop_index("idx_position_lifecycle_plan_position_group_id", table_name="position_lifecycle_plan")
    op.drop_index("idx_position_lifecycle_plan_user_id", table_name="position_lifecycle_plan")
    op.drop_constraint("uq_position_lifecycle_plan_group", "position_lifecycle_plan", type_="unique")
    op.drop_table("position_lifecycle_plan")
    op.drop_constraint("ck_position_event_confidence_level", "position_event", type_="check")
    op.drop_constraint("ck_position_event_plan_adherence", "position_event", type_="check")
    op.drop_constraint("ck_position_event_reason_code", "position_event", type_="check")
    op.drop_constraint("ck_position_event_reason_category", "position_event", type_="check")
    op.drop_column("position_event", "confidence_level")
    op.drop_column("position_event", "plan_adherence")
    op.drop_column("position_event", "reason_code")
    op.drop_column("position_event", "reason_category")
