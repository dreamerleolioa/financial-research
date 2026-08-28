from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.managed_raw_data import (
    select_managed_raw_data_symbols,
)
from ai_stock_sentinel.db.models import StockAnalysisCache, UserPortfolio
from ai_stock_sentinel.db.session import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_managed_raw_data_selection_prioritizes_active_and_has_no_lookahead(
    db_session: Session,
) -> None:
    run_date = date(2026, 6, 1)
    db_session.add_all(
        [
            _portfolio("2330.TW", entry_date=date(2026, 5, 1)),
            _portfolio("006208.tw", entry_date=date(2026, 5, 2)),
            _portfolio("2317.TW", entry_date=date(2026, 6, 2)),
            _portfolio("AAPL", entry_date=date(2026, 5, 3)),
            _analysis("2454.TW", record_date=run_date),
            _analysis("2330.TW", record_date=run_date),
            _analysis("3008.TW", record_date=date(2026, 5, 2)),
            _analysis("9999.TW", record_date=date(2026, 5, 1)),
            _analysis("6488.TWO", record_date=date(2026, 6, 2)),
            _analysis("MSFT", record_date=run_date),
        ]
    )
    db_session.commit()

    selection = select_managed_raw_data_symbols(
        db_session,
        run_date=run_date,
        max_symbols=3,
    )

    assert selection.active_symbols == ("006208.TW", "2330.TW")
    assert selection.recent_analysis_symbols == (
        "2330.TW",
        "2454.TW",
        "3008.TW",
    )
    assert selection.symbols == ("006208.TW", "2330.TW", "2454.TW")
    assert selection.active_symbol_count == 2
    assert selection.recent_analysis_symbol_count == 3
    assert selection.overlap_symbol_count == 1
    assert selection.deferred_recent_symbol_count == 1
    assert selection.active_symbols_over_budget is False


def test_managed_raw_data_selection_fails_closed_when_active_positions_exceed_budget(
    db_session: Session,
) -> None:
    db_session.add_all(
        [
            _portfolio("2330.TW", entry_date=date(2026, 5, 1)),
            _portfolio("2454.TW", entry_date=date(2026, 5, 2)),
        ]
    )
    db_session.commit()

    selection = select_managed_raw_data_symbols(
        db_session,
        run_date=date(2026, 6, 1),
        max_symbols=1,
    )

    assert selection.symbols == ()
    assert selection.active_symbol_count == 2
    assert selection.active_symbols_over_budget is True


def _portfolio(symbol: str, *, entry_date: date) -> UserPortfolio:
    return UserPortfolio(
        symbol=symbol,
        entry_price=100,
        quantity=1,
        entry_date=entry_date,
        is_active=True,
    )


def _analysis(symbol: str, *, record_date: date) -> StockAnalysisCache:
    return StockAnalysisCache(
        symbol=symbol,
        record_date=record_date,
        analysis_type="general",
        analysis_is_final=True,
    )
