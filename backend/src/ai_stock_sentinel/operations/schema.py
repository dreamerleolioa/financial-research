"""Read-only deployment schema probe and separately authorized migration job."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from ai_stock_sentinel.operations import preimage

DATABASE = 'financial_research'
READER = 'financial_prod_debug_readonly'
MIGRATOR = 'financial_prod_migrator'
MIGRATION_LOCK = 7420921875317
SOCKET = '/cloudsql/financial-research-20261019:asia-east1:financial-postgres'


class GateError(ValueError):
    """Operator-safe diagnostic; raw driver errors must never be printed."""


def require(condition, message):
    if not condition:
        raise GateError(message)


def classify(script, current):
    heads = sorted(script.get_heads())
    require(len(heads) == 1, 'Image must contain exactly one Alembic head')
    report = {'version': 'production-schema-v1', 'current_revisions': sorted(current),
              'expected_revisions': heads, 'pending_revisions': []}
    if current == heads:
        report['status'] = 'ready'
    elif not current:
        report['status'] = 'uninitialized'
    elif len(current) != 1:
        report['status'] = 'incompatible'
    else:
        try:
            pending = list(script.iterate_revisions(heads[0], current[0]))
            report.update(status='migration_required', pending_revisions=[r.revision for r in reversed(pending)])
        except Exception as error:
            # Alembic uses different exception classes for unknown/divergent revisions.
            from alembic.util.exc import CommandError
            from alembic.script.revision import RevisionError
            if not isinstance(error, (CommandError, RevisionError)):
                raise
            report['status'] = 'incompatible'
    return report


def inspect_schema(connection, script):
    exists = connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
    current = list(connection.scalars(text('SELECT version_num FROM public.alembic_version ORDER BY version_num'))) if exists else []
    return classify(script, current)


def tables_hash(names):
    return hashlib.sha256(json.dumps(sorted(names), separators=(',', ':')).encode()).hexdigest()


def current_tables(connection):
    return list(connection.scalars(text("SELECT schemaname || '.' || tablename FROM pg_tables WHERE schemaname IN ('public','maintenance_backup')")))


def migration_url(value):
    url = make_url(value)
    require(url.drivername in ('postgresql', 'postgresql+psycopg2') and url.database == DATABASE
            and url.username == MIGRATOR and bool(url.password), 'Wrong migration DSN database or role')
    require(url.host is None and url.port in (None, 5432) and set(url.query) <= {'host', 'port'}
            and url.query.get('host') == SOCKET and url.query.get('port', '5432') == '5432',
            'Migration DSN must use the exact production Cloud SQL socket and port without overrides')
    return URL.create('postgresql+psycopg2', username=MIGRATOR, password=url.password,
                      database=DATABASE, query={'host': SOCKET, 'port': '5432'})


def probe(connection, script, *, database=DATABASE, user=READER):
    with connection.begin():
        connection.execute(text('SET TRANSACTION READ ONLY'))
        actual = connection.execute(text('SELECT current_database(), current_user')).one()
        require(tuple(actual) == (database, user), 'Wrong schema probe database or role')
        require(connection.scalar(text('SHOW transaction_read_only')) == 'on', 'Schema probe must be read only')
        return inspect_schema(connection, script)


def migrate(connection, config, script, *, from_revision, to_revision, backup_tables_sha256, backup_preimage_sha256,
            database=DATABASE, user=MIGRATOR):
    """Fence and upgrade on one transaction/connection, including Alembic's DDL."""
    with connection.begin():
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SET LOCAL statement_timeout = '120s'"))
        actual = connection.execute(text('SELECT current_database(), current_user')).one()
        require(tuple(actual) == (database, user), 'Wrong migration database or role')
        require(connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': MIGRATION_LOCK}),
                'Another production migration is running')
        report = inspect_schema(connection, script)
        require(report['current_revisions'] == [from_revision], 'Schema changed since the approved backup; create a new plan')
        require(report['expected_revisions'] == [to_revision], 'Image head differs from the approved migration target')
        require(report['status'] == 'migration_required', 'Only a known forward migration is allowed')
        require(tables_hash(current_tables(connection)) == backup_tables_sha256,
                'Backup does not cover the current database table set')
        others = connection.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()'))
        require(others == 0, 'Database writers/connections have not drained')
        require(preimage.capture(connection, current_tables(connection)) == backup_preimage_sha256,
                'Live data/schema/sequences differ from the approved backup; create and restore a fresh drained backup')
        config.attributes['connection'] = connection
        try:
            command.upgrade(config, to_revision)
        finally:
            config.attributes.pop('connection', None)
        after = inspect_schema(connection, script)
        require(after['status'] == 'ready', 'Migration did not reach the image head')
    return {**after, 'applied_revisions': report['pending_revisions']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migrate', action='store_true', help='Owner job only; never used by deployment CI')
    parser.add_argument('--from-revision')
    parser.add_argument('--to-revision')
    args = parser.parse_args(argv)
    try:
        config = Config('alembic.ini')
        script = ScriptDirectory.from_config(config)
        require(not any(os.environ.get(name) for name in
                        ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGSYSCONFDIR', 'PGOPTIONS')),
                'libpq environment overrides are not allowed')
        if args.migrate:
            require(os.getenv('CONFIRM_PRODUCTION_MIGRATION') == 'yes', 'Explicit production migration approval required')
            require(args.from_revision and args.to_revision, 'Pass approved from/to revisions')
            expires = datetime.fromisoformat(os.environ.get('MIGRATION_APPROVAL_EXPIRES_AT', ''))
            now = datetime.now(timezone.utc)
            require(expires.tzinfo is not None and now < expires <= now + timedelta(hours=6),
                    'Migration backup/approval expired; create a new plan')
            require(re.fullmatch('[a-f0-9]{64}', os.environ.get('MIGRATION_BACKUP_MANIFEST_SHA256', '')),
                    'Verified backup manifest checksum required')
            from ai_stock_sentinel.db.session import get_database_url
            require(os.getenv('EXPECTED_DATABASE_NAME') == DATABASE, 'Expected production database is required')
            url = migration_url(get_database_url())
            options = '-c lock_timeout=5000 -c statement_timeout=120000'
        else:
            require(os.environ.get('PGDATABASE') == DATABASE and os.environ.get('PGUSER') == READER,
                    'Expected read-only production connection is required')
            require(os.environ.get('PGHOST') == SOCKET,
                    'Wrong Cloud SQL instance')
            url = URL.create('postgresql+psycopg2', username=READER, password=os.environ['PGPASSWORD'],
                             database=DATABASE, query={'host': os.environ['PGHOST'], 'port': '5432'})
            options = '-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=5000'
        engine = create_engine(url, poolclass=NullPool, connect_args={'options': options, 'connect_timeout': 15})
        try:
            with engine.connect() as connection:
                result = (migrate(connection, config, script, from_revision=args.from_revision, to_revision=args.to_revision,
                                  backup_tables_sha256=os.environ.get('MIGRATION_BACKUP_TABLES_SHA256'),
                                  backup_preimage_sha256=os.environ.get('MIGRATION_BACKUP_PREIMAGE_SHA256'))
                          if args.migrate else probe(connection, script))
        finally:
            engine.dispose()
        if args.migrate:
            result['backup_manifest_sha256'] = os.environ['MIGRATION_BACKUP_MANIFEST_SHA256']
        print(json.dumps(result))
        return 0 if result['status'] == 'ready' else 2
    except Exception as error:
        # Never emit a DSN, password or raw driver/subprocess exception.
        print(json.dumps({'status': 'error', 'error_type': type(error).__name__,
                          'message': str(error) if isinstance(error, (GateError, preimage.PreimageError)) else 'Schema operation failed; inspect job configuration and database connectivity'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
