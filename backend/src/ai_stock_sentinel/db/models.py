# backend/src/ai_stock_sentinel/db/models.py
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
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
