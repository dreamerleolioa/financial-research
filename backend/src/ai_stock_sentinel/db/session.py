from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _make_session_factory() -> sessionmaker:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    engine = create_engine(url, pool_pre_ping=True)
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
