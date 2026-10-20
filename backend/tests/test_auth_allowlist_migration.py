import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import psycopg2
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool

from test_gcp_migration import pg17  # noqa: F401


def load_migration(connection):
    path = Path(__file__).parents[1] / "alembic/versions/bc3d4e5f6a7b_add_auth_allowed_accounts.py"
    spec = importlib.util.spec_from_file_location("allowlist_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def test_allowlist_schema_upgrade_and_downgrade_are_additive():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE preserved_data (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO preserved_data VALUES (1)")
        migration = load_migration(connection)
        migration.upgrade()
        assert "auth_allowed_accounts" in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT count(*) FROM auth_allowed_accounts").scalar() == 0
        migration.downgrade()
        assert "auth_allowed_accounts" not in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT id FROM preserved_data").scalar() == 1
    engine.dispose()


@pytest.mark.parametrize("api_role", ["financial_prod_api", "financial_test_api"])
def test_pg17_api_cannot_modify_membership_despite_broad_default_privileges(pg17, api_role):
    env, admin = pg17()
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {api_role} NOLOGIN")
        pg17.roles.append(api_role)
        cursor.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {api_role}, PUBLIC")
    engine = create_engine("postgresql+psycopg2://", poolclass=NullPool,
                           creator=lambda: psycopg2.connect(host=env["PGHOST"], port=65439,
                                                           dbname=env["PGDATABASE"]))
    try:
        with engine.begin() as connection:
            load_migration(connection).upgrade()
        with admin.cursor() as cursor:
            for permission in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                cursor.execute("SELECT has_table_privilege(%s, 'auth_allowed_accounts', %s)",
                               (api_role, permission))
                assert cursor.fetchone()[0] is (permission == "SELECT")
            cursor.execute("INSERT INTO auth_allowed_accounts(google_sub,email) VALUES ('owner-sub','owner@gmail.com')")
            cursor.execute(f"SET ROLE {api_role}")
            cursor.execute("SELECT google_sub FROM auth_allowed_accounts")
            assert cursor.fetchone()[0] == "owner-sub"
            for query in ("DELETE FROM auth_allowed_accounts", "UPDATE auth_allowed_accounts SET google_sub='intruder'",
                          "INSERT INTO auth_allowed_accounts(google_sub,email) VALUES ('intruder','x@gmail.com')"):
                try:
                    cursor.execute(query)
                except psycopg2.errors.InsufficientPrivilege:
                    pass
                else:
                    raise AssertionError("API membership mutation was permitted")
            cursor.execute("RESET ROLE")
    finally:
        admin.close()
        engine.dispose()


@pytest.mark.parametrize("environment", ["production", "test"])
def test_pg17_restore_regrant_removes_public_and_api_membership_writes(pg17, environment):
    env, admin = pg17()
    is_prod = environment == "production"
    api_role = "financial_prod_api" if is_prod else "financial_test_api"
    migrator = "financial_prod_migrator" if is_prod else "financial_test_migrator"
    debug = "financial_prod_debug_readonly" if is_prod else "financial_debug_readonly"
    with admin.cursor() as cursor:
        for role in (api_role, migrator, debug):
            cursor.execute(f"CREATE ROLE {role} NOLOGIN")
            pg17.roles.append(role)
        cursor.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {migrator}")
        cursor.execute(f"CREATE SCHEMA maintenance_backup AUTHORIZATION {migrator}")
        cursor.execute(f"SET ROLE {migrator}")
        cursor.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {api_role}, PUBLIC")
        # ACL-free restore recreates objects under the target's defaults. It does
        # not execute migration.upgrade(), so the re-grant script must fix this.
        cursor.execute("CREATE TABLE auth_allowed_accounts (google_sub VARCHAR(255) PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL, created_at TIMESTAMPTZ DEFAULT now() NOT NULL)")
        cursor.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        cursor.execute("RESET ROLE")
        script = Path(__file__).parents[2] / f"scripts/gcp/configure-{environment}-privileges.sql"
        sql = "\n".join(line for line in script.read_text().splitlines() if not line.startswith("\\"))
        expected = "financial_research" if is_prod else "financial_research_test"
        cursor.execute(sql.replace("'" + expected + "'", "'" + env["PGDATABASE"] + "'"))
        for permission in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT has_table_privilege(%s, 'auth_allowed_accounts', %s)", (api_role, permission))
            assert cursor.fetchone()[0] is (permission == "SELECT")
    admin.close()
