"""Replay the two 11/05 conflicts without network or production access."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result
from test_next_open_corporate_actions import replay


TARGET = date(2026, 11, 5)
CASES = [("1815.TWO", date(2026, 9, 3), 40), ("6669.TW", date(2026, 8, 6), 60)]


@pytest.fixture
def precision_replay(replay):
    archive, *_ = replay
    archive.update(json.loads((Path(__file__).parent / "fixtures/next_open_distribution_precision.json").read_text()))
    return replay


@pytest.mark.parametrize("symbol,signal,window", CASES)
def test_captured_rounding_conflicts_become_proven_distributions(precision_replay, symbol, signal, window):
    archive, _, engine, run, calls = precision_replay
    original = copy.deepcopy(archive)
    result = run(symbol, signal, window, target=TARGET, seed=True)
    assert result["validated_count"] == 0  # Distribution windows are not ordinary return samples.
    assert result["terminal_skipped_count"] == 1
    assert result["report"]["remaining_retryable_window_count"] == 0
    with Session(engine) as db:
        row = db.scalars(select(Result)).one()
        assert row.skip_reason == "stock_distribution_in_window"
        evidence = row.outcome["official_corporate_action"]
        assert len(evidence["session_dates"]) == window
        assert evidence["session_dates"][-1] == str(TARGET)
        assert "2026-10-23" not in evidence["session_dates"]
        precision = evidence["distribution_precision"]
        assert precision["policy_version"] == "bounded-distribution-decimals-v1"
        assert precision["price_share_ratio"] == (1.0500017 if symbol == "1815.TWO" else 2.9827947)
        assert evidence["event"]["share_ratio"] == (1.05000171092 if symbol == "1815.TWO" else 2.9828)
        assert precision["official_terms"]["shares_per_thousand"] == ("50.00171092" if symbol == "1815.TWO" else "1982.8")
        assert precision["official_terms"]["cash_per_share"] == ("0.50001709" if symbol == "1815.TWO" else "0")
        assert precision["provider_cash_per_share"] == (0.500017 if symbol == "1815.TWO" else 0)
        assert evidence["sources"] and all(len(s["snapshot_sha256"]) == 64 for s in evidence["sources"])
    assert archive == original
    before = calls[:]
    assert run(symbol, signal, window, target=TARGET)["records_written"] == 0
    assert calls == before


def _assert_retryable(result, engine):
    assert result["terminal_skipped_count"] == 0
    assert result["report"]["remaining_retryable_window_count"] == 1
    with Session(engine) as db:
        row = db.scalars(select(Result)).one()
        assert row.skip_reason == "conflicting_price_records"
        assert "official_corporate_action" not in row.outcome


@pytest.mark.parametrize("symbol,signal,window", CASES)
@pytest.mark.parametrize("field", [1, 2, 3, 4], ids=["open", "high", "low", "close"])
@pytest.mark.parametrize("period", ["before", "after"])
def test_any_unexplained_ohlc_stays_retryable(precision_replay, symbol, signal, window, field, period):
    archive, _, engine, run, _ = precision_replay
    code = symbol.split(".")[0]
    day = str(signal) if period == "before" else "2026-11-05"
    next(row for row in archive[f"yahoo-{code}"] if row[0] == day)[field] += .01
    _assert_retryable(run(symbol, signal, window, target=TARGET, seed=True), engine)


@pytest.mark.parametrize("defect", ["ratio", "cash", "date", "duplicate", "missing", "nan", "zero_cash", "official_precision",
                                   "provider_coarse_cash", "official_coarse_cash", "provider_coarse_ratio"])
def test_event_differences_must_be_explained_by_bounded_source_precision(precision_replay, defect):
    archive, _, engine, run, _ = precision_replay
    symbol, signal, window = CASES[1] if defect in {"zero_cash", "official_precision"} else CASES[0]
    rows = archive[f"yahoo-{symbol.split('.')[0]}"]
    event = next(row for row in rows if row[7] not in (0, 1))
    if defect == "ratio":
        event[7] += .0001
    elif defect == "cash":
        event[6] += .00001
    elif defect == "date":
        event[6], event[7], rows[-1][6], rows[-1][7] = 0, 0, event[6], event[7]
    elif defect == "duplicate":
        rows[-1][6], rows[-1][7] = event[6], event[7]
    elif defect == "missing":
        rows[-1][7] = None
    elif defect == "nan":
        event[7] = float("nan")
    elif defect == "zero_cash":
        event[6] = .0000001
    elif defect == "provider_coarse_cash":
        event[6] = .5
    elif defect == "official_coarse_cash":
        archive["tpex-distributions"][0][4] = "0.5"
    elif defect == "provider_coarse_ratio":
        event[7] = 1.05
    else:
        detail = archive["twse-rights-6669"]
        idx = detail["fields"].index("A. 按普通股股東持股比例每千股無償配股")
        detail["data"][0][idx] = "1,982.80000000 股"
    result = run(symbol, signal, window, target=TARGET, seed=True)
    _assert_retryable(result, engine)
    assert any(d.get("reason") == "official_corporate_action_provider_event_mismatch"
               for d in result["report"]["source_recovery"])


@pytest.mark.parametrize("defect", ["stock_gap", "benchmark_gap", "unverified_market_session"])
def test_rounding_cannot_hide_missing_or_unverified_sessions(precision_replay, defect):
    archive, fault, engine, run, _ = precision_replay
    symbol, signal, window = CASES[1]
    rows = archive["yahoo-6669"]
    if defect == "stock_gap":
        rows[:] = [row for row in rows if row[0] != "2026-10-19"]
    elif defect == "benchmark_gap":
        fault.add("missing_index")
    else:
        next(row for row in rows if row[0] == "2026-10-23")[5] = 1
    _assert_retryable(run(symbol, signal, window, target=TARGET, seed=True), engine)


@pytest.mark.parametrize("symbol,signal,window", CASES)
def test_distribution_requires_a_provider_split_even_if_official_factor_matches_prices(precision_replay, symbol, signal, window):
    archive, _, engine, run, _ = precision_replay
    next(row for row in archive[f"yahoo-{symbol.split('.')[0]}"] if row[7] not in (0, 1))[7] = 0
    _assert_retryable(run(symbol, signal, window, target=TARGET, seed=True), engine)
