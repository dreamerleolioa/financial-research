# backend/src/ai_stock_sentinel/db/models.py
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.user_models.user import User  # noqa: F401  re-export for unified import


class UserPortfolio(Base):
    __tablename__ = "user_portfolio"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_portfolio_user_symbol"),
    )

    id:          Mapped[int]        = mapped_column(Integer, primary_key=True)
    user_id:     Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    symbol:      Mapped[str]        = mapped_column(String(20), nullable=False)
    entry_price: Mapped[float]      = mapped_column(Numeric(10, 2), nullable=False)
    quantity:    Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    entry_date:  Mapped[date]       = mapped_column(Date, nullable=False)
    is_active:   Mapped[bool]       = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes:       Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


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
        UniqueConstraint("symbol", "record_date", name="uq_cache_symbol_date"),
        Index("idx_cache_indicators_gin", "indicators", postgresql_using="gin"),
    )

    id:                 Mapped[int]          = mapped_column(Integer, primary_key=True)
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
