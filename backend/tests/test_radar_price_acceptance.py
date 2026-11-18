from datetime import date
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from test_radar_jobs import db

from ai_stock_sentinel.daily_radar.forward_validation import build_forward_validation_report
from ai_stock_sentinel.daily_radar.price_acceptance import (
    OfficialPriceEvidence, PRICE_EVIDENCE, accept_next_open_prices, accept_signal_close_prices,
    verify_price_lock, verify_signal_bars,
)
from ai_stock_sentinel.daily_radar.research_validation import evaluate_research_window


SIGNAL = date(2026, 6, 3)
TARGET = date(2026, 6, 4)
DAYS = [SIGNAL, TARGET]
CANDIDATE = {
    "candidate_id": 1, "symbol": "5880.TW", "record_date": str(SIGNAL),
    "data_dates": {"ohlcv": str(SIGNAL)},
    "input_snapshot": {"ohlcv": {"close": 100.0}},
}


def bar(day, price, source="twse_test"):
    return {"date": str(day), "open": price, "high": price, "low": price,
            "close": price, "price_source": source}


def official(prices, corporate_events=()):
    return OfficialPriceEvidence(
        {str(row["date"]): row for row in prices},
        {str(day): bar(day, 10000) for day in DAYS},
        list(corporate_events),
    )


def test_signal_return_rejects_same_date_conflict_before_marking_return_validated():
    prices = [bar(SIGNAL, 100.0), bar(TARGET, 95.0) | {"ohlc_conflict_fields": ["close"]}]
    outcome = build_forward_validation_report(
        [CANDIDATE], price_series_by_symbol={"5880.TW": prices},
        benchmark_prices=[bar(day, 10000) for day in DAYS],
        market="TW", sample_source="test", as_of_date=TARGET, windows=[1], trading_calendar=DAYS,
    ).outcomes[0]
    assert outcome["status"] == "skipped"
    assert outcome["skip_reason"] == "conflicting_price_records"
    assert "forward_return_pct" not in outcome["outcome"]


def test_signal_rebases_cash_dividend_with_verified_events_and_locks_calculation_prices():
    stock = [bar(SIGNAL, 100.0), bar(TARGET, 98.0)]
    adjusted = [bar(SIGNAL, 0.98 * 100.0, "yfinance"), bar(TARGET, 98.0, "yfinance")]
    event = {"family": "rights", "effective_date": str(TARGET), "cash_dividend": 2.0}
    accepted = accept_signal_close_prices(CANDIDATE, DAYS, adjusted, official(stock, [event]))
    assert accepted.stock[-1]["close"] == 100.0
    assert accepted.proof == accept_signal_close_prices(CANDIDATE, DAYS, adjusted, official(stock, [event])).proof
    assert accepted.proof["stock"][-1]["close"] == 100.0


def test_signal_rejects_reported_factor_change_without_official_event():
    stock = [bar(SIGNAL, 100.0), bar(TARGET, 98.0)]
    adjusted = [bar(SIGNAL, 98.0, "yfinance"), bar(TARGET, 98.0, "yfinance")]
    accepted = accept_signal_close_prices(CANDIDATE, DAYS, adjusted, official(stock))
    assert accepted.reason == "adjustment_factor_unverified"


def test_signal_never_overwrites_saved_entry_or_support_for_a_different_official_close():
    stock = [bar(SIGNAL, 101.0), bar(TARGET, 98.0)]
    accepted = accept_signal_close_prices(CANDIDATE, DAYS, stock, official(stock))
    assert accepted.reason == "signal_price_anchor_mismatch"


def test_next_open_uses_official_quotes_instead_of_yahoo_split_scaled_history():
    stock = [bar(SIGNAL, 100.0), bar(TARGET, 101.0)]
    yahoo = [bar(SIGNAL, 50.0, "yfinance"), bar(TARGET, 50.5, "yfinance")]
    accepted = accept_next_open_prices(CANDIDATE, DAYS, yahoo, official(stock))
    row = evaluate_research_window(
        CANDIDATE, price_series=accepted.stock, benchmark_prices=accepted.benchmark,
        window_days=1, as_of_date=TARGET, benchmark_symbol="TAIEX",
        trading_calendar=DAYS, validate_signal_session=True,
    )
    assert row["status"] == "validated"
    assert row["outcome"]["entry_price"] == 101.0
    assert accepted.proof["stock"][-1]["open"] == 101.0
    locked = accepted.with_proof(row)
    assert locked["outcome"][PRICE_EVIDENCE]["sha256"] == accepted.proof["sha256"]
    verify_price_lock(locked, symbol="5880.TW", basis="next_open")
    changed = deepcopy(locked)
    changed["outcome"][PRICE_EVIDENCE]["stock"][-1]["open"] += 1
    with pytest.raises(ValueError, match="invalid_validation_price_lock"):
        verify_price_lock(changed, symbol="5880.TW", basis="next_open")


def test_next_open_requires_proven_halt_instead_of_treating_incomplete_quotes_as_clean():
    accepted = accept_next_open_prices(CANDIDATE, DAYS, [], official([bar(SIGNAL, 100.0)], [
        {"family": "reduction", "effective_date": str(TARGET)},
    ]))
    assert accepted.reason == "official_corporate_action_in_window"


def test_unrecognized_official_corporate_action_cannot_fall_through_to_legacy_return():
    from ai_stock_sentinel.daily_radar.research_validation import _evaluate_locked_next_open

    stock = [bar(SIGNAL, 100.0), bar(TARGET, 50.0)]
    yahoo = [p | {"price_basis": "unadjusted_price", "corporate_actions_checked": True,
                  "dividends": 0, "stock_splits": 0} for p in stock]
    source = official(stock, [{"family": "face_value", "effective_date": str(TARGET)}])
    result = _evaluate_locked_next_open(
        CANDIDATE, DAYS, yahoo=yahoo, benchmark=list(source.benchmark.values()), official=source,
        as_of_date=TARGET, benchmark_symbol="TAIEX", calendar=SimpleNamespace(sessions=DAYS), provider=object(),
    )
    assert result["status"] == "skipped" and result["skip_reason"] == "official_corporate_action_in_window"
    assert "forward_return_pct" not in result["outcome"]


def test_next_open_refuses_a_non_proportional_mismatch_between_sources():
    stock = [bar(SIGNAL, 100.0), bar(TARGET, 101.0)]
    yahoo = [bar(SIGNAL, 100.0, "yfinance"), bar(TARGET, 101.0, "yfinance") | {"open": 90.0}]
    accepted = accept_next_open_prices(CANDIDATE, DAYS, yahoo, official(stock))
    assert accepted.reason == "conflicting_price_records"


def test_publication_does_not_accept_correct_trade_date_with_wrong_close(monkeypatch):
    from ai_stock_sentinel.daily_radar import market_bar_repository
    archived = SimpleNamespace(symbol="5880.TW", source_dataset="TWSE_MI_INDEX",
                               open=100, high=105, low=95, close=102)
    monkeypatch.setattr(market_bar_repository, "get_taiwan_daily_bars", lambda *args, **kw: [archived])
    technical = {"data_dates": {"ohlcv": str(SIGNAL)}, "ohlcv": {
        "open": 100, "high": 105, "low": 95, "close": 101,
    }}
    row = SimpleNamespace(symbol="5880.TW", technical=technical)
    assert verify_signal_bars(None, [row], run_date=SIGNAL) == ({}, {"5880.TW": "official_signal_bar_mismatch"})
    technical["ohlcv"]["close"] = 102
    accepted, rejected = verify_signal_bars(None, [row], run_date=SIGNAL)
    assert not rejected
    assert accepted["5880.TW"]["sha256"] and accepted["5880.TW"]["raw_ohlcv"]["close"] == 102


def test_official_collection_retains_a_waiting_source_when_no_halt_is_proven(monkeypatch):
    from datetime import datetime, timezone
    from ai_stock_sentinel.daily_radar import next_open_action_scope, trading_status
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector, refused_prices
    from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy

    def busy(*args, **kwargs):
        raise SourceDependencyBusy(["source-key"], datetime.now(timezone.utc))

    monkeypatch.setattr(next_open_action_scope, "_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(trading_status, "collect_target_suspension", lambda *args: None)
    result = OfficialPriceCollector(SimpleNamespace(official_research_history=busy)).collect("5880.TW", SIGNAL, TARGET)
    assert str(TARGET) in result.unavailable_dates
    assert refused_prices({"target_date": str(TARGET)}, "official_validation_price_missing",
        result, sessions=DAYS)["skip_reason"] == "validation_price_source_unavailable"


def test_failed_month_does_not_discard_an_earlier_proven_window_in_same_batch(monkeypatch):
    from ai_stock_sentinel.daily_radar import next_open_action_scope
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector
    july = date(2026, 7, 1)

    def prices(symbol, *, start_date, end_date):
        if start_date.month == 7:
            raise OSError("official outage")
        return [bar(day, 100.0) for day in DAYS if start_date <= day <= end_date]

    def events(symbol, start, end, provider, *, record_detail=None):
        if start.month == 7:
            raise OSError("official event outage")
        return []

    monkeypatch.setattr(next_open_action_scope, "_events", events)
    def missing_status(*args, **kwargs):
        raise OSError('official status outage')
    provider = SimpleNamespace(official_research_history=prices, capture_twse_report=missing_status)
    evidence = OfficialPriceCollector(provider).collect("5880.TW", SIGNAL, july)
    assert not accept_signal_close_prices(CANDIDATE, DAYS, [bar(day, 100.0) for day in DAYS], evidence).reason
    july_window = accept_next_open_prices(CANDIDATE, [SIGNAL, july], [], evidence)
    assert july_window.reason == "official_validation_events_unverified"


def test_locked_signal_close_preserves_proven_trading_termination():
    from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices

    signal = date(2026, 9, 22)
    days = [signal, date(2026, 9, 23)]
    candidate = CANDIDATE | {
        "symbol": "8183.TWO", "record_date": str(signal), "data_dates": {"ohlcv": str(signal)},
    }
    result = _collect_locked_signal_prices(
        [candidate], None, calendar=SimpleNamespace(sessions=days, through=date(2026, 10, 2)),
        request=SimpleNamespace(windows=[1], benchmark_symbol="TAIEX"),
        collect_official=lambda *args: OfficialPriceEvidence({}, {}, None),
        fetch_prices=lambda *args, **kwargs: {},
    )[0]
    assert result["status"] == "skipped"
    assert result["skip_reason"] == "trading_terminated_in_window"
    assert result["outcome"]["market_lifecycle"]


def test_missing_official_evidence_does_not_replace_known_price_conflict(db):
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from test_daily_radar_forward_validation import _add_candidate, _add_run

    run = _add_run(db)
    candidate = _add_candidate(db, run)
    conflict = {
        "candidate_id": candidate.id, "signal_date": str(run.run_date),
        "window_days": 5, "target_date": None, "evaluation_as_of_date": "2026-06-08",
        "benchmark_symbol": "TAIEX", "validation_version": RESEARCH_VALIDATION_VERSION,
        "status": "skipped", "skip_reason": "conflicting_price_records",
        "outcome": {"expected_target_date": "2026-06-08"},
    }
    assert upsert_forward_validation_results(db, [conflict])["records_written"] == 1
    missing = conflict | {"skip_reason": "official_validation_price_missing"}
    assert upsert_forward_validation_results(db, [missing])["records_written"] == 0
    saved = db.scalar(select(DailyRadarForwardValidationResult))
    assert saved.skip_reason == "conflicting_price_records"


def test_reviewed_next_open_lock_versions_terms_and_retains_full_receipt_integrity():
    from ai_stock_sentinel.calibration.twse_rights_recovery import RIGHTS_DETAIL
    from ai_stock_sentinel.daily_radar.price_acceptance import lock_reviewed_next_open
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

    quote = bar(TARGET, 100.0)
    detail_url = f"https://www.twse.com.tw{RIGHTS_DETAIL}?STK_NO=5880&T1=20260604"
    report = {"subscription_price": 75.0}
    receipt = {"source_url": detail_url, "snapshot_sha256": fingerprint(report),
               "captured_at": "2026-06-04T12:00:00+00:00"}
    row = {"symbol": "5880.TW", "signal_date": str(SIGNAL), "outcome": {
        "official_rights_recovery": {"event": {
            "ex_date": str(TARGET), "subscription_price": 75.0,
            **receipt,
        }},
        "reviewed_official_window": {
            "symbol": "5880.TW", "signal_date": str(SIGNAL), "session_dates": [str(TARGET)],
            "stock": [quote], "benchmark": [quote],
            "event_replay": {"yahoo": [quote | {"source_capture": {
                "provider": "yfinance", "snapshot_sha256": "b" * 64,
            }}], "row": {"evaluation_as_of_date": str(TARGET)},
                "calls": [{"request": {"method": "capture_twse_report"}, "value": [report, receipt]}]},
        },
    }}
    source = official([quote])
    standalone = lock_reviewed_next_open(row, source)
    batch = deepcopy(row)
    replay = batch["outcome"]["reviewed_official_window"]["event_replay"]
    replay["yahoo"][0]["source_capture"]["snapshot_sha256"] = "c" * 64
    replay["row"]["evaluation_as_of_date"] = "2026-06-30"
    replay["calls"][0]["value"][1]["captured_at"] = "2026-06-30T12:00:00+00:00"
    batched = lock_reviewed_next_open(batch, source)
    assert standalone["outcome"][PRICE_EVIDENCE] == batched["outcome"][PRICE_EVIDENCE]
    assert standalone["outcome"]["reviewed_official_window_sha256"] != batched["outcome"]["reviewed_official_window_sha256"]
    verify_price_lock(batched, symbol="5880.TW", basis="next_open")

    detail_changed = deepcopy(batch)
    detail_changed["outcome"]["official_rights_recovery"]["event"]["snapshot_sha256"] = "d" * 64
    assert lock_reviewed_next_open(detail_changed, source)["outcome"][PRICE_EVIDENCE] != standalone["outcome"][PRICE_EVIDENCE]

    tampered = deepcopy(batch)
    tampered["outcome"]["reviewed_official_window"]["event_replay"]["calls"][0]["value"][0]["subscription_price"] = 76.0
    with pytest.raises(ValueError, match="next_open_official_event_report_hash_invalid"):
        verify_price_lock(lock_reviewed_next_open(tampered, source), symbol="5880.TW", basis="next_open")

    batch["outcome"]["official_rights_recovery"]["event"]["subscription_price"] = 76.0
    corrected = lock_reviewed_next_open(batch, source)
    assert corrected["outcome"][PRICE_EVIDENCE]["sha256"] != standalone["outcome"][PRICE_EVIDENCE]["sha256"]
    replay["yahoo"][0]["close"] = 120.0
    with pytest.raises(ValueError, match="invalid_validation_price_lock"):
        verify_price_lock(batched, symbol="5880.TW", basis="next_open")
