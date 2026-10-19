"""Read-only verification of the sequence state captured inside a financial dump."""
import argparse
from pathlib import Path
import re

import psycopg2
from psycopg2 import sql

SETVAL = re.compile(r"SELECT pg_catalog\.setval\('((?:public|maintenance_backup)\.[a-z0-9_]+)', (-?\d+), (true|false)\);")


def parse_state(path: Path) -> list[tuple[str, int, bool]]:
    result = []
    for line in path.read_text().splitlines():
        match = SETVAL.fullmatch(line)
        if not match:
            raise ValueError("Unrecognized sequence state; inspect the dump before restoring")
        result.append((match[1], int(match[2]), match[3] == "true"))
    if not result or len({item[0] for item in result}) != len(result):
        raise ValueError("Missing or duplicate sequence state")
    return result


def verify(connection, states):
    connection.set_session(readonly=True)
    with connection.cursor() as cursor:
        for name, value, called in states:
            schema, sequence = name.split(".")
            cursor.execute(sql.SQL("SELECT last_value, is_called FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(sequence)))
            if cursor.fetchone() != (value, called):
                raise ValueError(f"{name}: state differs from dump; target must remain idle")
            cursor.execute("""SELECT n.nspname, t.relname, a.attname
                FROM pg_depend d JOIN pg_class t ON t.oid=d.refobjid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
                WHERE d.classid='pg_class'::regclass AND d.objid=%s::regclass
                  AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')""", (name,))
            owner = cursor.fetchone()
            if owner is None:
                raise ValueError(f"{name}: missing sequence ownership")
            cursor.execute(sql.SQL("SELECT max({}) FROM {}.{}").format(
                sql.Identifier(owner[2]), sql.Identifier(owner[0]), sql.Identifier(owner[1])))
            maximum = cursor.fetchone()[0]
            if maximum is not None and (value < maximum or (value == maximum and not called)):
                raise ValueError(f"{name}: next value could collide with restored rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    args = parser.parse_args()
    # libpq uses PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSFILE; no URL on argv.
    with psycopg2.connect(connect_timeout=10) as connection:
        verify(connection, parse_state(args.state))
    print("Sequence states and restored row bounds match.")
