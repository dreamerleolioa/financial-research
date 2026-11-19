from datetime import date

import pytest
from sqlalchemy import select, func, event
from sqlalchemy.orm import Session

from test_forward_validation_memory import engine_with_tables
from test_daily_radar_forward_validation import _add_candidate, _add_run, _price
from test_radar_job_postgres import pg_engine
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


@pytest.mark.parametrize("basis", ["signal_close", "next_open"])
@pytest.mark.parametrize("revision", ["event", "source", "detail"])
@pytest.mark.parametrize("database", ["sqlite", "postgres"])
def test_price_versions_follow_the_window_across_range_requests(basis, revision, database, monkeypatch, request):
    from ai_stock_sentinel.daily_radar import next_open_action_scope
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector, PRICE_EVIDENCE
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    from ai_stock_sentinel.daily_radar.validation_preparation import ValidationAcceptanceConflict
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun
    from ai_stock_sentinel.db.session import Base

    signal, cutoff = date(2026, 6, 1), date(2026, 7, 2)
    calendar = FixtureCalendarProvider()
    days = calendar.fetch(start_date=signal, end_date=cutoff).sessions
    event_days = [days[3], days[12]]
    corrected = False

    def events(symbol, start, end, provider, *, record_detail=None, record_rights=None):
        params = {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d")}
        provider.capture_twse_report("/dividends", params)
        found = []
        for day in event_days:
            if start <= day <= end:
                _, detail = provider.capture_twse_report("/dividend-detail", {"T1": day.strftime("%Y%m%d")})
                if record_detail is not None:
                    record_detail(day, detail)
                found.append({"family": "rights", "effective_date": str(day), "cash_dividend": 1.0,
                    "cash_terms": "1.00" if corrected and revision == "event" and day == event_days[0] else "1"})
        return found

    monkeypatch.setattr(next_open_action_scope, "_events", events)

    class Provider:
        def fetch(self, symbols, *, start_date, end_date):
            snapshot = {"snapshot_sha256": fingerprint([str(start_date), str(end_date)]), "provider": "yfinance"}
            return {symbol: [
                _price(str(day), price, price, price, price) | {
                    "price_source": "yfinance", "source_capture": snapshot,
                } for day in days if start_date <= day <= end_date
                for price in [100 / .99 ** sum(day >= event for event in event_days)
                              if basis == "signal_close" and symbol != "TAIEX" else 100]
            ] for symbol in symbols}

        def official_research_history(self, symbol, *, start_date, end_date):
            return [_price(str(day), 100, 100, 100, 100) | {
                "price_source": "exchange",
                "official_price_report": {
                    "source_url": "https://www.twse.com.tw/daily?date=20260601",
                    "snapshot_sha256": fingerprint([str(start_date), str(end_date)]),
                },
            } for day in days if start_date <= day <= end_date]

        def capture_twse_report(self, path, params):
            from urllib.parse import urlencode
            detail_changed = corrected and revision == "detail" and params.get("T1") == event_days[0].strftime("%Y%m%d")
            return {}, {"source_url": f"https://www.twse.com.tw{path}?{urlencode(params)}",
                        "snapshot_sha256": fingerprint([params, detail_changed]),
                        "parser_version": "v2" if corrected and revision == "source" else "v1"}

    execute = run_signal_close_validation if basis == "signal_close" else run_research_validation
    engine = request.getfixturevalue("pg_engine") if database == "postgres" else engine_with_tables()
    if database == "postgres":
        Base.metadata.create_all(engine)
    with Session(engine) as db:
        _add_candidate(db, _add_run(db, run_date=signal))
        for day in (days[5], cutoff):
            db.add(DailyRadarPreparedRun(run_date=day, market="TW", selected_symbols=[], symbol_count=0,
                                       universe=[], step_statuses={}))
        db.commit()
        provider = Provider()

        def run(windows, *, as_of_date=cutoff, **kwargs):
            payload = DailyRadarForwardValidationRunRequest(mode="range", market="TW", as_of_date=as_of_date,
                start_date=signal, end_date=signal, windows=windows, return_basis=basis)
            return execute(db, payload, as_of_date=as_of_date, price_provider=provider, calendar_provider=calendar,
                           official_price_collector=OfficialPriceCollector(provider), **kwargs)

        def saved():
            return {row.window_days: row.outcome[PRICE_EVIDENCE]["sha256"] for row in
                    db.scalars(select(DailyRadarForwardValidationResult).execution_options(populate_existing=True))}

        run([5], as_of_date=days[5])
        db.commit()
        original = saved()[5]
        batch = run([5, 20], prepare_only=True)
        proofs = {row["window_days"]: row["outcome"][PRICE_EVIDENCE] for row in batch.outcomes}
        assert proofs[5]["sha256"] == original
        assert [event["effective_date"] for event in proofs[5]["events"]] == [str(event_days[0])]
        assert [event["effective_date"] for event in proofs[20]["events"]] == list(map(str, event_days))
        assert not any(str(event_days[1]) in report["dates"] for report in proofs[5]["event_reports"])
        run([5, 20])
        db.commit()
        accepted = saved()
        assert accepted == {5: original, 20: proofs[20]["sha256"]}

        corrected = True
        with pytest.raises(ValidationAcceptanceConflict, match="validation_price_version_changed"):
            run([5, 20])
        db.commit()
        assert saved() == accepted
