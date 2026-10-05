from datetime import date, timedelta
from types import SimpleNamespace
import pytest

from ai_stock_sentinel.daily_radar.market_exploration import build_market_exploration, build_turnover_contexts
from ai_stock_sentinel.daily_radar.pipeline_support import _capped_daily_radar_universe
from ai_stock_sentinel.daily_radar.universe import DailyRadarUniverseEntry


def _bars(symbol="1234.TW", count=70):
    days = []
    day = date(2026, 6, 1)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return [SimpleNamespace(
        symbol=symbol, trade_date=day, market="TWO" if symbol.endswith(".TWO") else "TW",
        close=50 + i * .5, open=50 + i * .5, high=51 + i * .5, low=49 + i * .5,
        volume=20_000_000 if i == count - 1 else 10_000_000, amount=1_000_000_000,
        is_final=True, dataset="taiwan_market_daily_ohlcv", adjustment_mode="unadjusted",
    ) for i, day in enumerate(days)]


def _with_market_reference(bars):
    """Provide the other market's calendar without adding stock candidates."""
    references = {}
    for bar in bars:
        market = "TWO" if bar.market == "TW" else "TW"
        references[(market, bar.trade_date)] = SimpleNamespace(**(vars(bar) | {
            "symbol": "0050.TW" if market == "TW" else "006201.TWO", "market": market,
        }))
    return list(bars) + list(references.values())


def test_exploration_finds_stock_without_existing_raw_data_or_institutional_leadership():
    bars = _bars()
    entries, audit = build_market_exploration(_with_market_reference(bars), run_date=bars[-1].trade_date)
    assert [entry.symbol for entry in entries] == ["1234.TW"]
    assert "market_trend" in entries[0].tracks
    assert "market_price_volume" in entries[0].tracks
    assert entries[0].track_metrics["market_trend"]["adjustment_mode"] == "unadjusted"
    assert audit["eligible_symbol_count"] == 1


def test_exploration_rejects_incomplete_series_and_suspected_corporate_action():
    bars = _bars()
    broken = _bars("5678.TW")
    broken.pop(35)
    jump = _bars("6789.TW")
    jump[-1].close *= .5
    entries, audit = build_market_exploration(_with_market_reference(bars + broken + jump), run_date=bars[-1].trade_date)
    assert [entry.symbol for entry in entries] == ["1234.TW"]
    assert audit["excluded_symbol_reasons"]["5678.TW"] == "history_gap"
    assert audit["excluded_symbol_reasons"]["6789.TW"] == "price_discontinuity"


def test_exploration_excludes_future_nonfinal_and_unsupported_products():
    bars = _bars()
    future = _bars("5678.TW")
    future[-1].trade_date = bars[-1].trade_date + timedelta(days=1)
    nonfinal = _bars("6789.TW")
    nonfinal[-1].is_final = False
    entries, audit = build_market_exploration(_with_market_reference(bars + future + nonfinal + _bars("0050.TW")), run_date=bars[-1].trade_date)
    assert [entry.symbol for entry in entries] == ["1234.TW"]
    assert audit["excluded_symbol_reasons"]["5678.TW"] == "current_day_missing"


def test_capping_reserves_discovery_seats_instead_of_slicing_institutional_first():
    entries = [DailyRadarUniverseEntry(symbol=f"{1000+i}.TW", rank=i+1, primary_track="foreign_same_day",
                tracks=("foreign_same_day",)) for i in range(10)]
    entries.append(DailyRadarUniverseEntry(symbol="5678.TW", rank=11, primary_track="market_trend", tracks=("market_trend",)))
    capped = _capped_daily_radar_universe(entries, max_symbols=2)
    assert [row.symbol for row in capped] == ["1000.TW", "5678.TW"]


def test_exploration_fails_closed_when_actual_turnover_is_missing():
    bars = _bars()
    bars[-2].amount = None
    entries, audit = build_market_exploration(_with_market_reference(bars), run_date=bars[-1].trade_date)
    assert entries == []
    assert audit["excluded_symbol_reasons"]["1234.TW"] == "turnover_missing"


def test_turnover_uses_actual_amount_and_does_not_estimate_across_missing_sessions():
    bars = _bars()
    contexts = build_turnover_contexts(_with_market_reference(bars), run_date=bars[-1].trade_date)
    assert contexts["1234.TW"]["avg_turnover_value_million"] == 1000
    broken = _bars("5678.TW")
    broken.pop(-3)
    contexts = build_turnover_contexts(_with_market_reference(bars + broken), run_date=bars[-1].trade_date)
    assert contexts["5678.TW"]["missing_reason"] == "turnover_history_gap"
    assert "avg_turnover_value_million" not in contexts["5678.TW"]


@pytest.mark.parametrize("missing_market", ["TW", "TWO"])
def test_entire_market_missing_session_cannot_compress_exploration_or_turnover(missing_market):
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[66].trade_date
    run_date = bars[69].trade_date
    incomplete = [bar for bar in bars if not (bar.market == missing_market and bar.trade_date == missing_day)]
    entries, audit = build_market_exploration(incomplete, run_date=run_date)
    assert entries == []
    assert audit["missing_market_dates"] == {missing_day.isoformat(): [missing_market]}
    contexts = build_turnover_contexts(incomplete, run_date=run_date)
    assert all(context["missing_reason"] == "market_archive_history_gap" for context in contexts.values())
    assert all("avg_turnover_value_million" not in context for context in contexts.values())
    assert len(contexts["1234.TW"]["source_dates"]) == 20
    assert missing_day.isoformat() in contexts["1234.TW"]["source_dates"]


def test_gap_outside_twenty_day_window_does_not_invalidate_actual_turnover():
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[20].trade_date
    incomplete = [bar for bar in bars if not (bar.market == "TWO" and bar.trade_date == missing_day)]
    entries, audit = build_market_exploration(incomplete, run_date=bars[69].trade_date)
    assert entries == []
    assert missing_day.isoformat() in audit["missing_market_dates"]
    contexts = build_turnover_contexts(incomplete, run_date=bars[69].trade_date)
    assert contexts["1234.TW"]["avg_turnover_value_million"] == 1000


def test_nonfinal_market_session_does_not_satisfy_archive_history():
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[66].trade_date
    for bar in bars:
        if bar.market == "TWO" and bar.trade_date == missing_day:
            bar.is_final = False
    entries, audit = build_market_exploration(bars, run_date=bars[69].trade_date)
    assert entries == []
    assert audit["missing_market_dates"] == {missing_day.isoformat(): ["TWO"]}


@pytest.mark.parametrize("missing_market", ["TW", "TWO"])
def test_turnover_alone_rejects_missing_entire_market_session(missing_market):
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[66].trade_date
    incomplete = [bar for bar in bars if not (bar.market == missing_market and bar.trade_date == missing_day)]
    contexts = build_turnover_contexts(incomplete, run_date=bars[69].trade_date)
    assert contexts["1234.TW"]["missing_reason"] == "market_archive_history_gap"
    assert contexts["5678.TWO"]["missing_reason"] == "market_archive_history_gap"
    assert all("avg_turnover_value_million" not in context for context in contexts.values())


def test_session_with_only_nonfinal_rows_in_both_markets_stays_in_required_calendar():
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[66].trade_date
    for bar in bars:
        if bar.trade_date == missing_day:
            bar.is_final = False
    entries, audit = build_market_exploration(bars, run_date=bars[69].trade_date)
    assert entries == []
    assert audit["missing_market_dates"] == {missing_day.isoformat(): ["TW", "TWO"]}
