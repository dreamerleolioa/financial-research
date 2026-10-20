"""Add live Google account admission membership without changing existing users.

Revision ID: bc3d4e5f6a7b
Revises: ab2c3d4e5f6a
"""
from alembic import op
import sqlalchemy as sa

revision = "bc3d4e5f6a7b"
down_revision = "ab2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_allowed_accounts",
        sa.Column("google_sub", sa.String(255), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    if op.get_bind().dialect.name == "postgresql":
        # Override existing broad default table privileges in the same transaction.
        op.execute("""DO $$ BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'financial_prod_api') THEN
            REVOKE ALL ON public.auth_allowed_accounts FROM financial_prod_api;
            GRANT SELECT ON public.auth_allowed_accounts TO financial_prod_api;
          END IF;
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'financial_test_api') THEN
            REVOKE ALL ON public.auth_allowed_accounts FROM financial_test_api;
            GRANT SELECT ON public.auth_allowed_accounts TO financial_test_api;
          END IF;
          REVOKE ALL ON public.auth_allowed_accounts FROM PUBLIC;
        END $$""")


def downgrade() -> None:
    op.drop_table("auth_allowed_accounts")
