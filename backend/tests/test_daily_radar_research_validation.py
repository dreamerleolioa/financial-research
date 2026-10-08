from datetime import date

import pandas as pd
import pytest

from ai_stock_sentinel.calibration import price_provider
from ai_stock_sentinel.daily_radar.research_validation import (
    RESEARCH_VALIDATION_VERSION, evaluate_research_window,
)


def price(day, open_=110, close=115, **extra):
    return {"date": day, "open": open_, "close": close,
            "high": max(open_, close) + 2, "low": min(open_, close) - 5,
            "price_basis": "unadjusted_price", "price_source": "yfinance",
            "corporate_actions_checked": True, "stock_splits": 0, "dividends": 0,
            **extra}


def evaluate(prices=None, benchmark=None, *, as_of=date(2026, 6, 9)):
    return evaluate_research_window(
        {"candidate_id": 1, "symbol": "2330.TW", "record_date": "2026-06-05",
         "input_snapshot": {"ohlcv": {"close": 50}}},
        price_series=prices if prices is not None else [
            price("2026-06-05", 100, 100), price("2026-06-08"), price("2026-06-09", 115, 121)],
        benchmark_prices=benchmark if benchmark is not None else [
            price("2026-06-05", 1000, 1000), price("2026-06-08", 1100, 1100),
            price("2026-06-09", 1100, 1122)],
        window_days=2, as_of_date=as_of, benchmark_symbol="TAIEX")


def test_next_open_excludes_signal_to_entry_gap_and_aligns_benchmark():
    row = evaluate()
    assert row["validation_version"] == RESEARCH_VALIDATION_VERSION
    assert row["status"] == "validated"
    assert row["target_date"] == "2026-06-09"
    data = row["outcome"]
    assert data["entry_date"] == "2026-06-08"
    assert data["entry_price"] == 110
    assert data["forward_return_pct"] == 10
    assert data["benchmark_return_pct"] == 2
    assert data["excess_return_vs_benchmark_pct"] == 8
    assert data["max_adverse_excursion_pct"] == pytest.approx(-4.5455)
    assert data["cost_scenarios"]["0.5"]["net_return_pct"] == 9.5
    assert data["cost_scenarios"]["1"]["net_excess_return_pct"] == 7
    assert data["dividends_included"] is False


@pytest.mark.parametrize("open_", [None, 0, float("nan")])
def test_real_entry_open_is_required(open_):
    prices = [price("2026-06-05"), price("2026-06-08", open=open_), price("2026-06-09")]
    assert evaluate(prices)["skip_reason"] == "missing_entry_open"


@pytest.mark.parametrize("change,reason", [
    ({"price_basis": "auto_adjust"}, "price_basis_mismatch"),
    ({"price_source": None}, "price_provenance_missing"),
    ({"corporate_actions_checked": False}, "corporate_action_provenance_missing"),
    ({"stock_splits": 2}, "stock_split_in_window"),
    ({"ohlc_conflict_fields": ["low"]}, "conflicting_price_records"),
    ({"low": None}, "invalid_research_ohlc"),
])
def test_new_basis_rejects_ambiguous_or_conflicting_data(change, reason):
    prices = [price("2026-06-05"), price("2026-06-08", **change), price("2026-06-09")]
    assert evaluate(prices)["skip_reason"] == reason


def test_cash_dividend_is_disclosed_but_not_added_to_price_return():
    prices = [price("2026-06-05"), price("2026-06-08", dividends=3), price("2026-06-09", 115, 121)]
    row = evaluate(prices)
    assert row["outcome"]["forward_return_pct"] == 10
    assert row["outcome"]["cash_dividends_excluded"] == 3


def test_immaturity_and_missing_prices_are_distinct_and_future_rows_are_ignored():
    row = evaluate(as_of=date(2026, 6, 8))
    assert row["status"] == "pending"
    assert row["skip_reason"] == "window_not_mature"
    row = evaluate([price("2026-06-05"), price("2026-06-09")])
    assert row["skip_reason"] == "missing_future_price"


def test_benchmark_requires_real_open_and_matching_price_basis():
    benchmark = [price("2026-06-05"), price("2026-06-08", open=None), price("2026-06-09")]
    assert evaluate(benchmark=benchmark)["skip_reason"] == "missing_benchmark_entry_open"
    benchmark[1] = price("2026-06-08", price_basis="auto_adjust")
    assert evaluate(benchmark=benchmark)["skip_reason"] == "price_basis_mismatch"


def test_research_provider_explicitly_requests_unadjusted_prices_and_preserves_missing_open(monkeypatch):
    calls = []
    def download(symbols, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame({"Open": [float("nan")], "High": [105], "Low": [95],
                             "Close": [100], "Dividends": [2], "Stock Splits": [0]},
                            index=pd.to_datetime(["2026-06-08"]))
    monkeypatch.setattr(price_provider.yf, "download", download)
    rows = price_provider.get_research_price_provider().fetch(
        ["2330.TW"], start_date=date(2026, 6, 5), end_date=date(2026, 6, 9))["2330.TW"]
    assert calls[0]["auto_adjust"] is False
    assert calls[0]["actions"] is True
    assert rows[0]["open"] is None
    assert rows[0]["price_basis"] == "unadjusted_price"
    assert rows[0]["corporate_actions_checked"] is True
    assert rows[0]["dividends"] == 2


def test_old_incomplete_benchmark_is_missing_data_rather_than_immature():
    row = evaluate(as_of=date(2026, 7, 1), benchmark=[price("2026-06-05"), price("2026-06-08")])
    assert row["status"] == "skipped"
    assert row["skip_reason"] == "missing_benchmark"


def test_api_keeps_legacy_results_and_research_retry_is_idempotent(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.daily_radar import maintenance_router
    from ai_stock_sentinel.calibration.price_provider import get_research_price_provider
    from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from ai_stock_sentinel.db.session import get_db
    from test_forward_validation_memory import engine_with_tables
    from test_daily_radar_forward_validation import _add_candidate, _add_run

    engine = engine_with_tables()
    with Session(engine) as session:
        candidate = _add_candidate(session, _add_run(session, run_date=date(2026, 6, 5)))
        candidate_id = candidate.id
        session.add(DailyRadarForwardValidationResult(
            candidate_id=candidate_id, window_days=2, validation_version=FORWARD_VALIDATION_VERSION,
            status="validated", signal_date=date(2026, 6, 5), target_date=date(2026, 6, 9),
            benchmark_symbol="TAIEX", evaluation_as_of_date=date(2026, 6, 9),
            outcome={"forward_return_pct": 999, "observation_diagnostic": {"status": "confirmed"}}))
        session.commit()
    class Provider:
        calls = []
        def fetch(self, symbols, *, start_date, end_date):
            self.calls.append(symbols)
            return {s: [price("2026-06-05", 100, 100), price("2026-06-08"),
                        price("2026-06-09", 115, 121)] for s in symbols}
    provider = Provider()
    app = FastAPI()
    app.include_router(maintenance_router.router)
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "research-test")
    app.dependency_overrides[get_db] = lambda: Session(engine)
    app.dependency_overrides[get_research_price_provider] = lambda: provider
    try:
        with TestClient(app) as client:
            args = dict(json={"mode": "due", "windows": [2], "as_of_date": "2026-06-09",
                              "return_basis": "next_open"},
                        headers={"Authorization": "Bearer research-test"})
            response = client.post("/internal/daily-radar/forward-validation/run", **args)
            assert response.status_code == 200, response.text
            assert response.json()["records_written"] == 1
            assert response.json()["report"]["validation_version"] == RESEARCH_VALIDATION_VERSION
            calls = list(provider.calls)
            response = client.post("/internal/daily-radar/forward-validation/run", **args)
            assert response.status_code == 200, response.text
            assert response.json()["records_written"] == 0
            assert provider.calls == calls
    finally:
        app.dependency_overrides.clear()
    with Session(engine) as session:
        rows = list(session.scalars(select(DailyRadarForwardValidationResult).order_by(
            DailyRadarForwardValidationResult.validation_version)))
        assert len(rows) == 2
        legacy = next(r for r in rows if r.validation_version == FORWARD_VALIDATION_VERSION)
        assert legacy.outcome["forward_return_pct"] == 999
        saved = next(r for r in rows if r.validation_version == RESEARCH_VALIDATION_VERSION)
        assert saved.outcome["entry_date"] == "2026-06-08"
        assert saved.outcome["cost_scenarios"]["0.5"]["net_return_pct"] == 9.5
    engine.dispose()
