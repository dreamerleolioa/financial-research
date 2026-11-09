"""Replay public 2026-11-04 OHLC, action reports and Yahoo chart data."""
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider, _mark_conflicts
from ai_stock_sentinel.daily_radar import research_validation
from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION, run_research_validation
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result
from test_daily_radar_forward_validation import _add_candidate, _add_run
from test_forward_validation_memory import engine_with_tables


TARGET = date(2026, 11, 4)
FIELDS = ("open", "high", "low", "close", "volume", "dividends", "stock_splits")
HALT_DAYS = ["2026-10-07", "2026-10-08", *(f"2026-10-{d}" for d in range(12, 17))]
CASES = [
    ("2885.TW", date(2026, 8, 5), 60, "stock_distribution_in_window", 8),
    ("3706.TW", date(2026, 8, 5), 60, "stock_distribution_in_window", 24),
    ("6173.TWO", date(2026, 10, 2), 20, "cash_reduction_halt_in_window", 2),
]


def _iso_roc(value):
    y, m, d = value.split("/")
    return str(date(int(y) + 1911, int(m), int(d)))


@pytest.fixture
def replay(monkeypatch):
    archive = json.loads((Path(__file__).parent / "fixtures/next_open_corporate_actions.json").read_text())
    engine = engine_with_tables()
    fault = set()
    calls = []

    def official_get(url, *, params, **kwargs):
        endpoint = url.rsplit("/", 1)[1]
        calls.append(endpoint)
        if endpoint in {"STOCK_DAY", "MI_5MINS_HIST", "tradingStock"}:
            month = date.fromisoformat(params["date"].replace("/", "-"))
            code = params.get("stockNo", params.get("code", "index"))
            key = f"tpex-{params['code']}" if endpoint == "tradingStock" else f"twse-{code}"
            result = archive[f"{key}-{month.month}"]
            table = result.get("tables", [result])[0]
            for reason, day in (("missing_index", "115/10/19"), ("missing_official_stock", "115/10/19")):
                if reason in fault and ((code == "index") == (reason == "missing_index")):
                    data = [row for row in table["data"] if row[0] != day]
                    table = table | {"data": data, ("totalCount" if "tables" in result else "total"): len(data)}
                    return result | ({"tables": [table]} if "tables" in result else table)
            return result
        if endpoint == "TWT49UDetail":
            return archive[f"twse-rights-{params['STK_NO']}"]
        if endpoint == "TWTAVUDetail":
            assert (params["STK_NO"], params["FILE_DATE"]) == ("6548", "20261006")
            return archive["twse-reduction-detail-example"]
        start = params["startDate"].replace("/", "")
        end = params["endDate"].replace("/", "")
        if "report_timeout" in fault:
            raise TimeoutError("test timeout")
        if endpoint == "TWT49U":
            report = archive["twse-rights"]
            rows = [row for row in report["data"] if start <=
                    f"{int(row[0][:3]) + 1911}{row[0][4:6]}{row[0][7:9]}" <= end]
            return report | {"strDate": start, "endDate": end, "data": rows}
        if endpoint in {"exDailyQ", "revivt"}:
            report = archive["tpex-reduction-record"]
            table = report["tables"][0]
            count = 999 if "truncated_report" in fault else len(table["data"])
            distributions = archive.get("tpex-distributions", [])
            return report | {"date": f"{start}~{end}",
                             "tables": [table | {"totalCount": count}] if endpoint == "revivt" else
                             [{"fields": ["除權息日期", "代號", "除權息前收盤價", "權/息", "現金股利",
                                          "每仟股無償配股", "現金增資股數", "現金增資認購價",
                                          "按持股比例仟股認購"],
                               "data": distributions, "totalCount": len(distributions)}]}
        assert endpoint in {"TWTAUU", "TWTB8U"}
        if endpoint == "TWTAUU":
            return {"stat": "OK", "strDate": start, "endDate": end,
                    "fields": ["恢復買賣日期", "股票代號", "停止買賣前收盤價格", "減資原因", "詳細資料"],
                    "data": archive.get("twse-reductions", [])}
        return {"stat": "OK", "strDate": start, "endDate": end,
                "fields": ["恢復買賣日期", "股票代號"], "data": []}

    def yahoo(symbols, *, start_date, end_date):
        output = {}
        for symbol in symbols:
            code = "index" if symbol == "TAIEX" else symbol.split(".")[0]
            rows = archive[f"yahoo-{code}"]
            rows = [dict(zip(("date", *FIELDS), row)) | {"price_source": "yfinance",
                    "price_basis": "unadjusted_price", "corporate_actions_checked": True}
                    for row in rows if str(start_date) <= row[0] <= str(end_date)]
            if symbol == "6173.TWO":
                if "missing_yahoo_stock" in fault:
                    rows = [row for row in rows if row["date"] != "2026-10-19"]
                if "nonzero_halt" in fault:
                    next(row for row in rows if row["date"] == HALT_DAYS[0])["volume"] = 1
                if "unexplained_quote" in fault:
                    next(row for row in rows if row["date"] == "2026-10-19")["high"] += 1
            output[symbol] = rows
        return output

    def mark_archived_conflicts(prices, **_):
        # Represent the existing daily final archive comparison without populating a second history store.
        result = {symbol: [dict(row) for row in series] for symbol, series in prices.items()}
        for symbol, series in result.items():
            if symbol == "TAIEX":
                continue
            code, market = symbol.split(".")
            prefix = "twse" if market == "TW" else "tpex"
            official = {_iso_roc(row[0]): {f: v for f, v in zip(FIELDS, row[3:7])}
                        for key, payload in archive.items() if key.startswith(f"{prefix}-{code}-")
                        for row in payload.get("tables", [payload])[0]["data"]}
            _mark_conflicts({row["date"]: row for row in series}, official)
        return result

    def run(symbol, signal, window, *, seed=False, extra_symbols=(), target=TARGET):
        if seed:
            with Session(engine) as db:
                saved_run = _add_run(db, run_date=signal)
                for ticker in (symbol, *extra_symbols):
                    candidate = _add_candidate(db, saved_run, symbol=ticker)
                    db.add(Result(candidate_id=candidate.id, window_days=window,
                                  validation_version=RESEARCH_VALIDATION_VERSION, status="skipped",
                                  signal_date=signal, benchmark_symbol="TAIEX", evaluation_as_of_date=target,
                                  skip_reason="conflicting_price_records", outcome={"expected_target_date": str(target)}))
                db.commit()
        provider = RecoveringResearchPriceProvider(official_getter=official_get)
        monkeypatch.setattr(provider, "fetch", yahoo)
        monkeypatch.setattr(provider, "repair", mark_archived_conflicts)
        request = DailyRadarForwardValidationRunRequest(return_basis="next_open", as_of_date=target,
                                                       start_date=signal, end_date=signal, windows=[window])
        with Session(engine) as db:
            result = run_research_validation(db, request, as_of_date=target, price_provider=provider)
            db.commit()
            return result

    yield archive, fault, engine, run, calls
    engine.dispose()


@pytest.mark.parametrize("symbol,signal,window,reason,adjusted_count", CASES)
def test_reviewed_windows_stop_retrying_with_official_evidence(replay, symbol, signal, window, reason, adjusted_count):
    _, _, engine, run, calls = replay
    result = run(symbol, signal, window, seed=True)
    assert result["validated_count"] == 0
    assert result["terminal_skipped_count"] == 1
    assert result["report"]["remaining_retryable_window_count"] == 0
    with Session(engine) as db:
        row = db.scalars(select(Result)).one()
        assert row.status == "skipped"
        assert row.skip_reason == reason
        assert row.outcome["expected_target_date"] == str(TARGET)
        evidence = row.outcome["official_corporate_action"]
        assert len(evidence["yahoo_adjusted_dates"]) == adjusted_count
        assert evidence["halt_dates"] == (HALT_DAYS if symbol.endswith(".TWO") else [])
        assert "2026-10-23" not in evidence["session_dates"]
        assert evidence["sources"] and all(len(s["snapshot_sha256"]) == 64 for s in evidence["sources"])
        from types import SimpleNamespace
        from ai_stock_sentinel.daily_radar.readiness import classify_window
        calendar = SimpleNamespace(through=TARGET, sessions=tuple([signal] + [
            date.fromisoformat(d) for d in evidence['session_dates']]))
        state, _, _ = classify_window({'symbol': symbol, 'record_date': str(signal),
            'data_dates': {'ohlcv': str(signal)}}, {'status': row.status, 'skip_reason': row.skip_reason,
            'target_date': str(row.target_date) if row.target_date else None,
            'evaluation_as_of_date': str(row.evaluation_as_of_date), 'outcome': row.outcome},
            basis='next_open', window=window, as_of_date=TARGET, calendar=calendar)
        assert state == 'terminal'
    before = calls[:]
    assert run(symbol, signal, window)["records_written"] == 0
    assert calls == before


@pytest.mark.parametrize("defect", [
    "missing_official_stock", "missing_yahoo_stock", "missing_index", "unexplained_quote",
    "nonzero_halt", "report_timeout", "truncated_report",
])
def test_6173_keeps_retryable_with_unexplained_or_unavailable_evidence(replay, defect):
    _, fault, engine, run, _ = replay
    fault.add(defect)
    result = run("6173.TWO", date(2026, 10, 2), 20, seed=True)
    assert result["terminal_skipped_count"] == 0
    assert result["report"]["remaining_retryable_window_count"] == 1
    with Session(engine) as db:
        row = db.scalars(select(Result)).one()
        assert row.skip_reason == "conflicting_price_records"
        assert "official_corporate_action" not in row.outcome


def test_cash_reduction_works_for_another_symbol_and_window(replay):
    archive, _, engine, run, _ = replay
    # Independent symbol, signal and window, with the same market report schema.
    for month in (10, 11):
        source = archive[f"tpex-6173-{month}"]
        archive[f"tpex-6548-{month}"] = source | {"code": "6548"}
    archive["yahoo-6548"] = archive["yahoo-6173"]
    table = archive["tpex-reduction-record"]["tables"][0]
    table["data"] = [[str(value).replace("6173", "6548") for value in row] for row in table["data"]]
    result = run("6548.TWO", date(2026, 10, 5), 19, seed=True)
    assert result["terminal_skipped_count"] == 1
    assert result["report"]["remaining_retryable_window_count"] == 0
    with Session(engine) as db:
        assert db.scalars(select(Result)).one().outcome["official_corporate_action"]["event"]["symbol"] == "6548.TWO"


def test_listed_cash_reduction_uses_twse_detail_identity_and_terms(replay):
    archive, _, engine, run, _ = replay
    for month in (10, 11):
        source = archive[f"tpex-6173-{month}"]
        rows = source["tables"][0]["data"]
        archive[f"twse-6548-{month}"] = {
            "date": source["date"], "title": "6548 月行情", "stat": "OK",
            "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
            "data": rows,
        }
    archive["yahoo-6548"] = archive["yahoo-6173"]
    archive["twse-reductions"] = [["115/10/19", "6548", "284.00", "退還股款", "6548  ,20261006"]]
    detail = archive["twse-reduction-detail-example"]
    detail["data"][0][:5] = ["6548", "測試個股", "115/10/07", "940 股", "0.6 元/股"]
    assert run("6548.TW", date(2026, 10, 5), 19, seed=True)["terminal_skipped_count"] == 1
    with Session(engine) as db:
        proof = db.scalars(select(Result)).one().outcome["official_corporate_action"]
        assert proof["halt_dates"] == HALT_DAYS
        assert any("TWTAVUDetail" in s["source_url"] for s in proof["sources"])


def test_tpex_distribution_and_unrelated_real_gap_in_the_same_batch(replay):
    archive, _, engine, run, _ = replay
    for month in (8, 9, 10, 11):
        source = archive[f"twse-2885-{month}"]
        fields = ["日 期", "成交張數", "成交仟元", "開盤", "最高", "最低", "收盤"]
        for code in ("6548", "8044"):
            archive[f"tpex-{code}-{month}"] = {
                "date": source["date"], "code": code, "stat": "ok",
                "tables": [{"fields": fields, "data": [row[:7] for row in source["data"]],
                            "totalCount": len(source["data"])}],
            }
    archive["tpex-reduction-record"]["tables"][0]["data"] = []
    archive["tpex-distributions"] = [["115/08/18", "6548", "68.30", "除權", "0", "40", "0", "0", "0"]]
    archive["yahoo-6548"] = archive["yahoo-2885"]
    archive["yahoo-8044"] = [
        [row[0], *(price * 1.04 if row[0] < "2026-08-18" else price for price in row[1:5]), row[5], 0, 0]
        for row in archive["yahoo-2885"] if row[0] != "2026-11-04"
    ]
    result = run("6548.TWO", date(2026, 8, 5), 60, seed=True, extra_symbols=("8044.TWO",))
    assert result["terminal_skipped_count"] == 1
    assert result["retryable_skipped_count"] == 1
    assert result["report"]["remaining_retryable_window_count"] == 1
    assert result["report"]["current_run_gaps"][0]["missing_dates"] == ["2026-11-04"]
    with Session(engine) as db:
        assert {row.candidate.symbol: row.skip_reason for row in db.scalars(select(Result))} == {
            "6548.TWO": "stock_distribution_in_window", "8044.TWO": "missing_future_price",
        }


def test_stale_in_flight_evaluation_does_not_reopen_proven_terminal_result(replay):
    _, _, engine, run, _ = replay
    assert run("6173.TWO", date(2026, 10, 2), 20, seed=True)["terminal_skipped_count"] == 1
    with Session(engine) as db:
        row = db.scalars(select(Result)).one()
        result = upsert_forward_validation_results(db, [{
            "candidate_id": row.candidate_id, "symbol": "6173.TWO", "window_days": 20,
            "signal_date": "2026-10-02", "benchmark_symbol": "TAIEX",
            "validation_version": RESEARCH_VALIDATION_VERSION, "evaluation_as_of_date": TARGET,
            "status": "skipped", "skip_reason": "conflicting_price_records", "outcome": {},
        }])
        assert result["records_written"] == 0
        assert row.skip_reason == "cash_reduction_halt_in_window"


def test_official_calendar_ignores_a_holiday_in_the_prepared_calendar(replay, monkeypatch):
    _, _, engine, run, _ = replay
    monkeypatch.setattr(research_validation, "load_benchmark_calendar_from_prepared_market_context",
                        lambda *args, **kwargs: [date(2026, 10, 23)])
    result = run("6173.TWO", date(2026, 10, 2), 20, seed=True)
    assert result["terminal_skipped_count"] == 1
    with Session(engine) as db:
        outcome = db.scalars(select(Result)).one().outcome
        assert outcome["expected_target_date"] == "2026-11-04"
        assert outcome["missing_dates"] == []
        assert outcome["official_corporate_action"]["prior_expected_target_date"] == "2026-11-03"
        assert "2026-10-23" not in outcome["official_corporate_action"]["session_dates"]
