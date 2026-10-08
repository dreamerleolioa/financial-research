from datetime import date, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration import repository
from ai_stock_sentinel.db.models import DailyRadarPreparedRun
from ai_stock_sentinel.db.session import Base
from test_daily_radar_forward_validation import _forward_validation_sqlite_engine


def add_snapshot(session, day, history, *, market="TW", symbol="TAIEX"):
    session.add(DailyRadarPreparedRun(
        run_date=day, market=market, status="prepared", selected_symbols=[], universe=[],
        symbol_count=0, step_statuses={}, errors=[],
        market_context={"benchmark": {"symbol": symbol, "price_history": history,
                                     "unused": "x" * 10_000},
                        "other_large_context": "y" * 10_000}))


def test_calendar_merges_more_than_thirty_snapshots_without_loading_orm_context():
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(engine, tables=[DailyRadarPreparedRun.__table__])
    start = date(2026, 1, 1)
    with Session(engine) as session:
        for i in range(40):
            day = start + timedelta(days=i)
            add_snapshot(session, day, [{"date": day.isoformat(), "close": 100 + i}])
        session.commit()
        session.expunge_all()
        options, columns = [], []
        def record_query(state):
            if state.is_select:
                options.append(state.execution_options)
                columns.extend(state.statement.selected_columns)
        event.listen(session, "do_orm_execute", record_query)
        days = repository.load_benchmark_calendar_from_prepared_market_context(
            session, market="TW", benchmark_symbol="TAIEX", start_date=start,
            as_of_date=start + timedelta(days=39))
        assert days == [start + timedelta(days=i) for i in range(40)]
        assert any(option.get("yield_per") == 1 for option in options)
        assert not any(column.compare(DailyRadarPreparedRun.market_context.__clause_element__())
                       for column in columns)
        assert not session.identity_map
    engine.dispose()


def test_calendar_uses_only_valid_matching_benchmark_dates_available_by_cutoff():
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(engine, tables=[DailyRadarPreparedRun.__table__])
    start, end = date(2026, 6, 1), date(2026, 6, 8)
    with Session(engine) as session:
        add_snapshot(session, end, [
            {"date": "2026-05-29", "close": 100},
            {"date": "2026-06-01", "close": 100},
            {"date": "2026-06-01", "close": 101},  # revisions do not duplicate calendar dates
            {"date": "2026-06-02", "close": 0},
            {"date": "2026-06-03", "close": None},
            {"date": "2026-06-04", "close": "NaN"},
            {"date": "2026-06-08", "close": 110},
            {"date": "2026-06-09", "close": 111},
            {"date": "invalid", "close": 100}, None,
        ])
        add_snapshot(session, date(2026, 6, 4), [{"date": "2026-06-05", "close": 100}])
        add_snapshot(session, end, [{"date": "2026-06-02", "close": 100}], market="US")
        add_snapshot(session, date(2026, 6, 5), [{"date": "2026-06-03", "close": 100}], symbol="SPX")
        add_snapshot(session, date(2026, 6, 9), [{"date": "2026-06-04", "close": 100}])
        session.commit()
        assert repository.load_benchmark_calendar_from_prepared_market_context(
            session, market="TW", benchmark_symbol="TAIEX", start_date=start, as_of_date=end
        ) == [start, end]
    engine.dispose()


def test_calendar_is_empty_when_prepared_table_is_unavailable():
    engine = _forward_validation_sqlite_engine()
    with Session(engine) as session:
        assert repository.load_benchmark_calendar_from_prepared_market_context(
            session, market="TW", benchmark_symbol="TAIEX", start_date=date(2026, 6, 1),
            as_of_date=date(2026, 6, 8)) == []
    engine.dispose()
