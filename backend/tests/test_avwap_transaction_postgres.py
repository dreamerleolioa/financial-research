"""The transaction regression on PostgreSQL, including caller-owned flushed writes."""
from sqlalchemy.orm import Session
from ai_stock_sentinel.db.session import Base
from test_radar_job_postgres import pg_engine
from test_avwap_transaction_boundaries import (
    test_refresh_endpoint_counts_match_committed_batch as endpoint_case,
    test_shared_provider_preserves_flushed_caller_writes as caller_case,
    test_endpoint_collects_all_remote_inputs_before_first_snapshot_write as collection_case,
)
import pytest


@pytest.mark.parametrize('missing_second,reuse', [(False, False), (True, False), (False, True)])
def test_postgres_committed_batch_matches_endpoint_counts(pg_engine, monkeypatch, missing_second, reuse):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        endpoint_case(db, monkeypatch, True, missing_second, reuse)


def test_postgres_provider_keeps_flushed_caller_transaction(pg_engine, monkeypatch):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        caller_case(db, monkeypatch)


def test_postgres_http_collection_has_no_checked_out_connection(pg_engine, monkeypatch):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        collection_case(db, monkeypatch)
