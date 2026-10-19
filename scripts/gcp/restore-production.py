"""Plan/verify by default. Restore only allowlisted financial tables in one transaction."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

import psycopg2
from psycopg2 import sql

HERE=Path(__file__).resolve().parent
PROJECT='financial-research-20261019'
INSTANCE=PROJECT+':asia-east1:financial-postgres'
DATABASE='financial_research'
OWNER='financial_prod_migrator'
TABLES=tuple((HERE/'financial-tables.txt').read_text().splitlines())


def sibling(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


manifests=sibling('compare-manifests')
sequences=sibling('verify-sequences')


def expected_head():
    from alembic.script import ScriptDirectory
    return ScriptDirectory(str(HERE.parents[1]/'backend/alembic')).get_current_head()


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def load_bundle(path, expected_manifest_sha256, expected_bundle_sha256):
    root=path.resolve(strict=True)
    allowed={'database.sql.gz','manifest.jsonl','tables.txt','sequence-state.sql'}
    for name in allowed|{'SHA256SUMS'}:
        p=root/name
        if p.is_symlink() or not p.is_file():raise ValueError('Missing or symlinked snapshot component')
    if digest(root/'SHA256SUMS')!=expected_bundle_sha256:
        raise ValueError('Unapproved bundle checksum list; obtain its SHA256 from the trusted exporter')
    checks={}
    for line in (root/'SHA256SUMS').read_text().splitlines():
        match=re.fullmatch(r'([a-f0-9]{64})  ([a-zA-Z0-9_.-]+)',line)
        if not match or match[2] in checks:raise ValueError('Invalid snapshot checksum list')
        checks[match[2]]=match[1]
    if set(checks)!=allowed:raise ValueError('Unexpected snapshot components')
    if checks['manifest.jsonl']!=expected_manifest_sha256:raise ValueError('Unapproved manifest SHA256')
    if any(digest(root/name)!=value for name,value in checks.items()):raise ValueError('Snapshot checksum mismatch')
    if tuple((root/'tables.txt').read_text().splitlines())!=TABLES:raise ValueError('Wrong financial table allowlist')
    expected=manifests.read_manifest(root/'manifest.jsonl')
    if set(expected)!=set(TABLES):raise ValueError('Wrong manifest table set')
    states=sequences.parse_state(root/'sequence-state.sql')
    owned={row[0] for item in expected.values() for row in item['sequences']}
    if {item[0] for item in states}!=owned:raise ValueError('Sequence inventory differs from manifest')
    return {'root':root,'manifest':expected,'states':states,'manifest_sha256':expected_manifest_sha256,
            'bundle_sha256':expected_bundle_sha256}


class Session:
    def __init__(self, env):
        self.errors=tempfile.TemporaryFile(mode='w+t')
        self.process=subprocess.Popen(['psql','-XqAt','-w','-v','ON_ERROR_STOP=1'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.errors,text=True,
            env={**env,'PGCONNECT_TIMEOUT':'15','PGAPPNAME':'financial-gcp-cutover',
                 'PGOPTIONS':'-c statement_timeout=120000 -c lock_timeout=5000', 'PSQL_PAGER':''})
    def write(self, statement):
        self.process.stdin.write(statement+'\n');self.process.stdin.flush()
    def query(self, statement):
        marker='CUTOVER_'+uuid.uuid4().hex
        self.write(statement+'\n\\echo '+marker)
        lines=[]
        while True:
            line=self.process.stdout.readline()
            if not line:raise RuntimeError('psql stopped before acknowledgement; inspect journal, do not retry')
            if line.strip()==marker:return [line for line in lines if line.strip()]
            lines.append(line)
    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.errors.close()


def quoted(name):
    if not re.fullmatch(r'(public|maintenance_backup)\.[a-z0-9_]+',name):raise ValueError('Unapproved SQL identifier')
    return '.'.join('"'+part+'"' for part in name.split('.'))


def read_manifest(session):
    result={}
    source=(HERE/'table-manifest.sql').read_text()
    for name in TABLES:
        lines=session.query("\\set relation "+name+"\n\\set schema_only false\n\\set full_schema false\n"+source)
        if len(lines)!=1:raise ValueError('Unexpected manifest response')
        result[name]=json.loads(lines[0])
    return result


def check_sequences(session, bundle):
    owners={row[0]:(name,row[1]) for name,item in bundle['manifest'].items() for row in item['sequences']}
    for name,value,called in bundle['states']:
        table,column=owners[name]
        # Column identifiers come from the validated manifest; quote, never interpolate raw SQL.
        column='"'+column.replace('"','""')+'"'
        lines=session.query(f'SELECT json_build_array(last_value,is_called,(SELECT max({column}) FROM {quoted(table)})) FROM {quoted(name)};')
        actual,flag,maximum=json.loads(lines[0])
        if (actual,flag)!=(value,called) or (maximum is not None and (value<maximum or (value==maximum and not called))):
            raise ValueError('Sequence state/bounds differ: '+name)


def match(session,bundle):
    differences=manifests.compare(bundle['manifest'],read_manifest(session))
    if differences:raise ValueError('; '.join(differences))
    check_sequences(session,bundle)


def require_apply_approval():
    for name in ('CONFIRM_FINAL_CUTOVER_APPROVED','CONFIRM_SOURCE_WRITERS_DRAINED',
                 'CONFIRM_TARGET_WRITERS_DRAINED','CONFIRM_SCHEDULES_PAUSED'):
        if os.getenv(name)!='yes':raise ValueError('Explicit approval required: '+name)
    if any(os.getenv(key) for key in ('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGSYSCONFDIR','PGOPTIONS')):
        raise ValueError('Connection environment overrides are forbidden')
    host=os.getenv('PGHOST')
    if host!='/cloudsql/'+INSTANCE and not (host=='127.0.0.1' and os.getenv('CONFIRM_CLOUD_SQL_PROXY')==INSTANCE):
        raise ValueError('Use the exact Cloud SQL socket or confirmed encrypted loopback proxy')
    if os.getenv('PGDATABASE')!=DATABASE or os.getenv('PGUSER')!=OWNER:
        raise ValueError('Wrong production database or migration identity')


def identity(session,database,owner):
    values=json.loads(session.query("SELECT json_build_array(current_database(),current_user,current_setting('server_version_num')::integer);")[0])
    if values[:2]!=[database,owner] or not 170000<=values[2]<180000:raise ValueError('Wrong database, owner, or PG major version')


def restore(env, final, backup, journal, *, database=DATABASE, owner=OWNER, privileges=True):
    os.umask(0o077)
    with journal.open('x') as f:json.dump({'status':'preflight'},f)
    state={'status':'preflight','source_manifest_sha256':final['manifest_sha256'],
           'rollback_manifest_sha256':backup['manifest_sha256'],
           'source_bundle_sha256':final['bundle_sha256'],'rollback_bundle_sha256':backup['bundle_sha256']}
    def save():journal.write_text(json.dumps(state,indent=2)+'\n')
    session=Session(env)
    try:
        identity(session,database,owner)
        session.query('BEGIN; SELECT pg_advisory_xact_lock(918645310958);')
        session.query('LOCK TABLE '+','.join(quoted(n) for n in TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT;')
        inventory=json.loads(session.query("SELECT coalesce(json_agg(n.nspname||'.'||c.relname ORDER BY n.nspname,c.relname),'[]'::json) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','maintenance_backup') AND c.relkind IN ('r','p','v','m','f');")[0])
        if set(inventory)!=set(TABLES):raise ValueError('Unexpected target objects; no tables dropped')
        connections=int(session.query('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid();')[0])
        if connections:raise ValueError('Target connections remain; close SQL Studio and API pools')
        # Prove that a verified rollback bundle represents the current target,
        # including sequence states, before performing any destructive statement.
        match(session,backup)
        state['status']='restoring_transaction';save()
        session.write('DROP TABLE '+','.join(quoted(n) for n in TABLES)+';\nDROP SCHEMA maintenance_backup;')
        with gzip.open(final['root']/'database.sql.gz','rt') as dump:
            while chunk:=dump.read(65536):session.process.stdin.write(chunk)
        session.process.stdin.flush();session.query('SELECT 1;')
        if privileges:
            text=(HERE/'configure-production-privileges.sql').read_text()
            text='\n'.join(line for line in text.splitlines() if line not in ('BEGIN;','COMMIT;'))
            session.query(text)
        match(session,final)
        head=session.query('SELECT version_num FROM public.alembic_version;')
        if [x.strip() for x in head]!=[expected_head()]:raise ValueError('Restored Alembic head differs from the reviewed application')
        # A disconnect after this point is ambiguous: the journal deliberately
        # retains commit_requested and forbids automatic re-execution.
        state['status']='commit_requested';save()
        session.query('COMMIT;')
        state['status']='committed_verified';save()
    except BaseException as error:
        state.update(status='stopped_no_automatic_retry',last_phase=state['status'],error_type=type(error).__name__);save()
        raise
    finally:session.close()


def verify_rollback(env,bundle, *, database=DATABASE, owner=OWNER):
    session=Session(env)
    try:
        identity(session,database,owner)
        session.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;')
        match(session,bundle)
        session.query('ROLLBACK;')
    finally:session.close()
    print('GCP still matches final source snapshot. Freeze both writers before reverting URLs; source must also remain unchanged.')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('plan','restore','verify-rollback'))
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--bundle-sha256',required=True,help='Trusted exporter SHA256 of the SHA256SUMS file')
    p.add_argument('--rollback-snapshot',type=Path)
    p.add_argument('--rollback-manifest-sha256')
    p.add_argument('--rollback-bundle-sha256')
    p.add_argument('--journal',type=Path)
    args=p.parse_args(argv)
    final=load_bundle(args.snapshot,args.manifest_sha256,args.bundle_sha256)
    if args.mode=='plan':
        print(json.dumps({'mode':'plan_only','database':DATABASE,'tables':len(TABLES),
                          'source_manifest_sha256':args.manifest_sha256,'alembic_head':expected_head()}));return
    require_apply_approval()
    if args.mode=='verify-rollback':verify_rollback(os.environ,final);return
    if not (args.rollback_snapshot and args.rollback_manifest_sha256 and args.rollback_bundle_sha256 and args.journal):
        raise ValueError('A verified rollback snapshot and new private journal are required')
    backup=load_bundle(args.rollback_snapshot,args.rollback_manifest_sha256,args.rollback_bundle_sha256)
    restore(os.environ,final,backup,args.journal)
    print('FINAL_FINANCIAL_RESTORE_COMMITTED_VERIFIED. Traffic and schedules remain operator controlled.')


if __name__=='__main__':main()
