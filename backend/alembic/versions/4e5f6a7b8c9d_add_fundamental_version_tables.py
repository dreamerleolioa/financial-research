"""add fundamental version tables

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "4e5f6a7b8c9d"
down_revision: str | Sequence[str] | None = "3d4e5f6a7b8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_fundamental_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_quarter", sa.Integer(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("statement_scope", sa.String(length=20), server_default="consolidated", nullable=False),
        sa.Column("industry_schema", sa.String(length=20), nullable=False),
        sa.Column("cumulative_eps", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("quarter_eps", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("source_report_date", sa.Date(), nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("availability_quality", sa.String(length=30), nullable=False),
        sa.Column("source_provider", sa.String(length=30), nullable=False),
        sa.Column("source_dataset", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("fiscal_quarter BETWEEN 1 AND 4", name="ck_company_fundamental_period_quarter"),
        sa.CheckConstraint(
            "availability_quality IN ('observed', 'historical_unknown')",
            name="ck_company_fundamental_period_availability_quality",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "fiscal_year",
            "fiscal_quarter",
            "statement_scope",
            "payload_hash",
            name="uq_company_fundamental_period_revision",
        ),
    )
    op.create_index(
        "idx_company_fundamental_period_symbol_period",
        "company_fundamental_periods",
        ["symbol", "fiscal_year", "fiscal_quarter"],
    )
    op.create_index(
        "idx_company_fundamental_period_first_observed",
        "company_fundamental_periods",
        ["first_observed_at"],
    )

    op.create_table(
        "company_dividend_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("dividend_year", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("period_label", sa.String(length=80), nullable=True),
        sa.Column("sequence", sa.String(length=30), nullable=True),
        sa.Column("decision_status", sa.String(length=60), nullable=True),
        sa.Column("board_date", sa.Date(), nullable=True),
        sa.Column("shareholder_date", sa.Date(), nullable=True),
        sa.Column("ex_dividend_date", sa.Date(), nullable=True),
        sa.Column("earnings_cash_per_share", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("legal_reserve_cash_per_share", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("capital_reserve_cash_per_share", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("total_cash_per_share", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("source_provider", sa.String(length=30), nullable=False),
        sa.Column("source_dataset", sa.String(length=80), nullable=False),
        sa.Column("event_key", sa.String(length=160), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "source_provider",
            "source_dataset",
            "event_key",
            "payload_hash",
            name="uq_company_dividend_event_revision",
        ),
    )
    op.create_index(
        "idx_company_dividend_event_symbol_year",
        "company_dividend_events",
        ["symbol", "dividend_year"],
    )
    op.create_index(
        "idx_company_dividend_event_first_observed",
        "company_dividend_events",
        ["first_observed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_company_dividend_event_first_observed", table_name="company_dividend_events")
    op.drop_index("idx_company_dividend_event_symbol_year", table_name="company_dividend_events")
    op.drop_table("company_dividend_events")
    op.drop_index("idx_company_fundamental_period_first_observed", table_name="company_fundamental_periods")
    op.drop_index("idx_company_fundamental_period_symbol_period", table_name="company_fundamental_periods")
    op.drop_table("company_fundamental_periods")
