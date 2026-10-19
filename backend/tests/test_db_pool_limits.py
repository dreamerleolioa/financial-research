from unittest.mock import MagicMock

import pytest

from ai_stock_sentinel.db import session


def test_postgres_pool_has_a_finite_connection_budget(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:unused@localhost/test")
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    engine = MagicMock()
    monkeypatch.setattr(session, "create_engine", engine)
    session._make_session_factory()
    assert engine.call_args.kwargs["pool_size"] == 3
    assert engine.call_args.kwargs["max_overflow"] == 0


@pytest.mark.parametrize("key,value", [("DB_POOL_SIZE", "0"), ("DB_POOL_SIZE", "-1"), ("DB_MAX_OVERFLOW", "-1")])
def test_unbounded_or_invalid_postgres_pool_refused(monkeypatch, key, value):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:unused@localhost/test")
    monkeypatch.setenv(key, value)
    engine = MagicMock()
    monkeypatch.setattr(session, "create_engine", engine)
    with pytest.raises(ValueError, match=key):
        session._make_session_factory()
    engine.assert_not_called()


def test_sqlite_keeps_its_native_pool_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    engine = MagicMock()
    monkeypatch.setattr(session, "create_engine", engine)
    session._make_session_factory()
    assert engine.call_args.kwargs == {"pool_pre_ping": True}


def test_expected_database_name_prevents_using_the_source_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:unused@localhost/zeabur")
    monkeypatch.setenv("EXPECTED_DATABASE_NAME", "financial_research_test")
    engine = MagicMock()
    monkeypatch.setattr(session, "create_engine", engine)
    with pytest.raises(RuntimeError, match="EXPECTED_DATABASE_NAME"):
        session._make_session_factory()
    engine.assert_not_called()


@pytest.mark.parametrize('override', ['dbname=zeabur', 'database=zeabur', 'dsn=dbname%3Dzeabur', 'service=production'])
def test_expected_database_refuses_driver_target_overrides(monkeypatch, override):
    monkeypatch.setenv('DATABASE_URL', f'postgresql://app:unused@localhost/financial_research_test?{override}')
    monkeypatch.setenv('EXPECTED_DATABASE_NAME', 'financial_research_test')
    with pytest.raises(RuntimeError, match='EXPECTED_DATABASE_NAME'):
        session.get_database_url()
