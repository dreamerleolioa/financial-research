"""The restricted Alembic revision format eligible for automated releases."""
import ast
from pathlib import Path
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


TYPES = {
    "bigint": sa.BigInteger,
    "boolean": sa.Boolean,
    "integer": sa.Integer,
    "jsonb": JSONB,
    "text": sa.Text,
    "timestamptz": lambda: sa.DateTime(timezone=True),
}
VERSION = re.compile(r"[a-f0-9]{12}")
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
TEMPLATE = """from ai_stock_sentinel.operations.online_ddl import apply
revision = {revision!r}
down_revision = {parent!r}
operation = {operation!r}
def upgrade():
    apply(operation)
def downgrade():
    raise NotImplementedError
"""


def valid_column(column):
    return (
        isinstance(column, tuple) and len(column) == 2
        and isinstance(column[0], str) and NAME.fullmatch(column[0])
        and isinstance(column[1], str) and column[1] in TYPES
    )


def valid(operation):
    if not isinstance(operation, tuple) or len(operation) < 2:
        return False
    kind, table, *args = operation
    if not isinstance(table, str) or not NAME.fullmatch(table) or table == "alembic_version":
        return False
    if kind == "add_nullable_column":
        return valid_column(tuple(args))
    if kind == "create_table" and len(args) == 1:
        columns = args[0]
        return (
            isinstance(columns, tuple) and 1 <= len(columns) <= 32
            and all(valid_column(column) for column in columns)
            and len({name for name, _ in columns}) == len(columns)
        )
    return False


def eligible(script, parent):
    if not VERSION.fullmatch(script.revision) or not VERSION.fullmatch(parent) or script.down_revision != parent:
        return False
    operation = getattr(script.module, "operation", None)
    if not valid(operation):
        return False
    # Arbitrary upgrade functions, imports, dependencies and extra code are
    # ineligible even if they happen to declare an allowed operation.
    source = ast.parse(Path(script.path).read_text(encoding="utf-8"))
    expected = ast.parse(TEMPLATE.format(revision=script.revision, parent=parent, operation=operation))
    return ast.dump(source) == ast.dump(expected)


def apply(operation):
    if not valid(operation):
        raise ValueError("Unsupported online DDL operation")
    kind, table, *args = operation

    def column(name, typename):
        return sa.Column(name, TYPES[typename](), nullable=True)

    if kind == "create_table":
        op.create_table(table, *(column(*item) for item in args[0]), schema="public")
        return

    bind = op.get_bind()
    quoted = bind.dialect.identifier_preparer.quote_identifier(table)
    relation = f"public.{quoted}"
    # Don't queue an exclusive lock behind live traffic. With the table locked,
    # reject inheritance/partition layouts before ALTER can lock other tables.
    bind.exec_driver_sql(f"LOCK TABLE ONLY {relation} IN ACCESS EXCLUSIVE MODE NOWAIT")
    standalone = bind.scalar(sa.text("""
        SELECT relkind = 'r' AND NOT relispartition
            AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhparent = c.oid OR inhrelid = c.oid)
        FROM pg_class AS c WHERE c.oid = to_regclass(:relation)
    """), {"relation": relation})
    if not standalone:
        raise ValueError("Online column additions require a standalone ordinary table")
    op.add_column(table, column(*args), schema="public")
