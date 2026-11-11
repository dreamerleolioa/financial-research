"""Backup/live preimage contract. Hashing/parsing is usable without DB dependencies."""
import hashlib
import json
from pathlib import Path
import re

RELATION = re.compile(r'(public|maintenance_backup)\.[a-z0-9_]+')
SETVAL = re.compile(r"SELECT pg_catalog\.setval\('((?:public|maintenance_backup)\.[a-z0-9_]+)', (-?\d+), (true|false)\);")


class PreimageError(ValueError):
    """Static operator-safe diagnostics, distinct from raw driver exceptions."""


def sequence_states(source):
    result = []
    for line in source.splitlines():
        match = SETVAL.fullmatch(line)
        if not match:
            raise ValueError('Unrecognized backup sequence state')
        result.append((match[1], int(match[2]), match[3] == 'true'))
    if len({row[0] for row in result}) != len(result):
        raise ValueError('Duplicate sequence state')
    return result


def fingerprint(manifest, states):
    value = {'manifest': manifest, 'sequences': sorted(states)}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def capture(connection, names):
    """Use the exporter's exact SQL under the caller's transaction/write fence."""
    from sqlalchemy import text
    names = sorted(names)
    if not names or not all(RELATION.fullmatch(name) for name in names):
        raise PreimageError('Unexpected migration preimage table name')
    # Fence DML while hashing and upgrading, including new connections arriving
    # after the initial drain check. One sorted lock acquisition avoids cycles.
    connection.exec_driver_sql('LOCK TABLE ' + ','.join(names) + ' IN SHARE ROW EXCLUSIVE MODE')
    for name in names:
        flags = connection.execute(text('SELECT relkind,relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)'),
                                   {'name': name}).one()
        if tuple(flags) != ('r', False, False):
            raise PreimageError('Live table no longer meets the exporter regular-table/no-RLS contract')
    settings = ('timezone', 'datestyle', 'intervalstyle', 'extra_float_digits', 'search_path')
    original = {key: connection.scalar(text('SHOW ' + key)) for key in settings}
    raw = Path(__file__).with_name('table-manifest.sql').read_text()
    start = raw.index('SELECT format(')
    for statement in raw[:start].splitlines():
        if statement.startswith('SET '):
            connection.execute(text(statement.replace('SET ', 'SET LOCAL ', 1)))
    query = raw[start:].split('\\gexec')[0]
    query = query.replace(":'relation'::regclass", 'CAST(:relation AS regclass)')
    query = query.replace(":'relation'", ':relation')
    query = query.replace(":'schema_only'::boolean", 'CAST(:schema_only AS boolean)')
    query = query.replace(":'schema_only'", ':schema_only').replace(":'full_schema'", ':full_schema')
    manifest = {}
    for name in names:
        sql = connection.scalar(text(query), {'relation': name, 'schema_only': False, 'full_schema': False})
        manifest[name] = connection.scalar(text(sql))
    states = []
    for name in sorted({row[0] for item in manifest.values() for row in item['sequences']}):
        if not RELATION.fullmatch(name):
            raise PreimageError('Unexpected migration preimage sequence name')
        value, called = connection.exec_driver_sql('SELECT last_value,is_called FROM ' + name).one()
        states.append((name, value, called))
    # On errors the enclosing transaction rolls back all SET LOCAL changes.
    # On success restore caller settings before Alembic executes any DDL/data SQL.
    for key, value in original.items():
        connection.execute(text('SELECT set_config(:key,:value,true)'), {'key': key, 'value': value})
    return fingerprint(manifest, states)
