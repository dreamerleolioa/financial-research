from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from ai_stock_sentinel.portfolio.entry_record_contract import EntryRecordContext


class PortfolioCreateRequest(BaseModel):
    symbol: str
    entry_price: float = Field(gt=0)
    entry_date: date
    quantity: int = 0
    notes: str | None = None
    entry_record: EntryRecordContext | None = None


class ClosePortfolioRequest(BaseModel):
    exit_date: date
    exit_price: float = Field(gt=0, allow_inf_nan=False)
    exit_quantity: int = Field(gt=0)
    fees: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    taxes: float | None = Field(default=None, ge=0, allow_inf_nan=False)


AddEntryReasonCode = Literal[
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
    "not_recorded",
]

LifecycleSetupType = Literal[
    "breakout",
    "pullback",
    "mean_reversion",
    "value_revaluation",
    "earnings_or_event",
    "momentum_continuation",
    "long_term_accumulation",
    "defensive_rebalance",
    "other",
]

PlannedHoldingPeriod = Literal["short_term", "swing", "medium_term", "long_term", "not_recorded"]
DefaultStopRule = Literal[
    "break_20d_low",
    "break_ma20",
    "break_ma60",
    "cost_minus_pct",
    "fixed_price",
    "no_stop_recorded",
    "not_recorded",
]
AddEntryCondition = Literal[
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


class AddEntryRequest(BaseModel):
    event_date: date
    price: float = Field(gt=0, allow_inf_nan=False)
    quantity: int = Field(gt=0)
    fees: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    taxes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reason_code: AddEntryReasonCode
    plan_adherence: Literal["yes", "partial", "no", "not_recorded"]
    confidence_level: Literal["high", "medium", "low", "not_recorded"]
    note: str | None = None


class BackfillLifecyclePlanRequest(BaseModel):
    thesis: str | None = None
    setup_type: LifecycleSetupType | None = None
    planned_holding_period: PlannedHoldingPeriod | None = None
    default_stop_rule: DefaultStopRule | None = None
    add_entry_condition: AddEntryCondition | None = None
    planned_invalidation: str | None = None
    planned_stop_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    planned_target_or_scale_out_rule: str | None = None
    planned_risk_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    planned_risk_pct: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    position_sizing_rationale: str | None = None


class UpdatePortfolioRequest(BaseModel):
    entry_price: float = Field(gt=0)
    quantity: int
    entry_date: date
    notes: str | None = None
