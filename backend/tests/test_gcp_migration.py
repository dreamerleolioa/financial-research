"""PG17 rehearsal: enable with GCP_MIGRATION_TEST_HOST on a private test server."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
import time

import psycopg2
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

import ai_stock_sentinel.db.models  # noqa: F401
import ai_stock_sentinel.user_models.user  # noqa: F401
from ai_stock_sentinel.db.session import Base

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts/gcp"
spec = importlib.util.spec_from_file_location("compare_manifests", SCRIPTS / "compare-manifests.py")
manifests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifests)
sequence_spec = importlib.util.spec_from_file_location("verify_sequences", SCRIPTS / "verify-sequences.py")
sequences = importlib.util.module_from_spec(sequence_spec)
sequence_spec.loader.exec_module(sequences)


def test_financial_allowlist_covers_all_models_without_other_apps():
    names = set((SCRIPTS / "financial-tables.txt").read_text().splitlines())
    public = {name for name in names if name.startswith("public.")}
    assert public == {f"public.{name}" for name in Base.metadata.tables} | {"public.alembic_version"}
    assert len(names - public) == 6
    assert len((SCRIPTS / "health-tables.txt").read_text().splitlines()) == 6


@pytest.mark.parametrize("value", ["TW", "TWO", "ma20", "not_recorded"])
def test_enum_coercion_normalization_preserves_real_constraint_changes(value):
    before = f"CHECK (market = ANY ((ARRAY['{value}'::character varying])::text[])))"
    after = f"CHECK (market = ANY (ARRAY[('{value}'::character varying)::text])))"
    normalized = manifests.normalize_constraints([["ck", before, True]])
    assert normalized == manifests.normalize_constraints([["ck", after, True]])
    assert normalized != manifests.normalize_constraints([["ck", after.replace(value, value + "_changed"), True]])
    assert normalized != manifests.normalize_constraints([["ck", after, False]])
    assert normalized != manifests.normalize_constraints([["ck", after.replace("market", "status"), True]])


def test_malformed_sequence_state_is_refused(tmp_path):
    state = tmp_path / "state.sql"
    state.write_text("DROP DATABASE example;\n")
    with pytest.raises(ValueError, match="Unrecognized"):
        sequences.parse_state(state)


@pytest.fixture
def pg17():
    socket = os.environ.get("GCP_MIGRATION_TEST_HOST")
    if not socket:
        pytest.skip("Set GCP_MIGRATION_TEST_HOST for the isolated PostgreSQL 17 rehearsal")
    assert socket == "127.0.0.1" or socket.startswith("/tmp/fr-gcp-pg."), "Only an isolated local fixture is allowed"
    if socket == "127.0.0.1":
        with psycopg2.connect(host=socket, port=65439, dbname="postgres", connect_timeout=10) as check:
            with check.cursor() as cursor:
                cursor.execute("SHOW cluster_name")
                assert cursor.fetchone()[0] == "gcp-migration-fixture"
    binary = Path(os.environ["GCP_MIGRATION_TEST_BIN"])
    env = {**os.environ, "PGHOST": socket, "PGPORT": "65439", "PGDATABASE": "postgres",
           "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    assert "17." in subprocess.check_output([str(binary / "psql"), "--version"], text=True)
    databases = []
    roles = []

    def make_database():
        name = "gcp_rehearsal_" + uuid.uuid4().hex
        subprocess.run([str(binary / "createdb"), name], env=env, check=True, capture_output=True)
        databases.append(name)
        db_env = {**env, "PGDATABASE": name}
        connection = psycopg2.connect(host=socket, port=65439, dbname=name)
        connection.autocommit = True
        return db_env, connection

    make_database.roles = roles
    yield make_database
    for name in databases:
        subprocess.run([str(binary / "dropdb"), "--force", name], env=env, check=True, capture_output=True)
    for name in roles:
        subprocess.run([str(binary / "psql"), "-Xq", "-v", "ON_ERROR_STOP=1", "-c", f"DROP ROLE {name}"],
                       env=env, check=True, capture_output=True)


def snapshot(env, mode, out):
    result = subprocess.run(["bash", str(SCRIPTS / "snapshot.sh"), mode, str(out)],
                            env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr
    return manifests.read_manifest(out / "manifest.jsonl")


def restore(env, source):
    result = subprocess.run(["psql", "-Xq", "-v", "ON_ERROR_STOP=1"],
                            input=gzip.decompress((source / "database.sql.gz").read_bytes()),
                            env=env, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode()


def test_pg17_financial_restore_excludes_n8n_and_detects_drift(pg17, tmp_path):
    source_env, source = pg17()
    target_env, target = pg17()
    engine = create_engine(URL.create("postgresql+psycopg2", database=source_env["PGDATABASE"],
                                     username=source_env.get("PGUSER"), password=source_env.get("PGPASSWORD"),
                                     query={"host": source_env["PGHOST"], "port": "65439"}))
    Base.metadata.create_all(engine)
    engine.dispose()
    with source.cursor() as cur:
        cur.execute("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)")
        cur.execute("INSERT INTO alembic_version VALUES ('ab2c3d4e5f6a')")
        cur.execute("INSERT INTO users(google_sub,email,name) VALUES ('test-sub','test@example.invalid',%s)",
                    ('繁體中文\nquote \\" 🙂',))
        cur.execute("CREATE SCHEMA maintenance_backup")
        for name in (SCRIPTS / "financial-tables.txt").read_text().splitlines():
            if name.startswith("maintenance_backup."):
                # Old migrations can leave physical attribute-number gaps. A dump
                # recreates logical columns compactly, including sequence owners.
                cur.execute(f"CREATE TABLE {name}(discarded text, id bigserial PRIMARY KEY, payload jsonb, blob_data bytea, amount numeric(30,10))")
                cur.execute(f"ALTER TABLE {name} DROP COLUMN discarded")
                cur.execute(f"INSERT INTO {name}(payload,blob_data,amount) VALUES (%s,%s,%s)",
                            (json.dumps({"zh": "測試\n", "array": [None, False, 0]}), b"\x00\xff\n", "123456789.0123456789"))
        cur.execute("CREATE TABLE n8n_workflow_entity(secret text)")
        cur.execute("INSERT INTO n8n_workflow_entity VALUES ('must not migrate')")
    # Writers stay active while the export and manifest use the same MVCC snapshot.
    stop = threading.Event()
    errors = []

    def writer():
        try:
            with psycopg2.connect(host=source_env["PGHOST"], port=65439, dbname=source_env["PGDATABASE"]) as conn:
                conn.autocommit = True
                count = 0
                while not stop.wait(0.01):
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO users(google_sub,email) VALUES (%s,%s)",
                                    (f"concurrent-{count}", f"concurrent-{count}@example.invalid"))
                    count += 1
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        expected = snapshot(source_env, "export-financial", tmp_path / "source")
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
    restore(target_env, tmp_path / "source")
    actual = snapshot(target_env, "verify-financial", tmp_path / "target")
    assert manifests.compare(expected, actual) == []
    with psycopg2.connect(host=target_env["PGHOST"], port=65439, dbname=target_env["PGDATABASE"]) as check:
        sequences.verify(check, sequences.parse_state(tmp_path / "source/sequence-state.sql"))
    with target.cursor() as cur:
        cur.execute("SELECT to_regclass('public.n8n_workflow_entity')")
        assert cur.fetchone()[0] is None
        cur.execute("INSERT INTO users(google_sub,email) VALUES ('next-sub','next@example.invalid') RETURNING id")
        assert cur.fetchone()[0] > expected["public.users"]["rows"]
    drift = snapshot(target_env, "verify-financial", tmp_path / "drift")
    assert "public.users: rows differs" in manifests.compare(expected, drift)
    with psycopg2.connect(host=target_env["PGHOST"], port=65439, dbname=target_env["PGDATABASE"]) as check:
        with pytest.raises(ValueError, match="state differs"):
            sequences.verify(check, sequences.parse_state(tmp_path / "source/sequence-state.sql"))
    with source.cursor() as cur:
        cur.execute("ALTER TABLE n8n_workflow_entity ADD COLUMN id serial PRIMARY KEY")
        cur.execute("ALTER TABLE users ADD COLUMN forbidden_n8n_id integer REFERENCES n8n_workflow_entity(id)")
    refused = subprocess.run(["bash", str(SCRIPTS / "snapshot.sh"), "export-financial", str(tmp_path / "refused")],
                             env=source_env, text=True, capture_output=True, timeout=30)
    assert refused.returncode != 0
    assert "Cross-application foreign key" in refused.stderr
    assert not (tmp_path / "refused/database.sql.gz").exists()
    source.close()
    target.close()


def test_pg17_health_schema_restore_is_empty_and_detects_unwanted_rows(pg17, tmp_path):
    source_env, source = pg17()
    target_env, target = pg17()
    with source.cursor() as cur:
        cur.execute("CREATE TYPE health_status AS ENUM ('ready','resting')")
        cur.execute("CREATE FUNCTION touch_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.payload = 'updated'; RETURN NEW; END $$")
        for name in (SCRIPTS / "health-tables.txt").read_text().splitlines():
            cur.execute(f"CREATE TABLE {name}(id bigserial PRIMARY KEY, payload text, status health_status DEFAULT 'ready')")
            cur.execute(f"INSERT INTO {name}(payload) VALUES ('private source data')")
        cur.execute("CREATE TRIGGER touch_fixture BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION touch_fixture()")
    expected = snapshot(source_env, "export-health-schema", tmp_path / "source")
    restore(target_env, tmp_path / "source")
    actual = snapshot(target_env, "verify-health-schema", tmp_path / "target")
    assert manifests.compare(expected, actual) == []
    assert all(item["rows"] == 0 for item in actual.values())
    with target.cursor() as cur:
        cur.execute("CREATE OR REPLACE FUNCTION touch_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.payload = 'wrong'; RETURN NEW; END $$")
    function_drift = snapshot(target_env, "verify-health-schema", tmp_path / "function-drift")
    assert "public.users: schema_objects differs" in manifests.compare(expected, function_drift)
    with target.cursor() as cur:
        cur.execute("INSERT INTO users(payload) VALUES ('unexpected')")
    drift = snapshot(target_env, "verify-health-schema", tmp_path / "drift")
    assert "public.users: rows differs" in manifests.compare(expected, drift)
    source.close()
    target.close()


def test_runtime_image_verifies_schema_without_ddl_permissions(pg17):
    image = os.environ.get("GCP_MIGRATION_IMAGE")
    postgres_container = os.environ.get("GCP_MIGRATION_TEST_CONTAINER")
    if not image or not postgres_container:
        pytest.skip("Run the Docker rehearsal with GCP_MIGRATION_IMAGE to test the exact image")
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from psycopg2 import sql

    env, connection = pg17()
    role = "gcp_api_" + uuid.uuid4().hex
    pg17.roles.append(role)
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with connection.cursor() as cur:
        cur.execute("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)")
        cur.execute("INSERT INTO alembic_version VALUES (%s)", (head,))
        cur.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'fixture-only-password'").format(sql.Identifier(role)))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        cur.execute(sql.SQL("GRANT SELECT ON alembic_version TO {}").format(sql.Identifier(role)))
    with psycopg2.connect(host=env["PGHOST"], port=65439, dbname=env["PGDATABASE"], user=role,
                         password="fixture-only-password") as restricted:
        with restricted.cursor() as cur:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("CREATE TABLE forbidden(id integer)")

    app_id = subprocess.check_output([
        "docker", "run", "--detach", "--platform", "linux/amd64",
        "--network", f"container:{postgres_container}",
        "--env", f"DATABASE_URL=postgresql://{role}:fixture-only-password@127.0.0.1:5432/{env['PGDATABASE']}",
        "--env", "DATABASE_MIGRATION_MODE=verify", "--env", f"EXPECTED_DATABASE_NAME={env['PGDATABASE']}",
        image], text=True).strip()
    try:
        success = False
        for _ in range(45):
            result = subprocess.run(["docker", "exec", app_id, "python", "-c",
                "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=1)) == {'status':'ok'}"],
                capture_output=True, timeout=10)
            if result.returncode == 0:
                success = True
                break
            if subprocess.check_output(["docker", "inspect", "--format", "{{.State.Running}}", app_id], text=True).strip() != "true":
                break
            time.sleep(1)
        if not success:
            logs = subprocess.run(["docker", "logs", app_id], text=True, capture_output=True)
            pytest.fail(f"Image did not start with verify-only DB privileges: {logs.stderr[-4000:]}")
    finally:
        subprocess.run(["docker", "rm", "-f", app_id], capture_output=True)
    connection.close()


def test_test_database_roles_enforce_real_permissions(pg17):
    finance_env, finance = pg17()
    health_env, health = pg17()
    administrator = 'gcp_admin_' + uuid.uuid4().hex
    with finance.cursor() as cur:
        cur.execute(f"CREATE ROLE {administrator} LOGIN CREATEROLE CREATEDB PASSWORD 'fixture-only-password'")
        cur.execute(f"ALTER DATABASE {finance_env['PGDATABASE']} OWNER TO {administrator}")
        cur.execute(f"ALTER DATABASE {health_env['PGDATABASE']} OWNER TO {administrator}")
    pg17.roles.append(administrator)
    role_names = ['financial_test_migrator', 'financial_test_api', 'financial_debug_readonly']
    bootstrap = (SCRIPTS / 'bootstrap-test-roles.sql').read_text().replace(
        'financial_research_test', finance_env['PGDATABASE']).replace('health-tracking', health_env['PGDATABASE'])
    # Exercise Cloud SQL's non-superuser administration, not fixture superuser
    # powers. Client wrappers use postgres, so SET ROLE at the SQL boundary.
    result = subprocess.run(['psql', '-Xq'], input=f'SET ROLE {administrator};\n' + bootstrap, text=True,
                            env={**finance_env, 'PGDATABASE': 'postgres'}, capture_output=True)
    assert result.returncode == 0, result.stderr
    pg17.roles.extend(role_names)
    with finance.cursor() as cur:
        for role in role_names:
            cur.execute(f"ALTER ROLE {role} PASSWORD 'fixture-only-password'")
        cur.execute('SET ROLE financial_test_migrator')
        cur.execute('CREATE TABLE business(id serial PRIMARY KEY, payload text)')
        cur.execute('CREATE TABLE alembic_version(version_num text)')
        cur.execute('CREATE SCHEMA maintenance_backup AUTHORIZATION financial_test_migrator')
        cur.execute('CREATE TABLE maintenance_backup.history(id serial PRIMARY KEY)')
        cur.execute('RESET ROLE')
    grants = (SCRIPTS / 'configure-test-privileges.sql').read_text().replace(
        'financial_research_test', finance_env['PGDATABASE'])
    # SET ROLE after a superuser connection would mask lost CONNECT permission.
    psycopg2.connect(host=finance_env['PGHOST'], port=65439,
        dbname=finance_env['PGDATABASE'], user=administrator, password='fixture-only-password').close()
    result = subprocess.run(['psql', '-Xq'], input=f'SET ROLE {administrator};\n' + grants,
                            text=True, env=finance_env, capture_output=True)
    assert result.returncode == 0, result.stderr

    def connect(role, database=None):
        connection = psycopg2.connect(host=finance_env['PGHOST'], port=65439,
            dbname=database or finance_env['PGDATABASE'], user=role, password='fixture-only-password')
        connection.autocommit = True
        return connection

    api = connect('financial_test_api')
    try:
        with api.cursor() as cur:
            cur.execute("INSERT INTO business(payload) VALUES ('allowed')")
            cur.execute('SELECT version_num FROM alembic_version')
            for statement in ['CREATE TABLE forbidden(id int)', "INSERT INTO alembic_version VALUES ('wrong')",
                              'SELECT * FROM maintenance_backup.history']:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute(statement)
    finally:
        api.close()
    debug = connect('financial_debug_readonly')
    try:
        with debug.cursor() as cur:
            cur.execute('SET default_transaction_read_only=off')
            cur.execute('SELECT * FROM business')
            cur.execute('SELECT * FROM maintenance_backup.history')
            for statement in ["INSERT INTO business(payload) VALUES ('denied')", 'DELETE FROM business',
                              'CREATE TABLE forbidden(id int)', 'CREATE TEMP TABLE forbidden(id int)',
                              "SELECT nextval('business_id_seq')"]:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute(statement)
    finally:
        debug.close()
    for role in ['financial_test_api', 'financial_debug_readonly']:
        with pytest.raises(psycopg2.OperationalError, match='permission denied for database'):
            connect(role, health_env['PGDATABASE'])
    finance.close()
    health.close()
