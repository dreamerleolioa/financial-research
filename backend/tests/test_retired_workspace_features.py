"""Retired workspace data must not affect the remaining research workflows."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.api import app
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.db.models import StockAnalysisCache, UserPortfolio, UserWatchlist
from ai_stock_sentinel.phase1_avwap.universe import resolve_phase1_refresh_symbol_set, resolve_phase1_managed_universe
from ai_stock_sentinel.data_sources.fundamental.service import resolve_managed_fundamental_symbols
from ai_stock_sentinel.daily_radar.managed_raw_data import select_managed_raw_data_symbols


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(*args, **kwargs):
    return "JSON"


@pytest.mark.parametrize("method,path", [
    ("GET", "/watchlist"), ("POST", "/watchlist"),
    ("GET", "/portfolio"), ("POST", "/portfolio"),
    ("GET", "/portfolio/closed"), ("GET", "/portfolio/risk-summary"),
    ("GET", "/portfolio/latest-history"), ("POST", "/analyze/position"),
])
def test_retired_endpoints_are_unavailable(method, path):
    response = TestClient(app).request(method, path, json={})
    assert response.status_code == 404


def test_retained_records_do_not_drive_research_refreshes():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            UserPortfolio(symbol="2330.TW", entry_price=100, quantity=1, entry_date=date(2026, 9, 1), user_id=1),
            UserWatchlist(symbol="2454.TW", user_id=1),
            StockAnalysisCache(symbol="6488.TWO", record_date=date(2026, 10, 1), analysis_type="general", analysis_is_final=True),
            StockAnalysisCache(symbol="3008.TW", record_date=date(2026, 10, 1), analysis_type="position", analysis_is_final=True),
            StockAnalysisCache(symbol="2317.TW", record_date=date(2026, 10, 3), analysis_type="general", analysis_is_final=True),
        ])
        session.commit()
        statements = []
        event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement))
        assert resolve_phase1_refresh_symbol_set(session, seed_symbols=["0050.TW"]).symbols == ["0050.TW"]
        assert resolve_phase1_managed_universe(session, user_id=1) == []
        assert resolve_managed_fundamental_symbols(session, raw_pool_date=date(2026, 10, 1)) == []
        assert select_managed_raw_data_symbols(session, run_date=date(2026, 10, 1)).symbols == ("6488.TWO",)
        assert not any("user_portfolio" in sql or "user_watchlist" in sql for sql in statements)
        assert session.query(UserPortfolio).count() == 1
        assert session.query(UserWatchlist).count() == 1
