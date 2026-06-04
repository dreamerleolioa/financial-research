"""add portfolio close fields

Revision ID: ab12cd34ef56
Revises: f2a7c8d9e0b1
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ab12cd34ef56"
down_revision: Union[str, Sequence[str], None] = "f2a7c8d9e0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_portfolio", sa.Column("exit_date", sa.Date(), nullable=True))
    op.add_column("user_portfolio", sa.Column("exit_price", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("user_portfolio", sa.Column("exit_quantity", sa.Integer(), nullable=True))
    op.add_column("user_portfolio", sa.Column("exit_fees", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("user_portfolio", sa.Column("exit_taxes", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("user_portfolio", sa.Column("realized_pnl", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("user_portfolio", sa.Column("realized_return_pct", sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column("user_portfolio", sa.Column("holding_days", sa.Integer(), nullable=True))
    op.drop_constraint("uq_portfolio_user_symbol", "user_portfolio", type_="unique")
    op.create_index(
        "uq_portfolio_user_symbol_active",
        "user_portfolio",
        ["user_id", "symbol"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_portfolio_user_symbol_active", table_name="user_portfolio")
    op.create_unique_constraint("uq_portfolio_user_symbol", "user_portfolio", ["user_id", "symbol"])
    op.drop_column("user_portfolio", "holding_days")
    op.drop_column("user_portfolio", "realized_return_pct")
    op.drop_column("user_portfolio", "realized_pnl")
    op.drop_column("user_portfolio", "exit_taxes")
    op.drop_column("user_portfolio", "exit_fees")
    op.drop_column("user_portfolio", "exit_quantity")
    op.drop_column("user_portfolio", "exit_price")
    op.drop_column("user_portfolio", "exit_date")
