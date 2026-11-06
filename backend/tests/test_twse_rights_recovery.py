from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.price_provider import get_research_price_provider
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.calibration.research_prices import _mark_conflicts
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
SUBSCRIPTIONS = {"1727.TW": (81, 86.23682261), "2890.TW": (35.15, 34.61290969),
                 "8021.TW": (388, 21.72000885)}
OTHER = "2330.TW"
BODY = {"return_basis": "next_open", "as_of_date": str(TARGET), "windows": [20],
        "start_date": str(SIGNAL), "end_date": str(SIGNAL), "conflict_recovery_symbols": list(TERMS)}


@pytest.fixture
def scenario(monkeypatch):
    engine = engine_with_tables()
    with Session(engine) as db:
        run = _add_run(db, run_date=SIGNAL)
        ids = {}
        for symbol, window in [(s, 20) for s in TERMS] + [(OTHER, 20), ("1727.TW", 10)]:
            if symbol not in ids:
                ids[symbol] = _add_candidate(db, run, symbol=symbol).id
            db.add(Result(candidate_id=ids[symbol], window_days=window,
                validation_version=RESEARCH_VALIDATION_VERSION, status="skipped", signal_date=SIGNAL,
                benchmark_symbol="TAIEX", evaluation_as_of_date=date(2026, 11, 4),
                skip_reason="conflicting_price_records", outcome={"expected_target_date": str(TARGET)}))
        db.commit()

    prices, quotes = {}, {}
    terms = TERMS | {OTHER: (date(2026, 10, 7), 100, 0.99, 110, 120)}
    subscriptions = SUBSCRIPTIONS | {OTHER: (79, 50)}
    faults = set()
    for symbol, (ex, prev, factor, entry, exit_) in terms.items():
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
    quotes["TAIEX"] = [row | {"price_source": "yfinance", "price_basis": "unadjusted_price",
                      "corporate_actions_checked": True, "dividends": 0, "stock_splits": 0}
                      for row in prices["TAIEX"]]
    calls = []

    def action_report(url, *, params, **kwargs):
        name = url.rsplit("/", 1)[1]
        if name in ("STOCK_DAY", "MI_5MINS_HIST"):
            month = date.fromisoformat(params["date"])
            symbol = params["stockNo"] + ".TW" if name == "STOCK_DAY" else "TAIEX"
            calls.append(("TWSE", symbol, month))
            rows = []
            for bar in prices[symbol]:
                day = date.fromisoformat(bar["date"])
                if day.month != month.month:
                    continue
                roc = f"{day.year - 1911}/{day:%m/%d}"
                values = [str(bar[field]) for field in FIELDS]
                rows.append([roc, *values, "10000", "1000000"] if symbol != "TAIEX" else [roc, *values])
            fields = (["日期", "開盤價", "最高價", "最低價", "收盤價", "成交股數", "成交金額"]
                      if symbol != "TAIEX" else ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"])
            return {"stat": "OK", "date": params["date"], "title": f"{params.get('stockNo', 'TAIEX')} 月行情",
                    "fields": fields, "data": rows}
        calls.append((name, dict(params)))
        if name == "TWT49UDetail":
            symbol = params["STK_NO"] + ".TW"
            subscribe, rate = subscriptions[symbol]
            fields = ["股票代號", "(每股配發現金股利)除息", "A. 按普通股股東持股比例每千股無償配股",
                      "B. 員工紅利轉增資", "C. (有償) 現金增資", "每股認購金額", "按股東持股比例每千股認購"]
            return {"stat": "ok", "fields": fields,
                    "data": [[params["STK_NO"], "0 元／股", "0 股", "0 股", "10,000 股", f"{subscribe} 元／股", f"{rate} 股"]]}
        start, end = params["startDate"], params["endDate"]
        if name == "TWT49U":
            rows = []
            for symbol, (ex, prev, *_) in terms.items():
                if symbol == "1727.TW" and "missing_announcement" in faults:
                    continue
                if start <= ex.strftime("%Y%m%d") <= end:
                    code = symbol.split(".")[0]
                    rows.append([f"{ex.year - 1911}年{ex:%m月%d日}", code, str(prev), "權", f"{code},{ex:%Y%m%d}"])
            return {"stat": "OK", "strDate": "20261001" if "unverified_range" in faults else start, "endDate": end,
                    "fields": ["資料日期", "股票代號", "除權息前收盤價", "權/息", "詳細資料"], "data": rows}
        if name == "TWTAUU":
            rows = [["115/10/06", "1727"]] if "capital_reduction" in faults else []
            return {"stat": "OK", "strDate": start, "endDate": end, "fields": ["恢復買賣日期", "股票代號"], "data": rows}
        assert name == "TWTB8U"
        return {"stat": "OK", "params": params, "fields": ["恢復買賣日期", "股票代號"], "data": []}

    provider = RecoveringResearchPriceProvider(official_getter=action_report)

    def yahoo(symbols, *, start_date, end_date):
        calls.append(("Yahoo", tuple(symbols), start_date, end_date))
        return {symbol: quotes[symbol] for symbol in symbols}

    def mark_prices(rows, **_):
        result = {symbol: [dict(row) for row in series] for symbol, series in rows.items()}
        for symbol, series in result.items():
            _mark_conflicts({r["date"]: r for r in series}, {r["date"]: r for r in prices[symbol]})
        return result

    monkeypatch.setattr(provider, "fetch", yahoo)
    monkeypatch.setattr(provider, "repair", mark_prices)
    app = FastAPI()
    app.include_router(maintenance_router.router)
    app.dependency_overrides[get_db] = lambda: Session(engine)
    from tests.radar_calendar_support import FixtureCalendarProvider
    from ai_stock_sentinel.daily_radar.trading_calendar import get_trading_calendar_provider
    day_set = set(DAYS)
    closed = {SIGNAL+timedelta(days=i) for i in range((TARGET-SIGNAL).days+1)} - day_set
    app.dependency_overrides[get_trading_calendar_provider] = lambda: FixtureCalendarProvider(closed)
    app.dependency_overrides[get_research_price_provider] = lambda: provider
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "rights-test")
    with TestClient(app) as client:
        def run(**overrides):
            return client.post("/internal/daily-radar/forward-validation/run",
                               json=BODY | overrides, headers={"Authorization": "Bearer rights-test"})
        yield engine, run, prices, quotes, calls, faults
    engine.dispose()


def test_recovers_only_three_persisted_windows_with_official_price_and_event_provenance(scenario):
    engine, run, _, _, calls, _ = scenario
    response = run()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["records_written"] == body["validated_count"] == 3
    assert body["report"]["remaining_retryable_window_count"] == 0
    assert body["report"]["conflict_recovery"]["ready"] is True
    assert len([call for call in calls if call[0] == "TWSE"]) == 8
    assert [call[0] for call in calls].count("TWT49U") == 1
    assert [call[0] for call in calls].count("TWT49UDetail") == 3
    assert ("Yahoo", tuple(sorted(TERMS)), SIGNAL, TARGET) in calls
    original_calls = calls[:]
    repeated = run().json()
    assert repeated["records_written"] == 0
    assert repeated["report"]["conflict_recovery"]["ready"] is True
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
            evidence = row.outcome["official_rights_recovery"]
            assert evidence["event"]["type"] == "cash_rights_issue"
            assert evidence["event"]["ex_date"] == str(TERMS[symbol][0])
            assert evidence["event"]["source_url"].startswith("https://www.twse.com.tw/")
            captured = datetime.fromisoformat(evidence["event"]["captured_at"])
            assert captured.utcoffset() is not None
            assert len(evidence["event"]["snapshot_sha256"]) == 64
            assert all(source["captured_at"] and source["snapshot_sha256"]
                       for source in evidence["window_action_sources"])
            assert len(evidence["session_dates"]) == 20
            assert evidence["yahoo_adjusted_dates"]


def test_generic_writer_without_symbol_cannot_replace_reviewed_official_result(scenario):
    engine, run, _, _, _, _ = scenario
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


@pytest.mark.parametrize("defect", ["stock_gap", "benchmark_gap", "unexplained_price", "unexpected_split",
                                    "missing_announcement", "unverified_range", "capital_reduction"])
def test_fails_closed_without_overwriting_the_conflict(scenario, defect):
    engine, run, prices, quotes, _, faults = scenario
    if defect == "stock_gap":
        prices["1727.TW"].pop(3)
    elif defect == "benchmark_gap":
        prices["TAIEX"].pop(3)
    elif defect == "unexplained_price":
        quotes["1727.TW"][4]["high"] += 1
    elif defect == "unexpected_split":
        quotes["1727.TW"][4]["stock_splits"] = 2
    else:
        faults.add(defect)
    response = run()
    assert response.status_code == 200, response.text
    body = response.json()
    expected_pending = 3 if defect in ("benchmark_gap", "unverified_range") else 1
    assert body["validated_count"] == 3 - expected_pending
    assert body["report"]["remaining_retryable_window_count"] == expected_pending
    assert body["report"]["source_recovery_summary"]["error_count"] > 0
    assert body["report"]["conflict_recovery"]["ready"] is False
    with Session(engine) as db:
        skipped = [row for row in db.scalars(select(Result)).all() if row.status == "skipped"]
        assert len(skipped) == expected_pending + 2
        assert all(row.skip_reason == "conflicting_price_records" for row in skipped)


def test_missing_target_conflict_is_not_recreated_or_reported_ready(scenario):
    engine, run, _, _, _, _ = scenario
    with Session(engine) as db:
        row = next(row for row in db.scalars(select(Result))
                   if row.candidate.symbol == "1727.TW" and row.window_days == 20)
        row.outcome = {"expected_target_date": "2026-11-04"}
        db.commit()
    body = run().json()
    assert body["validated_count"] == 2
    assert body["report"]["remaining_retryable_window_count"] == 0
    assert body["report"]["conflict_recovery"]["ready"] is False
    assert body["report"]["conflict_recovery"]["unverified_symbols"] == ["1727.TW"]


def test_normal_due_flow_recovers_another_symbol_using_its_official_announcement(scenario):
    engine, run, _, _, _, _ = scenario
    result = run(conflict_recovery_symbols=None).json()
    assert result["validated_count"] == 4
    with Session(engine) as db:
        row = next(r for r in db.scalars(select(Result)) if r.candidate.symbol == OTHER)
        assert row.status == "validated"
        assert row.outcome["official_rights_recovery"]["event"]["symbol"] == OTHER


def test_scope_works_for_a_different_target_and_window_size(scenario):
    engine, run, _, quotes, _, _ = scenario
    target = DAYS[10]
    for symbol, series in quotes.items():
        quotes[symbol] = [row for row in series if row["date"] <= str(target)]
    with Session(engine) as db:
        prior = db.scalar(select(Result).where(Result.window_days == 10))
        prior.outcome = {"expected_target_date": str(target)}
        db.commit()
    result = run(as_of_date=str(target), windows=[10], conflict_recovery_symbols=["1727.TW"]).json()
    assert result["validated_count"] == 1
    assert result["report"]["conflict_recovery"]["ready"]
    assert result["report"]["conflict_recovery"]["verified_windows"][0]["target_date"] == str(target)
    with Session(engine) as db:
        assert all(r.status == "skipped" for r in db.scalars(select(Result).where(Result.window_days == 20)))


@pytest.mark.parametrize("overrides", [
    {"as_of_date": None}, {"as_of_date": "2026-09-30"}, {"market": "US"},
    {"mode": "range"}, {"windows": [5, 20]}, {"return_basis": "signal_close"},
    {"benchmark_symbol": "SPX"}, {"start_date": "2026-10-02"},
    {"conflict_recovery_symbols": ["1727.TW", "1727.TW"]}, {"reviewed_rights_recovery": True},
])
def test_reviewed_recovery_rejects_out_of_scope_requests(scenario, overrides):
    _, run, _, _, calls, _ = scenario
    assert run(**overrides).status_code == 422
    assert calls == []
