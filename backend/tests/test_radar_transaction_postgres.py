"""Transaction contracts against an explicitly isolated local PostgreSQL database."""
import pytest
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import Base
from test_radar_job_postgres import pg_engine
from test_radar_shared_provider_transactions import (
    test_price_providers_preserve_caller_writes as price_case,
    test_raw_collector_preserves_caller_writes as raw_case,
    test_raw_owner_can_release_read_connection_before_fetch as release_case,
)


@pytest.mark.parametrize('kind', ['forward', 'research'])
@pytest.mark.parametrize('hit', [False, True])
def test_postgres_prices_preserve_flushed_caller_writes(pg_engine, monkeypatch, kind, hit):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        price_case(db, monkeypatch, True, hit, kind, True)


def test_postgres_raw_collection_preserves_flushed_caller_writes(pg_engine):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        raw_case(db, True, [])


def test_postgres_raw_collection_releases_read_connection(pg_engine):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine, autoflush=False) as db:
        release_case(db)
