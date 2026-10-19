"""Transactional final-copy and rollback readiness; isolated PG17 only."""
import hashlib
import json
import pytest

from test_gcp_cutover import load
from test_gcp_migration import pg17, snapshot, SCRIPTS  # noqa: F401


def test_unapproved_restore_never_opens_database(tmp_path,monkeypatch):
    module=load('restore-production')
    monkeypatch.delenv('CONFIRM_FINAL_CUTOVER_APPROVED',raising=False)
    with pytest.raises(ValueError,match='approval'):module.require_apply_approval()


def test_bundle_rejects_unapproved_or_changed_files(tmp_path):
    module=load('restore-production')
    (tmp_path/'manifest.jsonl').write_text('{}\n')
    with pytest.raises(ValueError):module.load_bundle(tmp_path,'a'*64,'b'*64)


def build_database(connection):
    import ai_stock_sentinel.db.models  # noqa: F401
    import ai_stock_sentinel.user_models.user  # noqa: F401
    from ai_stock_sentinel.db.session import Base
    from sqlalchemy.schema import CreateTable, CreateIndex
    from sqlalchemy.dialects import postgresql
    with connection.cursor() as c:
        for table in Base.metadata.sorted_tables:
            c.execute(str(CreateTable(table).compile(dialect=postgresql.dialect())))
            for index in table.indexes:c.execute(str(CreateIndex(index).compile(dialect=postgresql.dialect())))
        c.execute('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)')
        c.execute('INSERT INTO alembic_version VALUES (%s)',(load('restore-production').expected_head(),))
        c.execute('CREATE SCHEMA maintenance_backup')
        for name in (SCRIPTS/'financial-tables.txt').read_text().splitlines():
            if name.startswith('maintenance_backup.'):
                c.execute(f'CREATE TABLE {name}(id bigserial PRIMARY KEY, payload text)')
                c.execute(f"INSERT INTO {name}(payload) VALUES ('original')")


def test_restore_is_atomic_and_refuses_rollback_after_new_writes(pg17,tmp_path):
    m=load('restore-production')
    source_env,source=pg17();target_env,target=pg17()
    build_database(source);build_database(target)
    with source.cursor() as c:c.execute("INSERT INTO users(google_sub,email) VALUES ('new-user','fixture@example.invalid')")
    snapshot(source_env,'export-financial',tmp_path/'source')
    snapshot(target_env,'export-financial',tmp_path/'rollback')
    def bundle(name):
        p=tmp_path/name
        return m.load_bundle(p,hashlib.sha256((p/'manifest.jsonl').read_bytes()).hexdigest(),
                             hashlib.sha256((p/'SHA256SUMS').read_bytes()).hexdigest())
    final,backup=bundle('source'),bundle('rollback')
    import shutil
    tampered=tmp_path/'tampered';shutil.copytree(tmp_path/'source',tampered)
    with (tampered/'database.sql.gz').open('ab') as f:f.write(b'modified')
    checks=(tampered/'SHA256SUMS').read_text().replace(m.digest(final['root']/'database.sql.gz'),m.digest(tampered/'database.sql.gz'))
    (tampered/'SHA256SUMS').write_text(checks)
    with pytest.raises(ValueError,match='Unapproved bundle'):
        m.load_bundle(tampered,final['manifest_sha256'],final['bundle_sha256'])
    with target.cursor() as c:c.execute('SELECT current_user');owner=c.fetchone()[0]
    target.close()
    # Corrupt a verified expected content hash in memory. Restore fails before
    # COMMIT even though the source dump has already inserted all rows.
    bad={**final,'manifest':{k:dict(v) for k,v in final['manifest'].items()}}
    bad['manifest']['public.users']['content_md5']='bad'
    with pytest.raises(ValueError,match='public.users'):
        m.restore(target_env,bad,backup,tmp_path/'failed.json',database=target_env['PGDATABASE'],owner=owner,privileges=False)
    after=snapshot(target_env,'verify-financial',tmp_path/'after-failure')
    assert m.manifests.compare(backup['manifest'],after)==[]
    m.restore(target_env,final,backup,tmp_path/'success.json',database=target_env['PGDATABASE'],owner=owner,privileges=False)
    after=snapshot(target_env,'verify-financial',tmp_path/'after-success')
    assert m.manifests.compare(final['manifest'],after)==[]
    assert json.loads((tmp_path/'success.json').read_text())['status']=='committed_verified'
    m.verify_rollback(target_env,final,database=target_env['PGDATABASE'],owner=owner)
    import psycopg2
    with psycopg2.connect(host=target_env['PGHOST'],port=target_env['PGPORT'],dbname=target_env['PGDATABASE']) as changed:
        with changed.cursor() as c:c.execute("INSERT INTO users(google_sub,email) VALUES ('after-cutover','new@example.invalid')")
    with pytest.raises(ValueError,match='public.users'):
        m.verify_rollback(target_env,final,database=target_env['PGDATABASE'],owner=owner)
    # Reusing an audit path cannot silently replay a destructive restore.
    with pytest.raises(FileExistsError):
        m.restore(target_env,final,backup,tmp_path/'success.json',database=target_env['PGDATABASE'],owner=owner,privileges=False)
