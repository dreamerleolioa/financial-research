"""Schema gates use isolated PostgreSQL and fake cloud commands only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import hashlib
from datetime import datetime, timezone

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError, ProgrammingError
from test_gcp_migration import pg17  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts/gcp'
HEAD = 'de5f6a7b8c9d'
BASE = 'bc3d4e5f6a7b'
IMAGE = 'asia-east1-docker.pkg.dev/financial-research-20261019/financial-research/backend@sha256:' + 'a' * 64


def schema_module():
    from ai_stock_sentinel.operations import schema
    return schema


@pytest.mark.parametrize('current,status,pending', [
    ([HEAD], 'ready', []),
    ([BASE], 'migration_required', ['cd4e5f6a7b8c', HEAD]),
    ([], 'uninitialized', []),
    (['unknown'], 'incompatible', []),
    ([BASE, HEAD], 'incompatible', []),
])
def test_schema_requires_exact_known_head(current, status, pending):
    script = ScriptDirectory.from_config(Config(str(ROOT / 'backend/alembic.ini')))
    result = schema_module().classify(script, current)
    assert result['status'] == status
    assert result['pending_revisions'] == pending
    assert result['expected_revisions'] == [HEAD]


def cloud_fixture(tmp_path):
    from test_gcp_production import service
    state = service()
    fake = tmp_path / 'gcloud'
    fake.write_text(f'#!{sys.executable}\n' + '''import json,os,sys
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['CLOUD_LOG'],'a') as f:f.write(json.dumps(a)+'\\n')
if a[:2]==['auth','list']:print('financial-prod-deploy@financial-research-20261019.iam.gserviceaccount.com')
elif a[:3]==['run','services','describe']:
 p=Path(os.environ['SERVICE_STATE'])
 print(p.read_text() if p.exists() else os.environ['SERVICE_JSON'])
elif a[:3]==['run','services','get-iam-policy']:print('{"bindings":[]}')
elif a[:3]==['run','jobs','update']:sys.exit(int(os.environ.get('SCHEMA_UPDATE_EXIT','0')))
elif a[:3]==['run','jobs','execute']:
 if os.environ.get('SCHEMA_DRAIN_RACE'):
  data=json.loads(os.environ['SERVICE_JSON'])
  data['metadata']['annotations'].update({'run.googleapis.com/scalingMode':'manual','run.googleapis.com/manualInstanceCount':'0'})
  Path(os.environ['SERVICE_STATE']).write_text(json.dumps(data))
 sys.exit(int(os.environ.get('SCHEMA_EXIT','0')))
''')
    fake.chmod(0o700)
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'GCP_PROJECT_ID': 'financial-research-20261019', 'SERVICE_JSON': json.dumps(state),
           'SERVICE_STATE': str(tmp_path/'service-state.json'),
           'CLOUD_LOG': str(tmp_path / 'cloud.jsonl')}
    return env, Path(env['CLOUD_LOG'])


@pytest.mark.parametrize('exit_code', ['1', '2'])
def test_schema_mismatch_or_transport_failure_stops_before_service_update(tmp_path, exit_code):
    env, log = cloud_fixture(tmp_path)
    env['SCHEMA_EXIT'] = exit_code
    run = subprocess.run(['bash', str(SCRIPTS / 'deploy-production.sh'), IMAGE],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    assert run.returncode != 0
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert any(x[:3] == ['run', 'jobs', 'execute'] for x in calls)
    assert not any(x[:2] == ['run', 'deploy'] for x in calls)
    assert 'migration' in run.stderr.lower()


def test_missing_checker_or_permissions_stops_before_service_update(tmp_path):
    env,log=cloud_fixture(tmp_path)
    env['SCHEMA_UPDATE_EXIT']='1'
    run=subprocess.run(['bash',str(SCRIPTS/'deploy-production.sh'),IMAGE],cwd=ROOT,env=env,capture_output=True,text=True)
    assert run.returncode!=0 and 'job/IAM setup' in run.stderr
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert not any(x[:2]==['run','deploy'] or x[:3]==['run','jobs','execute'] for x in calls)


def test_readonly_schema_gate_precedes_service_update(tmp_path):
    env, log = cloud_fixture(tmp_path)
    run = subprocess.run(['bash', str(SCRIPTS / 'deploy-production.sh'), IMAGE],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    probe = next(x for x in calls if x[:3] == ['run', 'jobs', 'update'])
    assert probe[3] == 'financial-research-prod-schema-check'
    assert '--image=' + IMAGE in probe
    assert '--service-account=financial-prod-schema-check@financial-research-20261019.iam.gserviceaccount.com' in probe
    assert '--set-secrets=PGPASSWORD=financial-prod-debug-password:1' in probe
    assert calls.index(next(x for x in calls if x[:3] == ['run','jobs','execute'])) < calls.index(next(x for x in calls if x[:2] == ['run','deploy']))
    assert not any('migrator' in arg for x in calls for arg in x)


def test_deployment_refuses_operator_drain(tmp_path):
    env, log = cloud_fixture(tmp_path)
    state = json.loads(env['SERVICE_JSON'])
    state['metadata']['annotations'].update({'run.googleapis.com/scalingMode':'manual',
                                            'run.googleapis.com/manualInstanceCount':'0'})
    env['SERVICE_JSON'] = json.dumps(state)
    run = subprocess.run(['bash', str(SCRIPTS / 'deploy-production.sh'), IMAGE], cwd=ROOT,
                         env=env, capture_output=True, text=True)
    assert run.returncode != 0
    assert not any(json.loads(x)[:2] in (['run','deploy'], ['run','jobs']) for x in log.read_text().splitlines())


def test_drain_started_during_probe_blocks_deployment(tmp_path):
    env,log=cloud_fixture(tmp_path)
    env['SCHEMA_DRAIN_RACE']='1'
    run=subprocess.run(['bash',str(SCRIPTS/'deploy-production.sh'),IMAGE],cwd=ROOT,env=env,capture_output=True,text=True)
    assert run.returncode!=0 and 'drain is active' in run.stderr
    assert not any(json.loads(x)[:2]==['run','deploy'] for x in log.read_text().splitlines())


def test_migration_needs_separate_human_approval_before_cloud(tmp_path):
    env, log = cloud_fixture(tmp_path)
    env.update(CONFIRM_PRODUCTION_WRITERS_DRAINED='yes', BACKUP_MANIFEST_SHA256='a'*64)
    env.pop('CONFIRM_PRODUCTION_MIGRATION', None)
    run = subprocess.run(['bash', str(SCRIPTS / 'migrate-production.sh'), IMAGE], cwd=ROOT,
                         env=env, capture_output=True, text=True)
    assert run.returncode != 0
    assert not log.exists()


def test_migration_runtime_cli_help_never_connects():
    run = subprocess.run([sys.executable, '-m', 'ai_stock_sentinel.operations.schema', '--help'],
                         env={**os.environ, 'PYTHONPATH': str(ROOT / 'backend/src')},
                         capture_output=True, text=True)
    assert run.returncode == 0
    assert '--migrate' in run.stdout and '--from-revision' in run.stdout


def test_schema_ahead_of_image_is_incompatible(monkeypatch):
    script=ScriptDirectory.from_config(Config(str(ROOT/'backend/alembic.ini')))
    monkeypatch.setattr(script,'get_heads',lambda:[BASE])
    assert schema_module().classify(script,[HEAD])['status']=='incompatible'


@pytest.mark.parametrize('suffix', [
    '?host=/cloudsql/financial-research-20261019:asia-east1:other',
    '?host=/cloudsql/financial-research-20261019:asia-east1:financial-postgres&hostaddr=127.0.0.1',
    '?host=/cloudsql/financial-research-20261019:asia-east1:financial-postgres&port=5433',
    '?host=/cloudsql/financial-research-20261019:asia-east1:financial-postgres&service=other',
    '?host=/cloudsql/financial-research-20261019:asia-east1:financial-postgres&host=/tmp/other',
])
def test_migrator_rejects_dsn_endpoint_overrides(suffix):
    with pytest.raises(ValueError):
        schema_module().migration_url('postgresql://financial_prod_migrator:fixture@/financial_research'+suffix)


def test_migrator_rejects_remote_host_even_with_matching_database():
    with pytest.raises(ValueError):
        schema_module().migration_url('postgresql://financial_prod_migrator:fixture@other-instance/financial_research')


def test_migrator_pins_valid_socket_and_port():
    module=schema_module()
    url=module.migration_url('postgresql://financial_prod_migrator:fixture@/financial_research?host='+module.SOCKET)
    assert url.host is None and url.query=={'host':module.SOCKET,'port':'5432'}


@pytest.mark.parametrize('error_class',[RuntimeError,ValueError])
def test_probe_driver_error_does_not_print_credentials(monkeypatch, capsys,error_class):
    module=schema_module()
    monkeypatch.setenv('PGDATABASE',module.DATABASE)
    monkeypatch.setenv('PGUSER',module.READER)
    monkeypatch.setenv('PGHOST','/cloudsql/financial-research-20261019:asia-east1:financial-postgres')
    monkeypatch.setenv('PGPASSWORD','must-not-leak')
    def fail(*args,**kwargs): raise error_class('postgresql://must-not-leak@host')
    monkeypatch.setattr(module,'create_engine',fail)
    assert module.main([])==1
    output=capsys.readouterr().out
    assert json.loads(output)['status']=='error' and 'must-not-leak' not in output


def test_expired_migration_job_does_not_connect(monkeypatch,capsys):
    module=schema_module()
    monkeypatch.setenv('CONFIRM_PRODUCTION_MIGRATION','yes')
    monkeypatch.setenv('MIGRATION_APPROVAL_EXPIRES_AT','2000-01-01T00:00:00+00:00')
    def connect(*args,**kwargs): pytest.fail('expired approval connected to database')
    monkeypatch.setattr(module,'create_engine',connect)
    assert module.main(['--migrate','--from-revision',BASE,'--to-revision',HEAD])==1
    assert 'expired' in json.loads(capsys.readouterr().out)['message']


def test_safe_eligibility_failure_is_actionable_without_driver_details(monkeypatch,capsys):
    module=schema_module()
    monkeypatch.setenv('PGDATABASE',module.DATABASE)
    monkeypatch.setenv('PGUSER',module.READER)
    monkeypatch.setenv('PGHOST',module.SOCKET)
    monkeypatch.setenv('PGPASSWORD','fixture')
    def reject(*args,**kwargs): raise module.preimage.PreimageError('Live table fails no-RLS contract')
    monkeypatch.setattr(module,'create_engine',reject)
    assert module.main([])==1
    assert 'no-RLS' in json.loads(capsys.readouterr().out)['message']


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def backup_fixture(tmp_path):
    backup = tmp_path / 'backup'
    backup.mkdir()
    digest = hashlib.md5(hashlib.md5(json.dumps({'version_num': BASE}, separators=(',', ':')).encode()).hexdigest().encode()).hexdigest()
    row = {'table':'public.alembic_version', 'rows':1, 'content_md5':digest,
           **{k:[] for k in ('columns','constraints','indexes','sequences')}}
    (backup / 'manifest.jsonl').write_text(json.dumps(row)+'\n')
    (backup / 'tables.txt').write_text('public.alembic_version\n')
    (backup / 'database.sql.gz').write_bytes(b'fixture backup archive')
    (backup / 'sequence-state.sql').write_text('')
    (backup / 'started-at.txt').write_text(datetime.now(timezone.utc).isoformat())
    checksum = load_script('verify-migration-backup').digest
    (backup / 'SHA256SUMS').write_text(''.join(checksum(backup/n)+'  '+n+'\n' for n in
        ('database.sql.gz','manifest.jsonl','tables.txt','sequence-state.sql','started-at.txt')))
    restored = tmp_path / 'restored.jsonl'
    restored.write_bytes((backup/'manifest.jsonl').read_bytes())
    return backup, restored, checksum(backup/'manifest.jsonl'), checksum(backup/'SHA256SUMS')


def test_owner_migration_cli_binds_backup_and_approved_versions(tmp_path):
    from test_gcp_production import service
    module=load_script('verify-production-drain')
    backup,restored,manifest_sha,bundle_sha=backup_fixture(tmp_path)
    env,log=cloud_fixture(tmp_path)
    fake=tmp_path/'gcloud'
    fake.write_text(fake.read_text().replace('financial-prod-deploy@financial-research-20261019.iam.gserviceaccount.com','tonyliao1994@gmail.com'))
    current=service()
    current['metadata']['annotations'].update({'run.googleapis.com/scalingMode':'manual','run.googleapis.com/manualInstanceCount':'0'})
    current['spec']['template']['spec']['containers'][0]['env'].append({'name':'APP_MAINTENANCE_MODE','value':'true'})
    gh=tmp_path/'gh'
    gh.write_text(f'#!{sys.executable}\nimport json\nprint('+repr(json.dumps({'workflows':[{'path':'.github/workflows/'+name,'state':'disabled_manually'} for name in module.WRITERS]}))+')\n')
    gh.chmod(0o700)
    env.update(SERVICE_JSON=json.dumps(current),CONFIRM_PRODUCTION_WRITERS_DRAINED='yes',
        CONFIRM_PRODUCTION_MIGRATION='yes',CONFIRM_BACKUP_RESTORE_VERIFIED='yes',
        BACKUP_DIRECTORY=str(backup),BACKUP_RESTORED_MANIFEST=str(restored),
        BACKUP_MANIFEST_SHA256=manifest_sha,BACKUP_BUNDLE_SHA256=bundle_sha,
        PRODUCTION_MIGRATION_FROM_REVISION=BASE,PRODUCTION_MIGRATION_TO_REVISION=HEAD)
    run=subprocess.run(['bash',str(SCRIPTS/'migrate-production.sh'),IMAGE],cwd=ROOT,env=env,capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    job=next(x for x in calls if x[:3]==['run','jobs','deploy'])
    assert job[3]=='financial-research-prod-migrate' and '--image='+IMAGE in job
    assert '--set-secrets=DATABASE_URL=financial-prod-migrator-database-url:1' in job
    assert '--args=-m,ai_stock_sentinel.operations.schema,--migrate,--from-revision,'+BASE+',--to-revision,'+HEAD in job
    assert manifest_sha in next(x for x in job if x.startswith('--set-env-vars='))
    assert not any(x[:2]==['run','deploy'] for x in calls)


@pytest.mark.parametrize('defect', ['none','dump','manifest','restored','starting_revision','stale','timestamp','legacy_timestamp','missing_checksum'])
def test_backup_gate_checks_real_files_restored_content_and_preimage(tmp_path, defect):
    module = load_script('verify-migration-backup')
    backup, restored, manifest_sha, bundle_sha = backup_fixture(tmp_path)
    if defect == 'dump': (backup/'database.sql.gz').write_bytes(b'changed')
    if defect == 'manifest': (backup/'manifest.jsonl').write_text('{}\n')
    if defect == 'restored': restored.write_text(restored.read_text().replace('"rows": 1','"rows": 2'))
    if defect in ('stale','timestamp'):
        (backup/'started-at.txt').write_text('2000-01-01T00:00:00Z')
        if defect=='stale':
            lines=(backup/'SHA256SUMS').read_text().splitlines()
            (backup/'SHA256SUMS').write_text('\n'.join((module.digest(backup/'started-at.txt')+'  started-at.txt') if line.endswith('  started-at.txt') else line for line in lines)+'\n')
            bundle_sha=module.digest(backup/'SHA256SUMS')
    if defect == 'missing_checksum':
        (backup/'SHA256SUMS').write_text('')
        bundle_sha=module.digest(backup/'SHA256SUMS')
    if defect == 'legacy_timestamp':
        (backup/'SHA256SUMS').write_text(''.join(line+'\n' for line in (backup/'SHA256SUMS').read_text().splitlines()
                                              if not line.endswith('  started-at.txt')))
        bundle_sha=module.digest(backup/'SHA256SUMS')
    start = 'ab2c3d4e5f6a' if defect=='starting_revision' else BASE
    if defect == 'none':
        assert module.verify(backup,restored,manifest_sha,bundle_sha,start)['from_revision']==BASE
    else:
        with pytest.raises(ValueError): module.verify(backup,restored,manifest_sha,bundle_sha,start)


@pytest.mark.parametrize('defect', ['none','maintenance','instances','tag','workflow','missing_workflow'])
def test_actual_writer_drain_is_required(defect):
    from test_gcp_production import service
    module=load_script('verify-production-drain')
    current=service()
    current['metadata']['annotations'].update({'run.googleapis.com/scalingMode':'manual','run.googleapis.com/manualInstanceCount':'0'})
    current['spec']['template']['spec']['containers'][0]['env'].append({'name':'APP_MAINTENANCE_MODE','value':'true'})
    workflows={'workflows':[{'path':'.github/workflows/'+name,'state':'disabled_manually'} for name in sorted(module.WRITERS)]}
    if defect=='maintenance': current['spec']['template']['spec']['containers'][0]['env'][-1]['value']='false'
    if defect=='instances': current['metadata']['annotations']['run.googleapis.com/manualInstanceCount']='1'
    if defect=='tag': current['status']['traffic'][0]['tag']='old'
    if defect=='workflow': workflows['workflows'][0]['state']='active'
    if defect=='missing_workflow': workflows['workflows'].pop()
    if defect=='none': module.verify(current,workflows)
    else:
        with pytest.raises(ValueError): module.verify(current,workflows)


def baseline_database(pg17, monkeypatch):
    from alembic import command
    env, raw = pg17()
    url=URL.create('postgresql+psycopg2',username=env.get('PGUSER'),password=env.get('PGPASSWORD'),
                   database=env['PGDATABASE'],query={'host':env['PGHOST'],'port':'65439'})
    monkeypatch.setenv('DATABASE_URL',url.render_as_string(hide_password=False))
    monkeypatch.setenv('EXPECTED_DATABASE_NAME',env['PGDATABASE'])
    # This database was just created by the private fixture and contains no user
    # data. A historical destructive migration has its own explicit test gate.
    monkeypatch.setenv('CALIBRATION_MIGRATION_BACKUP_CONFIRMED','2c3d4e5f6a7b')
    config=Config(str(ROOT/'backend/alembic.ini'))
    command.upgrade(config,BASE)
    raw.close()
    return env, create_engine(url), config


@pytest.mark.parametrize('defect', ['none','from','to','connection','lock','partial_failure','incomplete_backup','changed_rows','changed_schema','changed_sequence','changed_rls','changed_force_rls'])
def test_pg17_migration_fences_and_atomic_rollback(pg17, monkeypatch, defect):
    env,engine,config=baseline_database(pg17,monkeypatch)
    module=schema_module()
    script=ScriptDirectory.from_config(config)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE public.preimage_probe(id bigserial PRIMARY KEY,payload text)'))
        conn.execute(text("INSERT INTO public.preimage_probe(payload) VALUES ('backed up')"))
        backup_hash=module.tables_hash(module.current_tables(conn))
        backup_preimage=module.preimage.capture(conn,module.current_tables(conn))
    if defect.startswith('changed_'):
        with engine.begin() as conn:
            if defect=='changed_rows': conn.execute(text("UPDATE public.preimage_probe SET payload='later write'"))
            if defect=='changed_schema': conn.execute(text('ALTER TABLE public.preimage_probe ADD COLUMN later integer'))
            if defect=='changed_sequence': conn.execute(text("SELECT nextval('public.preimage_probe_id_seq')"))
            if defect=='changed_rls': conn.execute(text('ALTER TABLE public.preimage_probe ENABLE ROW LEVEL SECURITY'))
            if defect=='changed_force_rls': conn.execute(text('ALTER TABLE public.preimage_probe FORCE ROW LEVEL SECURITY'))
    extra=None
    if defect in ('connection','lock'):
        extra=engine.connect()
        if defect=='lock': extra.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':module.MIGRATION_LOCK})
        else: extra.execute(text('SELECT 1'))
    if defect=='partial_failure':
        original=module.command.upgrade
        def fail_after_ddl(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('injected failure after DDL')
        monkeypatch.setattr(module.command,'upgrade',fail_after_ddl)
    try:
        with engine.connect() as conn:
            def execute():
                return module.migrate(conn,config,script,from_revision=HEAD if defect=='from' else BASE,
                    to_revision=BASE if defect=='to' else HEAD,backup_tables_sha256='0'*64 if defect=='incomplete_backup' else backup_hash,
                    backup_preimage_sha256=backup_preimage,
                    database=env['PGDATABASE'],user=env['PGUSER'])
            if defect=='none':
                result=execute()
                assert result['status']=='ready' and result['applied_revisions']==['cd4e5f6a7b8c',HEAD]
            else:
                with pytest.raises((ValueError,RuntimeError)): execute()
        with engine.connect() as check:
            assert check.scalar(text('SELECT version_num FROM alembic_version'))==(HEAD if defect=='none' else BASE)
            assert bool(check.scalar(text("SELECT to_regclass('public.radar_source_snapshots')")))==(defect=='none')
    finally:
        if extra is not None: extra.close()
        engine.dispose()


def test_pg17_readonly_probe_reports_missing_migrations_without_writes(pg17, monkeypatch):
    import uuid
    env,owner,config=baseline_database(pg17,monkeypatch)
    reader='schema_reader_'+uuid.uuid4().hex[:10]
    reader_password='schema-reader-fixture-only-password'
    pg17.roles.append(reader)
    with owner.begin() as conn:
        conn.execute(text(f'CREATE ROLE {reader} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :password'),
                     {'password': reader_password})
        conn.execute(text(f'GRANT CONNECT ON DATABASE {env["PGDATABASE"]} TO {reader}'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA public TO {reader}'))
        conn.execute(text(f'GRANT SELECT ON alembic_version TO {reader}'))
    owner.dispose()
    url=URL.create('postgresql+psycopg2',username=reader,password=reader_password,database=env['PGDATABASE'],
                   query={'host':env['PGHOST'],'port':'65439'})
    # The Docker CI fixture requires password auth; local Unix sockets may use trust.
    if env['PGHOST']=='127.0.0.1':
        invalid_engine=create_engine(url.set(password=reader_password+'-wrong'))
        try:
            with pytest.raises(OperationalError, match='password authentication failed'):
                with invalid_engine.connect():
                    pass
        finally: invalid_engine.dispose()
    engine=create_engine(url)
    try:
        with engine.connect() as conn:
            result=schema_module().probe(conn,ScriptDirectory.from_config(config),database=env['PGDATABASE'],user=reader)
            assert result['status']=='migration_required' and result['current_revisions']==[BASE]
            assert conn.scalar(text('SELECT version_num FROM alembic_version'))==BASE
            assert conn.scalar(text("SELECT to_regclass('public.radar_source_snapshots')")) is None
            with pytest.raises(ProgrammingError) as denied:
                conn.execute(text('CREATE TABLE forbidden_probe_write(id int)'))
            assert denied.value.orig.pgcode=='42501'  # insufficient_privilege
    finally: engine.dispose()


def test_pg17_export_restore_manifest_matches_runtime_preimage(pg17,monkeypatch,tmp_path):
    import shutil
    from test_gcp_migration import restore, manifests
    env,engine,config=baseline_database(pg17,monkeypatch)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE preimage_probe(id bigserial PRIMARY KEY,payload text)'))
        conn.execute(text("INSERT INTO preimage_probe(payload) VALUES ('backed up')"))
        names=schema_module().current_tables(conn)
        before=schema_module().preimage.capture(conn,names)
    contract=tmp_path/'exporter';contract.mkdir()
    for name in ('snapshot.sh','table-manifest.sql'):shutil.copyfile(SCRIPTS/name,contract/name)
    (contract/'financial-tables.txt').write_text('\n'.join(sorted(names))+'\n')
    backup=tmp_path/'source'
    exported=subprocess.run(['bash',str(contract/'snapshot.sh'),'export-financial',str(backup)],env=env,capture_output=True,text=True)
    assert exported.returncode==0,exported.stderr
    target_env,target=pg17()
    restore(target_env,backup)
    restored=tmp_path/'restored'
    checked=subprocess.run(['bash',str(contract/'snapshot.sh'),'verify-financial',str(restored)],env=target_env,capture_output=True,text=True)
    assert checked.returncode==0,checked.stderr
    module=load_script('verify-migration-backup')
    result=module.verify(backup,restored/'manifest.jsonl',module.digest(backup/'manifest.jsonl'),module.digest(backup/'SHA256SUMS'),BASE)
    assert result['backup_preimage_sha256']==before
    with engine.begin() as conn:
        assert schema_module().preimage.capture(conn,names)==before
    target.close();engine.dispose()
