"""Regressions from the dated 2026-10-21 Radar recovery."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.forward_validation import build_forward_validation_report
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, Phase1AvwapSnapshot, TaiwanDailyBar
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.phase1_avwap.provider import ArchiveFirstDailyPriceProvider
from ai_stock_sentinel.phase1_avwap.service import refresh_phase1_avwap_snapshots_for_symbols


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(type_, compiler, **kwargs):
    return "JSON"


WINDOW_DATES = [
    "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
    "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-29",
    "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06",
    "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14",
]
LISTED_DATES = WINDOW_DATES[6:] + ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]


def _price(day):
    return {"date": day, "open": 100, "high": 101, "low": 99, "close": 100}


def _evaluation(*, symbol="8183.TWO", as_of=date(2026, 10, 21), windows=(20,), calendar=None):
    candidate = {"candidate_id": 4685, "symbol": symbol, "record_date": "2026-09-11",
                 "data_dates": {"ohlcv": "2026-09-11"}, "input_snapshot": {"ohlcv": {"close": 100}}}
    return build_forward_validation_report(
        [candidate], price_series_by_symbol={symbol: [_price(d) for d in ["2026-09-11", *WINDOW_DATES[:7]]]},
        benchmark_prices=[_price(d) for d in ["2026-09-11", *(calendar if calendar is not None else WINDOW_DATES)]],
        market="TW", sample_source="regression", as_of_date=as_of, windows=windows,
    )


def test_official_termination_closes_unfinishable_window_with_replayable_evidence():
    result = _evaluation()
    outcome = result.outcomes[0]
    assert outcome["status"] == "skipped"
    assert outcome["skip_reason"] == "trading_terminated_in_window"
    assert outcome["target_date"] == "2026-10-14"
    evidence = outcome["outcome"]["market_lifecycle"]
    assert evidence["trading_stop_date"] == "2026-09-23"
    assert evidence["termination_date"] == "2026-10-01"
    assert evidence["source_url"] == "https://www.tpex.org.tw/storage/eb_data/11509/11500054131.html"
    assert result.report["current_run_skip_reasons"] == {"trading_terminated_in_window": 1}
    assert result.report["current_run_gaps"][0]["retryable"] is False


@pytest.mark.parametrize("kwargs", [
    {"symbol": "9999.TWO"},
    {"as_of": date(2026, 9, 30)},
    {"calendar": WINDOW_DATES[:15]},
])
def test_unknown_future_or_incomplete_calendar_never_becomes_terminal(kwargs):
    result = _evaluation(**kwargs)
    assert result.outcomes[0]["skip_reason"] == "missing_future_price"
    gap = result.report["current_run_gaps"][0]
    assert gap["candidate_id"] == 4685
    assert gap["signal_date"] == "2026-09-11"
    assert gap["retryable"] is True
    assert "2026-09-23" in gap["missing_dates"]


def test_windows_that_finish_before_the_stop_remain_valid():
    result = _evaluation(windows=(5, 20))
    assert result.outcomes[0]["status"] == "validated"
    assert result.outcomes[0]["target_date"] == "2026-09-18"
    assert result.outcomes[1]["skip_reason"] == "trading_terminated_in_window"


class _Fallback:
    source_provider = "test_fallback"
    source_dataset = "test_fallback"

    def __init__(self):
        self.calls = []

    def fetch_history(self, symbol, *, start_date, end_date):
        self.calls.append((symbol, start_date, end_date))
        return []


@pytest.fixture
def archive_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[TaiwanDailyBar.__table__, DailyRadarPreparedRun.__table__, Phase1AvwapSnapshot.__table__])
    with Session(engine) as session:
        session.add(DailyRadarPreparedRun(
            run_date=date(2026, 10, 21), market="TW", status="prepared", selected_symbols=["7856.TWO"],
            universe=[], symbol_count=1, step_statuses={},
            market_context={"benchmark": {"symbol": "TAIEX", "price_history": [_price(d) for d in LISTED_DATES]}},
        ))
        for day in LISTED_DATES:
            session.add(TaiwanDailyBar(
                symbol="7856.TWO", market="TWO", trade_date=date.fromisoformat(day), open=100, high=101,
                low=99, close=100, volume=1000, amount=100000, dataset="taiwan_market_daily_ohlcv",
                adjustment_mode="unadjusted", source_provider="tpex", source_dataset="tpex_mainboard_daily_close_quotes", is_final=True,
            ))
        session.commit()
        yield session
    engine.dispose()


def test_verified_new_listing_short_history_is_skipped_without_prelisting_fallback(archive_session):
    fallback = _Fallback()
    provider = ArchiveFirstDailyPriceProvider(archive_session, fallback_provider=fallback)
    result = refresh_phase1_avwap_snapshots_for_symbols(
        archive_session, symbols=["7856.TWO"], data_date=date(2026, 10, 21), provider=provider,
    )
    assert fallback.calls == []
    assert result.missing_symbols == []
    assert result.skipped_symbol_reasons == {"7856.TWO": "insufficient_listed_history"}
    snapshot = result.snapshots[0]
    assert snapshot.freshness == "missing"
    assert snapshot.missing_reason == "insufficient_listed_history"
    assert snapshot.payload["anchors"] == {}
    trace = snapshot.payload["source"]["trace"]
    assert trace["rows_used"] == 19
    assert trace["required_trading_bars"] == 60
    assert trace["market_lifecycle"]["listing_date"] == "2026-09-22"


@pytest.mark.parametrize("include_missing", [False, True])
def test_avwap_endpoint_keeps_short_history_separate_from_real_missing_prices(archive_session, include_missing):
    from ai_stock_sentinel.daily_radar.refresh_router import refresh_daily_radar_avwap_endpoint
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarRefreshStepRequest
    prepared = archive_session.query(DailyRadarPreparedRun).one()
    prepared.selected_symbols = ["7856.TWO", "^TWII"] + (["9999.TWO"] if include_missing else [])
    archive_session.commit()
    response = refresh_daily_radar_avwap_endpoint(
        DailyRadarRefreshStepRequest(run_date=date(2026, 10, 21)), db=archive_session,
        provider=ArchiveFirstDailyPriceProvider(archive_session, fallback_provider=_Fallback()),
    )
    assert response.status == ("failed" if include_missing else "completed")
    assert response.missing_symbols == (["9999.TWO"] if include_missing else [])
    assert response.skipped_symbol_reasons["7856.TWO"] == "insufficient_listed_history"
    assert "^TWII" in response.skipped_symbols
    details = prepared.step_statuses["refresh-avwap"]
    assert details["missing_symbols"] == (["9999.TWO"] if include_missing else [])
    assert details["skipped_symbol_reasons"]["7856.TWO"] == "insufficient_listed_history"


def test_prelisting_archive_rows_cannot_pad_a_short_listed_history(archive_session):
    from datetime import timedelta
    for offset in range(65):
        archive_session.add(TaiwanDailyBar(
            symbol="7856.TWO", market="TWO", trade_date=date(2026, 7, 1) + timedelta(days=offset),
            open=100, high=101, low=99, close=100, volume=1000, amount=100000,
            dataset="taiwan_market_daily_ohlcv", adjustment_mode="unadjusted",
            source_provider="tpex", source_dataset="tpex_mainboard_daily_close_quotes", is_final=True,
        ))
    archive_session.commit()
    fallback = _Fallback()
    result = refresh_phase1_avwap_snapshots_for_symbols(
        archive_session, symbols=["7856.TWO"], data_date=date(2026, 10, 21),
        provider=ArchiveFirstDailyPriceProvider(archive_session, fallback_provider=fallback),
    )
    assert result.skipped_symbol_reasons == {"7856.TWO": "insufficient_listed_history"}
    assert fallback.calls == []


@pytest.mark.parametrize("mode", ["archive_first", "remote_only"])
@pytest.mark.parametrize("damage", ["missing_session", "invalid_price"])
def test_remote_fallback_cannot_reintroduce_prelisting_rows_or_fake_long_anchors(archive_session, mode, damage):
    from datetime import timedelta
    from ai_stock_sentinel.phase1_avwap.calculator import DailyPriceBar
    damaged = archive_session.query(TaiwanDailyBar).filter_by(trade_date=date(2026, 10, 6))
    if damage == "missing_session":
        damaged.delete()
    else:
        damaged.one().open = 98
    archive_session.commit()

    class FullHistory(_Fallback):
        def fetch_history(self, symbol, *, start_date, end_date):
            super().fetch_history(symbol, start_date=start_date, end_date=end_date)
            # Model a vendor that returns older history even when a start is supplied.
            dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(65)]
            dates += [date.fromisoformat(d) for d in LISTED_DATES]
            return [DailyPriceBar(d, 100, 101, 99, 100, 1000, 100000) for d in dates]

    fallback = FullHistory()
    result = refresh_phase1_avwap_snapshots_for_symbols(
        archive_session, symbols=["7856.TWO"], data_date=date(2026, 10, 21),
        provider=ArchiveFirstDailyPriceProvider(archive_session, fallback_provider=fallback, provider_mode=mode),
    )
    assert fallback.calls == [("7856.TWO", date(2026, 9, 22), date(2026, 10, 21))]
    assert result.missing_symbols == ["7856.TWO"]
    assert result.skipped_symbol_reasons == {}
    assert result.snapshots[0].payload["anchors"] == {}


@pytest.mark.parametrize("damage", ["missing_session", "invalid_price", "missing_calendar", "unknown_symbol", "future_evidence", "unofficial_source"])
def test_short_history_skip_requires_known_listing_and_complete_valid_calendar(archive_session, damage):
    session = archive_session
    if damage == "missing_session":
        session.query(TaiwanDailyBar).filter_by(trade_date=date(2026, 10, 6)).delete()
    elif damage == "invalid_price":
        session.query(TaiwanDailyBar).filter_by(trade_date=date(2026, 10, 6)).one().open = 98
    elif damage == "missing_calendar":
        session.query(DailyRadarPreparedRun).delete()
    elif damage == "unknown_symbol":
        session.query(TaiwanDailyBar).update({"symbol": "9999.TWO"})
    elif damage == "unofficial_source":
        session.query(TaiwanDailyBar).update({"source_provider": "finmind"})
    session.commit()
    fallback = _Fallback()
    symbol = "9999.TWO" if damage == "unknown_symbol" else "7856.TWO"
    cutoff = date(2026, 10, 20) if damage == "future_evidence" else date(2026, 10, 21)
    result = refresh_phase1_avwap_snapshots_for_symbols(
        session, symbols=[symbol], data_date=cutoff,
        provider=ArchiveFirstDailyPriceProvider(session, fallback_provider=fallback),
    )
    assert len(fallback.calls) == 1
    assert result.missing_symbols == [symbol]
    assert result.skipped_symbol_reasons == {}
