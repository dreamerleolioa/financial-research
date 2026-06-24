from __future__ import annotations

from typing import Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


ENTRY_REASON_VALUES: Final[tuple[str, ...]] = (
    "breakout_confirmation",
    "pullback_held_support",
    "pullback_held_ma20",
    "institutional_flow_strengthened",
    "fundamental_thesis_improved",
    "event_or_news_catalyst",
    "long_term_accumulation",
    "value_revaluation",
    "other",
    "not_recorded",
)

PLANNED_HOLDING_PERIOD_VALUES: Final[tuple[str, ...]] = (
    "short_term",
    "swing",
    "medium_term",
    "long_term",
    "not_recorded",
)

DEFAULT_STOP_RULE_VALUES: Final[tuple[str, ...]] = (
    "break_20d_low",
    "break_ma20",
    "break_ma60",
    "cost_minus_pct",
    "fixed_price",
    "no_stop_recorded",
    "not_recorded",
)

ADD_ENTRY_CONDITION_VALUES: Final[tuple[str, ...]] = (
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

EntryReason: TypeAlias = Literal[
    "breakout_confirmation",
    "pullback_held_support",
    "pullback_held_ma20",
    "institutional_flow_strengthened",
    "fundamental_thesis_improved",
    "event_or_news_catalyst",
    "long_term_accumulation",
    "value_revaluation",
    "other",
    "not_recorded",
]

PlannedHoldingPeriod: TypeAlias = Literal[
    "short_term",
    "swing",
    "medium_term",
    "long_term",
    "not_recorded",
]

DefaultStopRule: TypeAlias = Literal[
    "break_20d_low",
    "break_ma20",
    "break_ma60",
    "cost_minus_pct",
    "fixed_price",
    "no_stop_recorded",
    "not_recorded",
]

AddEntryCondition: TypeAlias = Literal[
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
]


class EntryRecordContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_reason: EntryReason | None = None
    planned_holding_period: PlannedHoldingPeriod | None = None
    default_stop_rule: DefaultStopRule | None = None
    planned_stop_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    add_entry_condition: AddEntryCondition | None = None
    note: str | None = None
