# backend/src/ai_stock_sentinel/db/models.py
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.user_models.user import User  # noqa: F401  re-export for unified import


class UserPortfolio(Base):
    __tablename__ = "user_portfolio"
    __table_args__ = (
        Index(
            "uq_portfolio_user_symbol_active",
            "user_id",
            "symbol",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id:          Mapped[int]        = mapped_column(Integer, primary_key=True)
    user_id:     Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    position_group_id: Mapped[str]  = mapped_column(String(36), nullable=False, default=lambda: str(uuid.uuid4()))
    symbol:      Mapped[str]        = mapped_column(String(20), nullable=False)
    entry_price: Mapped[float]      = mapped_column(Numeric(10, 2), nullable=False)
    quantity:    Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    entry_date:  Mapped[date]       = mapped_column(Date, nullable=False)
    is_active:   Mapped[bool]       = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    exit_date:   Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price:  Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    exit_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_fees:   Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    exit_taxes:  Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    realized_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes:       Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


POSITION_EVENT_TYPES = (
    "initial_entry",
    "add_entry",
    "partial_exit",
    "full_exit",
    "manual_adjustment",
)

POSITION_EVENT_SOURCES = (
    "synthetic_from_portfolio_row",
    "user_backfilled",
    "user_recorded_at_event_time",
    "manual_record_correction",
    "not_recorded",
)

POSITION_EVENT_REASON_CATEGORIES = (
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

POSITION_EVENT_ENTRY_REASON_CODES = (
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
)

POSITION_EVENT_EXIT_REASON_CODES = (
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
    "manual_record_correction",
)

POSITION_EVENT_REASON_CODES = tuple(dict.fromkeys(POSITION_EVENT_ENTRY_REASON_CODES + POSITION_EVENT_EXIT_REASON_CODES))

POSITION_EVENT_PLAN_ADHERENCE_VALUES = (
    "yes",
    "partial",
    "no",
    "not_recorded",
)

POSITION_EVENT_CONFIDENCE_LEVELS = (
    "high",
    "medium",
    "low",
    "not_recorded",
)

POSITION_LIFECYCLE_SETUP_TYPES = (
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

POSITION_LIFECYCLE_HOLDING_PERIODS = (
    "short_term",
    "swing",
    "medium_term",
    "long_term",
    "not_recorded",
)

POSITION_LIFECYCLE_DEFAULT_STOP_RULES = (
    "break_20d_low",
    "break_ma20",
    "break_ma60",
    "cost_minus_pct",
    "fixed_price",
    "no_stop_recorded",
    "not_recorded",
)

POSITION_LIFECYCLE_ADD_ENTRY_CONDITIONS = (
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


def _nullable_in_constraint(column: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column} IS NULL OR {column} IN ({quoted_values})"


class PositionEvent(Base):
    __tablename__ = "position_event"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('initial_entry', 'add_entry', 'partial_exit', 'full_exit', 'manual_adjustment')",
            name="ck_position_event_event_type",
        ),
        CheckConstraint(
            "source IN ('synthetic_from_portfolio_row', 'user_backfilled', 'user_recorded_at_event_time', 'manual_record_correction', 'not_recorded')",
            name="ck_position_event_source",
        ),
        CheckConstraint(
            _nullable_in_constraint("reason_category", POSITION_EVENT_REASON_CATEGORIES),
            name="ck_position_event_reason_category",
        ),
        CheckConstraint(
            _nullable_in_constraint("reason_code", POSITION_EVENT_REASON_CODES),
            name="ck_position_event_reason_code",
        ),
        CheckConstraint(
            _nullable_in_constraint("plan_adherence", POSITION_EVENT_PLAN_ADHERENCE_VALUES),
            name="ck_position_event_plan_adherence",
        ),
        CheckConstraint(
            _nullable_in_constraint("confidence_level", POSITION_EVENT_CONFIDENCE_LEVELS),
            name="ck_position_event_confidence_level",
        ),
        Index("idx_position_event_user_id", "user_id"),
        Index("idx_position_event_position_group_id", "position_group_id"),
        Index("idx_position_event_symbol", "symbol"),
        Index("idx_position_event_event_date", "event_date"),
        Index("idx_position_event_user_group_date", "user_id", "position_group_id", "event_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    position_group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    taxes: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    source_portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("user_portfolio.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plan_adherence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    data_quality_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class PositionLifecyclePlan(Base):
    __tablename__ = "position_lifecycle_plan"
    __table_args__ = (
        UniqueConstraint("position_group_id", name="uq_position_lifecycle_plan_group"),
        CheckConstraint(
            _nullable_in_constraint("setup_type", POSITION_LIFECYCLE_SETUP_TYPES),
            name="ck_position_lifecycle_plan_setup_type",
        ),
        CheckConstraint(
            _nullable_in_constraint("planned_holding_period", POSITION_LIFECYCLE_HOLDING_PERIODS),
            name="ck_position_lifecycle_plan_holding_period",
        ),
        CheckConstraint(
            _nullable_in_constraint("default_stop_rule", POSITION_LIFECYCLE_DEFAULT_STOP_RULES),
            name="ck_position_lifecycle_plan_default_stop_rule",
        ),
        CheckConstraint(
            _nullable_in_constraint("add_entry_condition", POSITION_LIFECYCLE_ADD_ENTRY_CONDITIONS),
            name="ck_position_lifecycle_plan_add_entry_condition",
        ),
        CheckConstraint(
            "source IN ('synthetic_from_portfolio_row', 'user_backfilled', 'user_recorded_at_event_time', 'manual_record_correction', 'not_recorded')",
            name="ck_position_lifecycle_plan_source",
        ),
        Index("idx_position_lifecycle_plan_user_id", "user_id"),
        Index("idx_position_lifecycle_plan_position_group_id", "position_group_id"),
        Index("idx_position_lifecycle_plan_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    position_group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    source_portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("user_portfolio.id", ondelete="SET NULL"), nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    setup_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    planned_holding_period: Mapped[str | None] = mapped_column(String(20), nullable=True)
    default_stop_rule: Mapped[str | None] = mapped_column(String(30), nullable=True)
    add_entry_condition: Mapped[str | None] = mapped_column(String(40), nullable=True)
    planned_invalidation: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_stop_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    planned_target_or_scale_out_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_risk_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    planned_risk_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    position_sizing_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    created_after_entry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TradeReview(Base):
    __tablename__ = "trade_review"
    __table_args__ = (
        UniqueConstraint("portfolio_id", name="uq_trade_review_portfolio_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("user_portfolio.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    position_group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    review_version: Mapped[str] = mapped_column(String(30), nullable=False, default="trade-review-v1")
    review_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class PositionLifecycleReview(Base):
    __tablename__ = "position_lifecycle_review"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "position_group_id",
            "review_version",
            name="uq_position_lifecycle_review_user_group_version",
        ),
        Index("idx_position_lifecycle_review_user_id", "user_id"),
        Index("idx_position_lifecycle_review_position_group_id", "position_group_id"),
        Index("idx_position_lifecycle_review_symbol", "symbol"),
        Index("idx_position_lifecycle_review_user_group", "user_id", "position_group_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    position_group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    review_version: Mapped[str] = mapped_column(String(40), nullable=False, default="position-lifecycle-review-v1")
    review_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DailyAnalysisLog(Base):
    __tablename__ = "daily_analysis_log"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "record_date", name="uq_log_user_symbol_date"),
        Index("idx_log_indicators_gin", "indicators", postgresql_using="gin"),
    )

    id:                 Mapped[int]          = mapped_column(Integer, primary_key=True)
    user_id:            Mapped[int | None]   = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    symbol:             Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:        Mapped[date]         = mapped_column(Date, nullable=False)
    signal_confidence:  Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    strategy_version:   Mapped[str | None]   = mapped_column(String(20), nullable=True)
    action_tag:         Mapped[str | None]   = mapped_column(String(20), nullable=True)
    recommended_action: Mapped[str | None]   = mapped_column(Text, nullable=True)
    indicators:         Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    final_verdict:      Mapped[str | None]   = mapped_column(Text, nullable=True)
    prev_action_tag:    Mapped[str | None]   = mapped_column(String(20), nullable=True)
    prev_confidence:    Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    analysis_is_final:  Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at:         Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockRawData(Base):
    __tablename__ = "stock_raw_data"
    __table_args__ = (
        UniqueConstraint("symbol", "record_date", name="uq_raw_symbol_date"),
        Index("idx_raw_technical_gin", "technical", postgresql_using="gin"),
        Index("idx_raw_institutional_gin", "institutional", postgresql_using="gin"),
    )

    id:                 Mapped[int]          = mapped_column(Integer, primary_key=True)
    symbol:             Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:        Mapped[date]         = mapped_column(Date, nullable=False)
    technical:          Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    institutional:      Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    fundamental:        Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    raw_data_is_final:  Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    fetched_at:         Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockAnalysisCache(Base):
    __tablename__ = "stock_analysis_cache"
    __table_args__ = (
        UniqueConstraint("symbol", "record_date", "analysis_type", name="uq_cache_symbol_date_type"),
        Index("idx_cache_indicators_gin", "indicators", postgresql_using="gin"),
    )

    id:                 Mapped[int]          = mapped_column(Integer, primary_key=True)
    symbol:             Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:        Mapped[date]         = mapped_column(Date, nullable=False)
    analysis_type:      Mapped[str]          = mapped_column(String(20), nullable=False, default="general")
    signal_confidence:  Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    strategy_version:   Mapped[str | None]   = mapped_column(String(20), nullable=True)
    action_tag:         Mapped[str | None]   = mapped_column(String(20), nullable=True)
    recommended_action: Mapped[str | None]   = mapped_column(Text, nullable=True)
    indicators:         Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    final_verdict:      Mapped[str | None]   = mapped_column(Text, nullable=True)
    prev_action_tag:    Mapped[str | None]   = mapped_column(String(20), nullable=True)
    prev_confidence:    Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    analysis_is_final:  Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at:         Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    full_result:        Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    updated_at:         Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class BacktestRun(Base):
    __tablename__ = "backtest_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    hold_days: Mapped[int] = mapped_column(Integer, nullable=False)
    days_lookback: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    total_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    results: Mapped[list["BacktestResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BacktestResult(Base):
    __tablename__ = "backtest_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    p0_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    pN_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)  # win/loss/draw/skip
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    conviction_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    strategy_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    action_tag: Mapped[str | None] = mapped_column(String(20), nullable=True)
    log_id: Mapped[int | None] = mapped_column(
        ForeignKey("daily_analysis_log.id"), nullable=True
    )

    run: Mapped["BacktestRun"] = relationship(back_populates="results")


class DailyRadarRun(Base):
    __tablename__ = "daily_radar_runs"
    __table_args__ = (
        Index("idx_daily_radar_runs_run_date", "run_date"),
    )

    id:               Mapped[int]         = mapped_column(Integer, primary_key=True)
    run_date:         Mapped[date]        = mapped_column(Date, nullable=False)
    market:           Mapped[str]         = mapped_column(String(20), nullable=False)
    status:           Mapped[str]         = mapped_column(String(20), nullable=False)
    started_at:       Mapped[datetime]    = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at:      Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    universe_count:   Mapped[int]         = mapped_column(Integer, nullable=False, default=0)
    prefilter_count:  Mapped[int]         = mapped_column(Integer, nullable=False, default=0)
    candidate_count:  Mapped[int]         = mapped_column(Integer, nullable=False, default=0)
    errors:           Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at:       Mapped[datetime]    = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    candidates: Mapped[list["DailyRadarCandidate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DailyRadarCandidate(Base):
    __tablename__ = "daily_radar_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "symbol", name="uq_daily_radar_candidates_run_symbol"),
        Index("idx_daily_radar_candidates_symbol", "symbol"),
        Index("idx_daily_radar_candidates_primary_bucket", "primary_bucket"),
        Index("idx_daily_radar_candidates_observation_score", "observation_score"),
    )

    id:                Mapped[int]         = mapped_column(Integer, primary_key=True)
    run_id:            Mapped[int]         = mapped_column(ForeignKey("daily_radar_runs.id"), nullable=False)
    symbol:            Mapped[str]         = mapped_column(String(20), nullable=False)
    name:              Mapped[str]         = mapped_column(String(100), nullable=False)
    primary_bucket:    Mapped[str]         = mapped_column(String(40), nullable=False)
    secondary_buckets: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    observation_score: Mapped[int]         = mapped_column(Integer, nullable=False)
    bucket_scores:     Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_labels:       Mapped[list | None] = mapped_column(JSONB, nullable=True)
    matched_rules:     Mapped[list | None] = mapped_column(JSONB, nullable=True)
    explanation:       Mapped[str]         = mapped_column(Text, nullable=False)
    repeat_status:     Mapped[str]         = mapped_column(String(20), nullable=False)
    score_breakdown:   Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_snapshot:    Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    data_dates:        Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at:        Mapped[datetime]    = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run: Mapped["DailyRadarRun"] = relationship(back_populates="candidates")
    forward_validation_results: Mapped[list["DailyRadarForwardValidationResult"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class DailyRadarForwardValidationResult(Base):
    __tablename__ = "daily_radar_forward_validation_results"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "window_days",
            "validation_version",
            name="uq_daily_radar_forward_validation_candidate_window_version",
        ),
        Index("idx_daily_radar_forward_validation_candidate_id", "candidate_id"),
        Index("idx_daily_radar_forward_validation_window_days", "window_days"),
        Index("idx_daily_radar_forward_validation_status", "status"),
        Index("idx_daily_radar_forward_validation_version", "validation_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("daily_radar_candidates.id"), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_version: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    benchmark_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    outcome: Mapped[dict] = mapped_column(JSONB, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    candidate: Mapped["DailyRadarCandidate"] = relationship(back_populates="forward_validation_results")


class SharedBackgroundContext(Base):
    __tablename__ = "shared_background_contexts"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "context_type",
            "replay_key",
            name="uq_shared_background_context_symbol_type_replay",
        ),
        CheckConstraint(
            "freshness IN ('fresh', 'stale', 'missing', 'unknown')",
            name="ck_shared_background_context_freshness",
        ),
        Index("idx_shared_background_context_symbol", "symbol"),
        Index("idx_shared_background_context_context_type", "context_type"),
        Index("idx_shared_background_context_as_of_date", "as_of_date"),
        Index("idx_shared_background_context_freshness", "freshness"),
        Index("idx_shared_background_context_replay_key", "replay_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    context_type: Mapped[str] = mapped_column(String(50), nullable=False)
    applicable_consumers: Mapped[list] = mapped_column(JSONB, nullable=False)
    source: Mapped[dict] = mapped_column(JSONB, nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    freshness: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    missing_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    replay_key: Mapped[str] = mapped_column(String(240), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
