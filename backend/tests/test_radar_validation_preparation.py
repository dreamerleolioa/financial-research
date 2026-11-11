from datetime import date

import pytest
from sqlalchemy import select, func, event
from sqlalchemy.orm import Session

from test_forward_validation_memory import engine_with_tables
from test_daily_radar_forward_validation import _add_candidate, _add_run, _price
from radar_calendar_support import FixtureCalendarProvider
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.daily_radar.validation_execution import run_signal_close_validation
from ai_stock_sentinel.daily_radar.research_validation import run_research_validation


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_collects_exact_window_with_no_domain_writes_or_http_transaction(basis):
    engine = engine_with_tables()
    with Session(engine) as db:
        run = _add_run(db)
        candidate = _add_candidate(db, run)
        candidate.data_dates = {'ohlcv': '2026-06-01'}
        db.commit()
        cid = candidate.id
        db.rollback()
        rows = [_price(day, 100, 110, 90, 100) | {'price_basis': 'unadjusted_price',
            'price_source': 'fixture', 'corporate_actions_checked': True, 'dividends': 0, 'stock_splits': 0}
            for day in ['2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04', '2026-06-05', '2026-06-08']]
        class Provider:
            def fetch(self, symbols, **kwargs):
                assert not db.in_transaction()
                return {symbol: rows for symbol in symbols}
        statements = []
        event.listen(engine, 'before_cursor_execute', lambda c, cu, sql, *args: statements.append(sql))
        request = DailyRadarForwardValidationRunRequest(mode='due', market='TW', as_of_date=date(2026, 6, 8),
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1), windows=[5], return_basis=basis)
        execute = run_signal_close_validation if basis == 'signal_close' else run_research_validation
        bundle = execute(db, request, as_of_date=date(2026, 6, 8), price_provider=Provider(),
            calendar_provider=FixtureCalendarProvider(), candidate_ids=[cid], prepare_only=True)
        assert {row['candidate_id'] for row in bundle.outcomes} == {cid}
        assert bundle.outcomes[0]['status'] == 'validated'
        assert not any(sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in statements)
        assert db.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0
    engine.dispose()
