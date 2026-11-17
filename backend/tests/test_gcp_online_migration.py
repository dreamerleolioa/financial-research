from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from test_gcp_migration import pg17  # noqa: F401


BASE = "123456789abc"
HEAD = "23456789abcd"
ADD = ("add_nullable_column", "live_data", "note", "text")
CREATE = ("create_table", "new_data", (("name", "text"),))


def revisions(tmp_path, *, operation=ADD, extra="", bridge=False):
    revisions_dir = tmp_path / "versions"
    revisions_dir.mkdir()
    env = Path(__file__).resolve().parents[1] / "alembic/env.py"
    (tmp_path / "env.py").write_text(env.read_text())
    (revisions_dir / "base.py").write_text(f"revision = {BASE!r}\ndown_revision = None\n")
    parent = BASE
    if bridge:
        parent = "3456789abcde"
        (revisions_dir / "bridge.py").write_text(f"revision = {parent!r}\ndown_revision = {BASE!r}\n")
    (revisions_dir / "head.py").write_text(
        "from ai_stock_sentinel.operations.online_ddl import apply\n"
        f"revision = {HEAD!r}\ndown_revision = {parent!r}\n"
        f"operation = {operation!r}\n"
        "def upgrade():\n    apply(operation)\n"
        "def downgrade():\n    raise NotImplementedError\n" + extra
    )
    config = Config()
    config.set_main_option("script_location", str(tmp_path))
    return config, ScriptDirectory.from_config(config)


@pytest.mark.parametrize(("operation", "extra", "bridge", "eligible"), [
    (ADD, "", False, True),
    (CREATE, "", False, True),
    (ADD, "", True, False),
    (ADD, "\noperation = ('drop_table', 'live_data')\n", False, False),
    (ADD, "\nimport os\n", False, False),
    (("add_nullable_column", "public.live_data", "note", "text"), "", False, False),
    (("add_nullable_column", "live_data", "note", "numeric(9,3)"), "", False, False),
    (("drop_table", "live_data"), "", False, False),
])
def test_eligibility_requires_one_restricted_additive_revision(tmp_path, operation, extra, bridge, eligible):
    from ai_stock_sentinel.operations.schema import classify
    _, scripts = revisions(tmp_path, operation=operation, extra=extra, bridge=bridge)
    report = classify(scripts, [BASE])
    assert report["online_eligible"] is eligible


@pytest.mark.parametrize("operation", [ADD, CREATE])
def test_pg17_online_upgrade_and_retry_with_live_reader(pg17, tmp_path, operation):
    from ai_stock_sentinel.operations.schema import migrate_online
    config, scripts = revisions(tmp_path, operation=operation)
    env, writer = pg17()
    writer.cursor().execute(
        "CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY); "
        "CREATE TABLE live_data(id int PRIMARY KEY); INSERT INTO live_data VALUES (1)"
    )
    writer.cursor().execute("INSERT INTO alembic_version VALUES (%s)", (BASE,))
    engine = sa.create_engine(sa.URL.create("postgresql+psycopg2", database=env["PGDATABASE"],
                                           query={"host": env["PGHOST"], "port": "65439"}))
    try:
        with engine.connect() as migration:
            options = {"database": env["PGDATABASE"], "user": env["PGUSER"]}
            assert migrate_online(migration, config, scripts, **options)["applied_revisions"] == [HEAD]
            assert migrate_online(migration, config, scripts, **options)["applied_revisions"] == []
        with writer.cursor() as cursor:
            cursor.execute("INSERT INTO live_data(id) VALUES (2) RETURNING id")
            assert cursor.fetchone() == (2,)
            cursor.execute("SELECT version_num FROM alembic_version")
            assert cursor.fetchone() == (HEAD,)
            cursor.execute("SELECT note FROM live_data" if operation == ADD else "SELECT name FROM new_data")
            assert cursor.fetchall() == ([(None,), (None,)] if operation == ADD else [])
    finally:
        engine.dispose()
        writer.close()


def test_pg17_busy_table_leaves_schema_and_revision_unchanged(pg17, tmp_path):
    from ai_stock_sentinel.operations.schema import migrate_online
    config, scripts = revisions(tmp_path)
    env, writer = pg17()
    with writer.cursor() as cursor:
        cursor.execute("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY); CREATE TABLE live_data(id int)")
        cursor.execute("INSERT INTO alembic_version VALUES (%s)", (BASE,))
        cursor.execute("BEGIN; INSERT INTO live_data VALUES (1)")
        engine = sa.create_engine(sa.URL.create("postgresql+psycopg2", database=env["PGDATABASE"],
                                               query={"host": env["PGHOST"], "port": "65439"}))
        try:
            with engine.connect() as migration, pytest.raises(sa.exc.DBAPIError) as error:
                migrate_online(migration, config, scripts, database=env["PGDATABASE"], user=env["PGUSER"])
            assert error.value.orig.pgcode == "55P03"
            cursor.execute("SELECT version_num FROM alembic_version")
            assert cursor.fetchone() == (BASE,)
            cursor.execute("SELECT * FROM live_data")
            assert cursor.fetchall() == [(1,)]
        finally:
            engine.dispose()
            writer.close()


def test_pg17_rolls_back_ddl_if_alembic_cannot_record_revision(pg17, tmp_path):
    from ai_stock_sentinel.operations.schema import migrate_online
    config, scripts = revisions(tmp_path, operation=CREATE)
    env, writer = pg17()
    try:
        with writer.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE alembic_version(version_num varchar(32) CHECK (version_num = %s)); "
                "INSERT INTO alembic_version VALUES (%s)", (BASE, BASE),
            )
        engine = sa.create_engine(sa.URL.create("postgresql+psycopg2", database=env["PGDATABASE"],
                                               query={"host": env["PGHOST"], "port": "65439"}))
        try:
            with engine.connect() as migration, pytest.raises(sa.exc.IntegrityError):
                migrate_online(migration, config, scripts, database=env["PGDATABASE"], user=env["PGUSER"])
        finally:
            engine.dispose()
        with writer.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.new_data'), version_num FROM alembic_version")
            assert cursor.fetchone() == (None, BASE)
    finally:
        writer.close()
