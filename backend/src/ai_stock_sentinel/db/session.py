from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    expected = os.environ.get("EXPECTED_DATABASE_NAME")
    if expected:
        parsed = make_url(url)
        if parsed.database != expected or parsed.query.keys() & {'dbname', 'database', 'dsn', 'service', 'servicefile'}:
            raise RuntimeError("Database target does not match EXPECTED_DATABASE_NAME")
    return url


def _make_session_factory() -> sessionmaker:
    url = get_database_url()
    pool_options = {}
    if make_url(url).get_backend_name() == "postgresql":
        for name, default, minimum in [
            ("DB_POOL_SIZE", 5, 1),
            ("DB_MAX_OVERFLOW", 0, 0),
        ]:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer >= {minimum}") from exc
            if value < minimum:
                raise ValueError(f"{name} must be >= {minimum}")
            pool_options[name.removeprefix("DB_").lower()] = value
    engine = create_engine(url, pool_pre_ping=True, **pool_options)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


_SessionLocal: sessionmaker | None = None
_lock = __import__("threading").Lock()


def _get_session_local() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        with _lock:
            if _SessionLocal is None:
                _SessionLocal = _make_session_factory()
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency – yields a DB session and closes it after the request."""
    db = _get_session_local()()
    try:
        yield db
    finally:
        db.close()


def create_session() -> Session:
    """Create an explicitly-owned session for non-FastAPI application services."""
    return _get_session_local()()
