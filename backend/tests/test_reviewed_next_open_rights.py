from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.price_provider import get_research_price_provider
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar import maintenance_router
from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result
from ai_stock_sentinel.db.session import get_db
from test_daily_radar_forward_validation import _add_candidate, _add_run
from test_forward_validation_memory import engine_with_tables


SIGNAL = date(2026, 10, 1)
TARGET = date(2026, 11, 3)
DAYS = [date(2026, 10, day) for day in (1, 2, 5, 6, 7, 8, 12, 13, 14, 15, 16, 19, 20, 21, 22, 27, 28, 29, 30)]
DAYS += [date(2026, 11, 2), TARGET]
FIELDS = ("open", "high", "low", "close")
TERMS = {
    "1727.TW": (date(2026, 10, 13), 103.50, 0.9827412080, 111, 83),
    "2890.TW": (date(2026, 10, 7), 45.40, 0.9924468479, 43.05, 44.40),
    "8021.TW": (date(2026, 10, 6), 560.0, 0.9934706715, 503, 382.5),
}
BODY = {"return_basis": "next_open", "as_of_date": str(TARGET), "windows": [20],
        "reviewed_rights_recovery": True}


@pytest.fixture
def scenario(monkeypatch):
    engine = engine_with_tables()
    with Session(engine) as db:
        run = _add_run(db, run_date=SIGNAL)
        ids = {}
        for symbol, window in [(s, 20) for s in TERMS] + [("2330.TW", 20), ("1727.TW", 10)]:
            if symbol not in ids:
                ids[symbol] = _add_candidate(db, run, symbol=symbol).id
            db.add(Result(candidate_id=ids[symbol], window_days=window,
                validation_version=RESEARCH_VALIDATION_VERSION, status="skipped", signal_date=SIGNAL,
                benchmark_symbol="TAIEX", evaluation_as_of_date=date(2026, 11, 4),
                skip_reason="conflicting_price_records", outcome={"expected_target_date": str(TARGET)}))
        db.commit()

    prices, quotes = {}, {}
    for symbol, (ex, prev, factor, entry, exit_) in TERMS.items():
        previous = DAYS[DAYS.index(ex) - 1]
        prices[symbol] = []
        quotes[symbol] = []
        for day in DAYS:
            open_ = entry if day == DAYS[1] else prev
            close = exit_ if day == TARGET else prev
            bar = dict(date=str(day), open=open_, high=max(open_, close) + 1,
                       low=min(open_, close) - 1, close=close, price_source="twse_stock_day")
            prices[symbol].append(bar)
            scale = factor if SIGNAL < day <= previous and (symbol != "1727.TW" or day != DAYS[1]) else 1
            quotes[symbol].append(bar | {f: bar[f] * scale for f in FIELDS}
                | {"price_source": "yfinance", "dividends": 0, "stock_splits": 0})
    prices["TAIEX"] = [dict(date=str(day), open=1000, high=1030, low=980, close=1010,
                             price_source="twse_index_history") for day in DAYS]
    calls = []
    provider = RecoveringResearchPriceProvider()

    def official_month(symbol, month):
        calls.append(("TWSE", symbol, month))
        return [row for row in prices[symbol] if date.fromisoformat(row["date"]).month == month.month]

    def yahoo(symbols, *, start_date, end_date):
        calls.append(("Yahoo", tuple(symbols), start_date, end_date))
        return {symbol: quotes[symbol] for symbol in symbols}

    monkeypatch.setattr(provider, "_official_month", official_month)
    monkeypatch.setattr(provider, "fetch", yahoo)
    app = FastAPI()
    app.include_router(maintenance_router.router)
    app.dependency_overrides[get_db] = lambda: Session(engine)
    app.dependency_overrides[get_research_price_provider] = lambda: provider
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "rights-test")
    with TestClient(app) as client:
        def run(**overrides):
            return client.post("/internal/daily-radar/forward-validation/run",
                               json=BODY | overrides, headers={"Authorization": "Bearer rights-test"})
        yield engine, run, prices, quotes, calls
    engine.dispose()


def test_recovers_only_three_persisted_windows_with_official_price_and_event_provenance(scenario):
    engine, run, _, _, calls = scenario
    response = run()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["records_written"] == body["validated_count"] == 3
    assert body["report"]["remaining_retryable_window_count"] == 0
    assert body["report"]["reviewed_rights_recovery"]["ready"] is True
    assert len([call for call in calls if call[0] == "TWSE"]) == 8
    assert ("Yahoo", tuple(sorted(TERMS)), SIGNAL, TARGET) in calls
    original_calls = calls[:]
    repeated = run().json()
    assert repeated["records_written"] == 0
    assert repeated["report"]["reviewed_rights_recovery"]["ready"] is True
    assert calls == original_calls
    with Session(engine) as db:
        records = db.scalars(select(Result)).all()
        assert len(records) == 5
        recovered = [row for row in records if row.status == "validated"]
        assert {row.candidate.symbol for row in recovered} == set(TERMS)
        for row in recovered:
            symbol = row.candidate.symbol
            assert row.evaluation_as_of_date == row.target_date == TARGET
            assert row.window_days == 20
            assert row.outcome["entry_price"] == TERMS[symbol][3]
            assert row.outcome["target_price"] == TERMS[symbol][4]
            assert row.outcome["price_sources"] == ["twse_stock_day"]
            assert row.outcome["benchmark_price_sources"] == ["twse_index_history"]
            evidence = row.outcome["reviewed_rights_recovery"]
            assert evidence["event"]["type"] == "cash_rights_issue"
            assert evidence["event"]["ex_date"] == str(TERMS[symbol][0])
            assert evidence["event"]["source_url"].startswith("https://www.twse.com.tw/")
            captured = datetime.fromisoformat(evidence["event"]["captured_at"])
            assert captured.astimezone(timezone.utc).date() == date(2026, 11, 4)
            assert len(evidence["event"]["snapshot_sha256"]) == 64
            assert all(source["captured_at"] and source["snapshot_sha256"]
                       for source in evidence["window_action_sources"])
            assert len(evidence["session_dates"]) == 20
            assert evidence["yahoo_adjusted_dates"]


def test_generic_writer_without_symbol_cannot_replace_reviewed_official_result(scenario):
    engine, run, _, _, _ = scenario
    assert run().json()["validated_count"] == 3
    with Session(engine) as db:
        row = db.scalars(select(Result).where(Result.status == "validated")).first()
        late = {"candidate_id": row.candidate_id,
                "window_days": 20, "signal_date": str(SIGNAL), "benchmark_symbol": "TAIEX",
                "validation_version": RESEARCH_VALIDATION_VERSION, "status": "skipped",
                "skip_reason": "conflicting_price_records", "evaluation_as_of_date": "2026-11-04"}
        assert upsert_forward_validation_results(db, [late])["records_written"] == 0
        assert upsert_forward_validation_results(db, [late | {"symbol": "2330.TW"}])["records_written"] == 0
        assert row.status == "validated"


@pytest.mark.parametrize("defect", ["stock_gap", "benchmark_gap", "unexplained_price", "unexpected_split"])
def test_fails_closed_without_overwriting_the_conflict(scenario, defect):
    engine, run, prices, quotes, _ = scenario
    if defect == "stock_gap":
        prices["1727.TW"].pop(3)
    elif defect == "benchmark_gap":
        prices["TAIEX"].pop(3)
    elif defect == "unexplained_price":
        quotes["1727.TW"][4]["high"] += 1
    else:
        quotes["1727.TW"][4]["stock_splits"] = 2
    response = run()
    assert response.status_code == 200, response.text
    body = response.json()
    expected_pending = 3 if defect == "benchmark_gap" else 1
    assert body["validated_count"] == 3 - expected_pending
    assert body["report"]["remaining_retryable_window_count"] == expected_pending
    assert body["report"]["source_recovery_summary"]["error_count"] > 0
    assert body["report"]["reviewed_rights_recovery"]["ready"] is False
    with Session(engine) as db:
        skipped = [row for row in db.scalars(select(Result)).all() if row.status == "skipped"]
        assert len(skipped) == expected_pending + 2
        assert all(row.skip_reason == "conflicting_price_records" for row in skipped)


def test_missing_target_conflict_is_not_recreated_or_reported_ready(scenario):
    engine, run, _, _, _ = scenario
    with Session(engine) as db:
        row = next(row for row in db.scalars(select(Result))
                   if row.candidate.symbol == "1727.TW" and row.window_days == 20)
        row.outcome = {"expected_target_date": "2026-11-04"}
        db.commit()
    body = run().json()
    assert body["validated_count"] == 2
    assert body["report"]["remaining_retryable_window_count"] == 0
    assert body["report"]["reviewed_rights_recovery"]["ready"] is False
    assert body["report"]["reviewed_rights_recovery"]["unverified_symbols"] == ["1727.TW"]


@pytest.mark.parametrize("overrides", [
    {"as_of_date": None}, {"as_of_date": "2026-11-04"}, {"market": "US"},
    {"mode": "range"}, {"windows": [5, 20]}, {"return_basis": "signal_close"},
    {"benchmark_symbol": "SPX"}, {"start_date": "2026-10-02"},
])
def test_reviewed_recovery_rejects_out_of_scope_requests(scenario, overrides):
    _, run, _, _, calls = scenario
    assert run(**overrides).status_code == 422
    assert calls == []
