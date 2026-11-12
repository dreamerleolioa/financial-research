"""Real adjusted-price adapter failures must leave retryable Radar gaps."""
from datetime import date
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel import api
from ai_stock_sentinel.calibration.price_provider import (
    YFinanceForwardPriceProvider, get_forward_price_provider,
)
from ai_stock_sentinel.daily_radar.durable_providers import CachedForwardPrices
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
from ai_stock_sentinel.daily_radar.trading_calendar import get_trading_calendar_provider
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult, RadarSourceSnapshot
from ai_stock_sentinel.db.session import Base, get_db
from test_daily_radar_forward_validation import (
    _add_candidate, _add_raw, _add_run, _add_validation_checkpoint,
    _forward_validation_sqlite_engine,
)
from tests.radar_calendar_support import FixtureCalendarProvider


START = date(2026, 6, 1)
AS_OF = date(2026, 11, 11)
WINDOWS = [5, 10, 20, 40, 60]
HEALTHY = ("1101.TW", "9904.TW")
MISSING = "8183.TWO"


@pytest.fixture
def radar(monkeypatch, request):
    engine = _forward_validation_sqlite_engine()
    # Include real independently committed source snapshots for the cached path.
    from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarCandidate, StockRawData
    Base.metadata.create_all(engine, tables=[DailyRadarRun.__table__, DailyRadarCandidate.__table__,
        DailyRadarForwardValidationResult.__table__, StockRawData.__table__, RadarSourceSnapshot.__table__])
    calendar = FixtureCalendarProvider()
    days = calendar.fetch(start_date=START, end_date=AS_OF).sessions
    with Session(engine) as db:
        _add_validation_checkpoint(db, AS_OF)
        run = _add_run(db, run_date=START)
        for symbol in (*HEALTHY, MISSING):
            _add_candidate(db, run, symbol=symbol)
        if getattr(request, "param", "benchmark") == "benchmark":
            for day in days:
                _add_raw(db, "TAIEX", day, 1000)
        db.commit()

    def database():
        with Session(engine) as db:
            yield db

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    monkeypatch.setenv("DAILY_RADAR_DURABLE_EXECUTION_ENABLED", "false")
    monkeypatch.setitem(api.app.dependency_overrides, get_db, database)
    monkeypatch.setitem(api.app.dependency_overrides, get_trading_calendar_provider, lambda: calendar)
    yield engine, days
    engine.dispose()


def _run(*, raise_server_exceptions=False):
    return TestClient(api.app, raise_server_exceptions=raise_server_exceptions).post(
        "/internal/daily-radar/forward-validation/run",
        json={"mode": "due", "as_of_date": str(AS_OF), "windows": WINDOWS},
        headers={"Authorization": "Bearer test-token"},
    )


def _healthy_rows(days):
    return [{"date": str(day), "open": 100, "high": 102, "low": 98, "close": 100} for day in days]


@pytest.mark.parametrize("empty", [None, pd.DataFrame()])
def test_real_yfinance_empty_keeps_all_five_windows_retryable(radar, monkeypatch, empty):
    engine, days = radar
    with Session(engine) as db:
        for symbol in HEALTHY:
            for day in days:
                _add_raw(db, symbol, day, 100)
        db.commit()
    calls = []

    def history(ticker, **kwargs):
        calls.append([ticker.ticker])
        assert kwargs["raise_errors"] is True
        assert kwargs["auto_adjust"] is True
        assert kwargs["actions"] is False
        return empty

    monkeypatch.setattr("ai_stock_sentinel.calibration.price_provider.yf.Ticker.history", history)
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, YFinanceForwardPriceProvider)
    response = _run()
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["validated_count"], body["retryable_skipped_count"], body["terminal_skipped_count"]) == (10, 5, 0)
    assert body["report"]["current_run_skip_reasons"] == {"missing_future_price": 5}
    assert calls == [[MISSING]]  # No retry of identical empty input.
    error = body["report"]["source_recovery"][0]
    assert error["symbols"] == [MISSING]
    assert (error["start_date"], error["end_date"]) == (str(START), str(AS_OF))
    assert error["reason"] == "forward_price_provider_returned_no_data"
    assert body["report"]["source_recovery_summary"]["error_count"] == 1
    assert {g["window_days"] for g in body["report"]["current_run_gaps"]} == set(WINDOWS)
    assert all(g["missing_dates"] for g in body["report"]["current_run_gaps"])


def test_cached_empty_symbol_preserves_snapshots_and_collects_later_symbols(radar, monkeypatch):
    engine, days = radar
    calls = []

    class Prices:
        def fetch(self, symbols, **kwargs):
            calls.extend(symbols)
            return {s: _healthy_rows(days) for s in symbols if s != MISSING}

    # The adapter must classify an empty single-symbol response too, even if
    # yfinance returned a non-empty frame with no usable rows for that symbol.
    store = SourceSnapshotStore(engine)
    provider = CachedForwardPrices(None, store=store, provider=Prices())
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, lambda: provider)
    response = _run()
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["validated_count"], body["retryable_skipped_count"], body["terminal_skipped_count"]) == (10, 5, 0)
    assert calls == [HEALTHY[0], MISSING, HEALTHY[1]]
    assert body["report"]["source_recovery"][0]["symbols"] == [MISSING]
    with Session(engine) as db:
        snapshots = db.scalars(select(RadarSourceSnapshot)).all()
        assert {s.request["params"]["symbol"] for s in snapshots} == set(HEALTHY)
        assert len(snapshots) == 2
        assert db.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 15


@pytest.mark.parametrize("error", [ConnectionError("provider_transport_failed"),
    RuntimeError("forward_price_provider_returned_no_data")])
def test_unclassified_failure_still_aborts_without_writing_results(radar, monkeypatch, error):
    engine, _ = radar

    def download(*args, **kwargs):
        raise error

    monkeypatch.setattr("ai_stock_sentinel.calibration.price_provider.yf.Ticker.history", download)
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, YFinanceForwardPriceProvider)
    with pytest.raises(type(error), match=str(error)):
        _run(raise_server_exceptions=True)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0


@pytest.mark.parametrize("radar", ["no_benchmark"], indirect=True)
def test_empty_benchmark_never_fabricates_validated_or_terminal_results(radar, monkeypatch):
    monkeypatch.setattr("ai_stock_sentinel.calibration.price_provider.yf.Ticker.history", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, YFinanceForwardPriceProvider)
    response = _run()
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["validated_count"], body["retryable_skipped_count"], body["terminal_skipped_count"]) == (0, 15, 0)
    assert body["report"]["current_run_skip_reasons"] == {"missing_benchmark": 15}
    assert body["report"]["source_recovery"][0]["symbols"] == ["TAIEX"]


@pytest.mark.parametrize("scope, wrong_date", [([], False), (["unrequested.TW"], False), ([MISSING], True)])
def test_malformed_unavailable_scope_fails_instead_of_looping(radar, monkeypatch, scope, wrong_date):
    from ai_stock_sentinel.calibration.price_provider import ForwardPriceUnavailableError
    calls = []

    class Prices:
        def fetch(self, symbols, *, start_date, end_date):
            calls.append(list(symbols))
            raise ForwardPriceUnavailableError(scope, start_date=AS_OF if wrong_date else start_date, end_date=end_date)

    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, Prices)
    with pytest.raises(ForwardPriceUnavailableError):
        _run(raise_server_exceptions=True)
    assert len(calls) == 1


def test_prepare_only_preserves_empty_source_diagnostics_without_result_writes(radar, monkeypatch):
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
    from ai_stock_sentinel.daily_radar.validation_execution import run_signal_close_validation
    engine, _ = radar
    with Session(engine) as db:
        def download(*args, **kwargs):
            assert not db.in_transaction()
            return pd.DataFrame()
        monkeypatch.setattr("ai_stock_sentinel.calibration.price_provider.yf.Ticker.history", download)
        bundle = run_signal_close_validation(db,
            DailyRadarForwardValidationRunRequest(mode="due", windows=WINDOWS), as_of_date=AS_OF,
            price_provider=YFinanceForwardPriceProvider(), calendar_provider=FixtureCalendarProvider(), prepare_only=True)
        assert len(bundle.outcomes) == 15
        assert all(row["skip_reason"] == "missing_future_price" for row in bundle.outcomes)
        assert bundle.diagnostics[0]["symbols"] == sorted((*HEALTHY, MISSING))
        assert db.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0


@pytest.mark.parametrize("error_kind", ["transport", "rate_limit", "programming", "indexing_bug", "http_503"])
@pytest.mark.parametrize("cold_timezone", [False, True])
def test_sdk_does_not_disguise_real_failures_as_empty_prices(radar, monkeypatch, error_kind, cold_timezone):
    """Exercise real Ticker/PriceHistory and the SDK's exception-hiding download."""
    from yfinance.data import YfData
    from yfinance.exceptions import YFRateLimitError
    from ai_stock_sentinel.calibration.price_provider import yf
    engine, days = radar
    with Session(engine) as db:
        for symbol in HEALTHY:
            for day in days:
                _add_raw(db, symbol, day, 100)
        db.commit()
    error = {"transport": ConnectionError("sdk_transport_failed"), "rate_limit": YFRateLimitError(),
        "programming": RuntimeError("sdk_programming_failed"), "indexing_bug": IndexError("unrelated_indexing_bug"),
        "http_503": ConnectionError("sdk_http_503")}[error_kind]

    def get(*args, **kwargs):
        if error_kind != "http_503":
            raise error

        def raise_for_status():
            raise error

        return SimpleNamespace(status_code=503, text="{}", json=lambda: {}, raise_for_status=raise_for_status)

    if cold_timezone:
        monkeypatch.setattr("yfinance.cache.get_tz_cache", lambda: SimpleNamespace(lookup=lambda s: None, store=lambda *a: None))
        monkeypatch.setattr(yf.Ticker, "info", property(lambda self: {}))
    else:
        monkeypatch.setattr(yf.Ticker, "_get_ticker_tz", lambda *a, **kw: "Asia/Taipei")
    monkeypatch.setattr(YfData, "get", get)
    monkeypatch.setattr(YfData, "cache_get", get)
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, YFinanceForwardPriceProvider)
    with pytest.raises(type(error), match=str(error)):
        _run(raise_server_exceptions=True)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0


@pytest.mark.parametrize("status", [200, 404])
@pytest.mark.parametrize("cold_timezone", [False, True])
def test_sdk_explicit_missing_prices_stay_retryable_without_global_config_changes(radar, monkeypatch, status, cold_timezone):
    from yfinance.data import YfData
    from ai_stock_sentinel.calibration.price_provider import yf
    before = yf.config.debug.hide_exceptions
    payload = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No price data found"}}}
    response = SimpleNamespace(status_code=status, text="{}", json=lambda: payload)
    if cold_timezone:
        monkeypatch.setattr("yfinance.cache.get_tz_cache", lambda: SimpleNamespace(lookup=lambda s: None, store=lambda *a: None))
        monkeypatch.setattr(yf.Ticker, "info", property(lambda self: {}))
    else:
        monkeypatch.setattr(yf.Ticker, "_get_ticker_tz", lambda *a, **kw: "Asia/Taipei")
    monkeypatch.setattr(YfData, "get", lambda *a, **kw: response)
    monkeypatch.setattr(YfData, "cache_get", lambda *a, **kw: response)
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, YFinanceForwardPriceProvider)
    result = _run()
    assert result.status_code == 200, result.text
    body = result.json()
    assert (body["validated_count"], body["retryable_skipped_count"], body["terminal_skipped_count"]) == (0, 15, 0)
    assert body["report"]["current_run_skip_reasons"] == {"missing_future_price": 15}
    assert yf.config.debug.hide_exceptions == before


def test_strict_history_preserves_unadjusted_research_actions_and_benchmark_alias(monkeypatch):
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider, yf
    calls = []

    def history(ticker, **kwargs):
        calls.append(ticker.ticker)
        assert kwargs == {"start": START, "end": date(2026, 6, 2), "period": None, "interval": "1d",
            "auto_adjust": False, "actions": True, "raise_errors": True}
        return pd.DataFrame({"Open": [100], "High": [102], "Low": [98], "Close": [101],
            "Volume": [500], "Dividends": [1], "Stock Splits": [0]}, index=pd.to_datetime([str(START)]))

    monkeypatch.setattr(yf.Ticker, "history", history)
    result = YFinanceResearchPriceProvider().fetch(["2330.TW", "TAIEX"], start_date=START, end_date=START)
    assert sorted(calls) == ["2330.TW", "^TWII"]
    assert result["2330.TW"][0] == {"date": str(START), "open": 100, "high": 102, "low": 98, "close": 101,
        "price_basis": "unadjusted_price", "price_source": "yfinance", "corporate_actions_checked": True,
        "dividends": 1, "stock_splits": 0, "volume": 500}
    assert result["TAIEX"][0]["dividends"] == 0


@pytest.mark.parametrize("payload", [{}, {"chart": {}},
    {"chart": {"result": None, "error": {"code": "Unauthorized", "description": "Unauthorized"}}}])
def test_sdk_unknown_responses_are_not_downgraded_to_empty_prices(radar, monkeypatch, payload):
    from yfinance.data import YfData
    from yfinance.exceptions import YFPricesMissingError
    from ai_stock_sentinel.calibration.price_provider import yf
    response = SimpleNamespace(status_code=200, text="{}", json=lambda: payload)
    monkeypatch.setattr(yf.Ticker, "_get_ticker_tz", lambda *a, **kw: "Asia/Taipei")
    monkeypatch.setattr(YfData, "get", lambda *a, **kw: response)
    monkeypatch.setattr(YfData, "cache_get", lambda *a, **kw: response)
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, YFinanceForwardPriceProvider)
    with pytest.raises(YFPricesMissingError):
        _run(raise_server_exceptions=True)


@pytest.mark.parametrize("result", [
    [{"meta": {"exchangeTimezoneName": "Asia/Taipei"}, "indicators": {"quote": []}}],
    [{"meta": {}, "indicators": {"quote": [{}]}}], [], None,
])
def test_recognized_empty_chart_shapes_do_not_escape_as_sdk_index_error(radar, monkeypatch, result):
    from yfinance.data import YfData
    from ai_stock_sentinel.calibration.price_provider import yf
    payload = {"chart": {"error": None, "result": result}}
    response = SimpleNamespace(status_code=200, text=json.dumps(payload), json=lambda: payload)
    monkeypatch.setattr(yf.Ticker, "_get_ticker_tz", lambda *a, **kw: "Asia/Taipei")
    monkeypatch.setattr(YfData, "get", lambda *a, **kw: response)
    monkeypatch.setattr(YfData, "cache_get", lambda *a, **kw: response)
    monkeypatch.setitem(api.app.dependency_overrides, get_forward_price_provider, YFinanceForwardPriceProvider)
    result = _run()
    assert result.status_code == 200, result.text
    assert result.json()["retryable_skipped_count"] == 15
    assert result.json()["validated_count"] == result.json()["terminal_skipped_count"] == 0


def test_cold_timezone_quote_http_failure_is_not_downgraded_to_empty(monkeypatch):
    from yfinance.data import YfData
    from yfinance.scrapers.quote import HTTPError
    from ai_stock_sentinel.calibration.price_provider import yf
    payload = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No price data found"}}}
    response = SimpleNamespace(status_code=404, text=json.dumps(payload), json=lambda: payload)
    raw_json_calls = []

    def raw_json(*args, **kwargs):
        raw_json_calls.append(args)
        raise HTTPError("fixture503", response=SimpleNamespace(text="fixture503"))

    monkeypatch.setattr("yfinance.base._tz_info_fetch_ctr", 0)
    monkeypatch.setattr("yfinance.cache.get_tz_cache", lambda: SimpleNamespace(lookup=lambda s: None, store=lambda *a: None))
    monkeypatch.setattr(YfData, "get", lambda *a, **kw: response)
    monkeypatch.setattr(YfData, "cache_get", lambda *a, **kw: response)
    monkeypatch.setattr(YfData, "get_raw_json", raw_json)
    with pytest.raises(HTTPError, match="fixture503"):
        YFinanceForwardPriceProvider().fetch([MISSING], start_date=START, end_date=AS_OF)
    assert len(raw_json_calls) == 2


@pytest.mark.parametrize("quotes, timezone_from_info", [([], False), ([{}], False), (None, True)])
def test_empty_recent_quotes_keep_usable_timezone_and_historical_prices(monkeypatch, quotes, timezone_from_info):
    from yfinance.data import YfData
    from ai_stock_sentinel.calibration.price_provider import yf
    meta = {"exchangeTimezoneName": "Asia/Taipei", "instrumentType": "EQUITY", "currency": "TWD"}
    timezone_body = {"chart": {"error": None, "result": [{"meta": meta, "indicators": {"quote": quotes}}]}}
    if timezone_from_info:
        timezone_body = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
    timestamp = [int(pd.Timestamp(d, tz="Asia/Taipei").timestamp()) for d in ("2026-06-01", "2026-06-02")]
    history_body = {"chart": {"error": None, "result": [{"meta": meta, "timestamp": timestamp,
        "indicators": {"quote": [{"open": [100, 101], "high": [102, 103], "low": [98, 99],
            "close": [100, 101], "volume": [500, 501]}], "adjclose": [{"adjclose": [100, 101]}]}}]}}
    warm = [False]
    calls = []
    info_calls = []
    monkeypatch.setattr("yfinance.base._tz_info_fetch_ctr", 0)

    def get(*args, **kwargs):
        timezone_request = kwargs.get("params", {}).get("range") == "1d"
        calls.append("timezone" if timezone_request else "history")
        body = timezone_body if timezone_request else history_body
        return SimpleNamespace(status_code=200, text=json.dumps(body), json=lambda: body)

    def raw_json(*args, **kwargs):
        info_calls.append(args)
        return {"quoteResponse": {"result": [{"symbol": MISSING, "exchangeTimezoneName": "Asia/Taipei"}]}}

    monkeypatch.setattr("yfinance.cache.get_tz_cache", lambda: SimpleNamespace(
        lookup=lambda s: "Asia/Taipei" if warm[0] else None, store=lambda *a: None))
    monkeypatch.setattr(YfData, "get", get)
    monkeypatch.setattr(YfData, "cache_get", get)
    monkeypatch.setattr(YfData, "get_raw_json", raw_json)
    cold_rows = YFinanceForwardPriceProvider().fetch([MISSING], start_date=START, end_date=date(2026, 6, 2))
    warm[0] = True
    warm_rows = YFinanceForwardPriceProvider().fetch([MISSING], start_date=START, end_date=date(2026, 6, 2))
    assert cold_rows == warm_rows
    assert calls == ["timezone", "history", "history"]
    assert [r["date"] for r in cold_rows[MISSING]] == ["2026-06-01", "2026-06-02"]
    assert [r["close"] for r in cold_rows[MISSING]] == [100, 101]
    assert len(info_calls) == (2 if timezone_from_info else 0)
