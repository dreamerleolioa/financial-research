"""add entry record phase b lifecycle fields

Revision ID: a6b7c8d9e0f1
Revises: f6a7b8c9d0e1
Create Date: 2026-06-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_STOP_RULES = (
    "break_20d_low",
    "break_ma20",
    "break_ma60",
    "cost_minus_pct",
    "fixed_price",
    "no_stop_recorded",
    "not_recorded",
)

ADD_ENTRY_CONDITIONS = (
    "no_add_entry",
    "breakout_above_prior_high",
    "pullback_holds_ma20",
    "pullback_holds_support",
    "institutional_flow_continues",
    "profit_threshold_reached",
    "data_quality_complete_only",
    "no_averaging_down",
    "custom_plan_required",
    "not_recorded",
)

OLD_HOLDING_PERIODS = ("short_term", "swing", "medium_term", "long_term")

HOLDING_PERIODS = (*OLD_HOLDING_PERIODS, "not_recorded")

OLD_REASON_CODES = (
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

REASON_CODES = (
    "breakout_confirmation",
    "pullback_held_support",
    "pullback_held_ma20",
    "institutional_flow_strengthened",
    "fundamental_thesis_improved",
    "event_or_news_catalyst",
    "long_term_accumulation",
    "value_revaluation",
    "other",
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


def _nullable_in_constraint(column: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column} IS NULL OR {column} IN ({quoted_values})"


def upgrade() -> None:
    op.drop_constraint("ck_position_event_reason_code", "position_event", type_="check")
    op.create_check_constraint(
        "ck_position_event_reason_code",
        "position_event",
        _nullable_in_constraint("reason_code", REASON_CODES),
    )
    op.drop_constraint("ck_position_lifecycle_plan_holding_period", "position_lifecycle_plan", type_="check")
    op.create_check_constraint(
        "ck_position_lifecycle_plan_holding_period",
        "position_lifecycle_plan",
        _nullable_in_constraint("planned_holding_period", HOLDING_PERIODS),
    )
    op.add_column("position_lifecycle_plan", sa.Column("default_stop_rule", sa.String(length=30), nullable=True))
    op.add_column("position_lifecycle_plan", sa.Column("add_entry_condition", sa.String(length=40), nullable=True))
    op.create_check_constraint(
        "ck_position_lifecycle_plan_default_stop_rule",
        "position_lifecycle_plan",
        _nullable_in_constraint("default_stop_rule", DEFAULT_STOP_RULES),
    )
    op.create_check_constraint(
        "ck_position_lifecycle_plan_add_entry_condition",
        "position_lifecycle_plan",
        _nullable_in_constraint("add_entry_condition", ADD_ENTRY_CONDITIONS),
    )


def downgrade() -> None:
    op.drop_constraint("ck_position_lifecycle_plan_add_entry_condition", "position_lifecycle_plan", type_="check")
    op.drop_constraint("ck_position_lifecycle_plan_default_stop_rule", "position_lifecycle_plan", type_="check")
    op.drop_column("position_lifecycle_plan", "add_entry_condition")
    op.drop_column("position_lifecycle_plan", "default_stop_rule")
    op.drop_constraint("ck_position_lifecycle_plan_holding_period", "position_lifecycle_plan", type_="check")
    op.create_check_constraint(
        "ck_position_lifecycle_plan_holding_period",
        "position_lifecycle_plan",
        _nullable_in_constraint("planned_holding_period", OLD_HOLDING_PERIODS),
    )
    op.drop_constraint("ck_position_event_reason_code", "position_event", type_="check")
    op.create_check_constraint(
        "ck_position_event_reason_code",
        "position_event",
        _nullable_in_constraint("reason_code", OLD_REASON_CODES),
    )
