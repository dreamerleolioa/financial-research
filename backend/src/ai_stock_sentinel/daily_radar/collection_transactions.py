"""Scoped read-only collection; source evidence uses its own transaction owner."""
from contextlib import contextmanager
import re

from sqlalchemy import event
from sqlalchemy.sql.selectable import TextualSelect


class CollectionMutationError(ValueError):
    def __init__(self):
        super().__init__('radar_collection_mutated_domain')


@contextmanager
def read_only_collection(session):
    """Reject writes before execution and retain violations even if swallowed.

    PostgreSQL read-only transactions also reject writes hidden inside SELECT
    functions. Connection listeners cover Core/driver SQL as well as the ORM.
    Listeners belong only to this scope, never to source-store connections.
    """
    violated = False
    connections = []
    listeners = []
    recovery_overrides = []
    absent = object()
    bind = session.get_bind()
    engine = getattr(bind, 'engine', bind)

    def reject(*args, **kwargs):
        nonlocal violated
        violated = True
        raise CollectionMutationError()

    def before_cursor(connection, cursor, statement, parameters, context, executemany):
        if violated:
            reject()
        # A SELECT-prefixed psycopg2 batch can COMMIT the read-only transaction
        # in SQL and then write outside it. Bind values; reject embedded statement
        # separators even in compiled SELECTs. A single trailing separator is OK.
        if ';' in statement.strip().removesuffix(';'):
            reject()
        compiled = getattr(context, 'compiled', None)
        expression = getattr(compiled, 'statement', None)
        # Text/driver SQL has no Select expression. Fail closed for commands,
        # including writable CTEs and SET TRANSACTION READ WRITE.
        if re.match(r'^\s*SELECT\b', statement, re.IGNORECASE):
            return
        if (re.match(r'^\s*WITH\b', statement, re.IGNORECASE)
                and getattr(expression, 'is_select', False) and not isinstance(expression, TextualSelect)):
            return  # The database read-only backstop rejects writable CTEs.
        if connection.dialect.name == 'sqlite' and re.match(
            r'^\s*PRAGMA\s+(?:(?:main|temp)\.)?'
            r'(?:table_info|table_xinfo|index_info|index_xinfo|index_list|foreign_key_list|database_list)\s*(?:\(|$)',
            statement, re.IGNORECASE,
        ):
            return  # SQLAlchemy schema reflection; no state-changing PRAGMAs.
        reject()

    def set_read_only(connection):
        if violated:
            reject()
        if connection.dialect.name == 'postgresql':
            raw = connection.connection.driver_connection
            if raw.autocommit:
                reject()
            # A Connection.begin event precedes SQLAlchemy's transaction object;
            # use its driver cursor to avoid recursively triggering autobegin.
            with raw.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')

    def execution_options(connection, options):
        if {'isolation_level', 'postgresql_readonly', 'postgresql_deferrable', 'autocommit'} & options.keys():
            reject()

    def after_begin(owner, transaction, connection):
        if connection in connections:
            return
        connections.append(connection)
        # Session caches a connection before this event. Install all protection
        # before fallible setup so a swallowed acquisition error cannot escape.
        event.listen(connection, 'before_cursor_execute', before_cursor)
        event.listen(connection, 'commit', reject)
        event.listen(connection, 'begin_twophase', reject)
        event.listen(connection, 'prepare_twophase', reject)
        event.listen(connection, 'commit_twophase', reject)
        event.listen(connection, 'begin', set_read_only)
        event.listen(connection, 'set_connection_execution_options', execution_options)
        # Core recovery APIs call the dialect directly, without ConnectionEvents.
        # Shadow them only on this connection and restore prior instance state.
        originals = {}
        for name in ('commit_prepared', 'rollback_prepared', 'recover_twophase'):
            originals[name] = connection.__dict__.get(name, absent)
            setattr(connection, name, reject)
        recovery_overrides.append((connection, originals))
        listeners.append(connection)
        set_read_only(connection)

    def handle_error(context):
        nonlocal violated
        if context.connection in connections and getattr(context.original_exception, 'pgcode', None) == '25006':
            violated = True

    if session.in_transaction():
        raise CollectionMutationError()
    event.listen(session, 'before_flush', reject)
    event.listen(session, 'before_commit', reject)
    event.listen(session, 'after_begin', after_begin)
    event.listen(engine, 'handle_error', handle_error)
    try:
        yield
        if violated or session.new or session.dirty or session.deleted:
            reject()
    except Exception:
        if violated:
            raise CollectionMutationError() from None
        raise
    finally:
        event.remove(session, 'before_flush', reject)
        event.remove(session, 'before_commit', reject)
        event.remove(session, 'after_begin', after_begin)
        event.remove(engine, 'handle_error', handle_error)
        for connection in listeners:
            event.remove(connection, 'before_cursor_execute', before_cursor)
            event.remove(connection, 'commit', reject)
            event.remove(connection, 'begin_twophase', reject)
            event.remove(connection, 'prepare_twophase', reject)
            event.remove(connection, 'commit_twophase', reject)
            event.remove(connection, 'begin', set_read_only)
            event.remove(connection, 'set_connection_execution_options', execution_options)
        for connection, originals in recovery_overrides:
            for name, original in originals.items():
                if original is absent:
                    delattr(connection, name)
                else:
                    setattr(connection, name, original)
        session.rollback()
