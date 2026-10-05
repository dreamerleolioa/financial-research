from types import SimpleNamespace

from test_daily_radar_market_exploration import _bars, _with_market_reference
from ai_stock_sentinel.daily_radar.scaled_accumulation import build_scaled_accumulation


def _flows(bars, net=100_000):
    return [SimpleNamespace(symbol=bar.symbol, trade_date=bar.trade_date,
                            foreign_net_shares=net, investment_trust_net_shares=net / 2) for bar in bars]


def test_scaled_accumulation_uses_volume_share_instead_of_absolute_buy_shares():
    small, large = _bars("1234.TW"), _bars("5678.TW")
    for bar in large:
        bar.volume *= 100
    # Larger raw buys, but smaller participation relative to its trading volume.
    entries, audit = build_scaled_accumulation(_with_market_reference(small + large), _flows(small) + _flows(large, 1_000_000),
                                              run_date=small[-1].trade_date, track_limit=1)
    assert [entry.symbol for entry in entries] == ["1234.TW"]
    metrics = entries[0].track_metrics["foreign_scaled_accumulation"]
    assert metrics["cumulative_net_shares_20d"] == 2_000_000
    assert metrics["cumulative_net_shares_60d"] == 6_000_000
    assert metrics["positive_days_20d"] == 20
    assert metrics["discovery_only"] is True
    assert audit["status"] == "completed"


def test_scaled_accumulation_never_zero_fills_missing_archived_flow_or_price():
    intact, gap = _bars("1234.TW"), _bars("5678.TW")
    flows = _flows(intact) + _flows(gap)
    flows = [flow for flow in flows if not (flow.symbol == "5678.TW" and flow.trade_date == gap[-4].trade_date)]
    entries, audit = build_scaled_accumulation(_with_market_reference(intact + gap), flows, run_date=intact[-1].trade_date)
    assert [entry.symbol for entry in entries] == ["1234.TW"]
    assert audit["excluded_symbol_reasons"]["5678.TW"] == "institutional_history_gap"
    gap.pop(-3)
    entries, audit = build_scaled_accumulation(_with_market_reference(intact + gap), _flows(intact) + _flows(gap), run_date=intact[-1].trade_date)
    assert audit["excluded_symbol_reasons"]["5678.TW"] == "price_history_gap"


def test_scaled_accumulation_requires_positive_long_term_net_and_majority_positive_days():
    bars = _bars()
    flows = _flows(bars)
    for flow in flows[-60:-20]:
        flow.foreign_net_shares = -1_000_000
        flow.investment_trust_net_shares = -1_000_000
    entries, _ = build_scaled_accumulation(_with_market_reference(bars), flows, run_date=bars[-1].trade_date)
    assert entries == []


def test_scaled_accumulation_rejects_unadjusted_price_discontinuity():
    bars = _bars()
    flows = _flows(bars)
    bars[-2].close *= .5
    entries, audit = build_scaled_accumulation(_with_market_reference(bars), flows, run_date=bars[-1].trade_date)
    assert entries == []
    assert audit["excluded_symbol_reasons"]["1234.TW"] == "price_discontinuity"


def test_scaled_accumulation_reports_missing_archive_sessions_without_shortening_60d_window():
    bars = _bars()
    entries, audit = build_scaled_accumulation(_with_market_reference(bars), _flows(bars[-20:]), run_date=bars[-1].trade_date)
    assert entries == []
    assert audit["status"] == "insufficient_institutional_history"
    assert audit["complete_session_count"] == 20
    assert len(audit["missing_session_dates"]) == 40


def test_scaled_accumulation_rejects_missing_market_day_outside_turnover_window():
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[20].trade_date
    incomplete = [bar for bar in bars if not (bar.market == "TWO" and bar.trade_date == missing_day)]
    entries, audit = build_scaled_accumulation(incomplete, _flows(bars), run_date=bars[69].trade_date)
    assert entries == []
    assert audit["status"] == "market_archive_incomplete"
    assert audit["missing_market_dates"] == {missing_day.isoformat(): ["TWO"]}


def test_scaled_accumulation_flow_dates_keep_session_missing_from_both_markets():
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[20].trade_date
    incomplete = [bar for bar in bars if bar.trade_date != missing_day]
    entries, audit = build_scaled_accumulation(incomplete, _flows(bars), run_date=bars[69].trade_date)
    assert entries == []
    assert audit["missing_market_dates"] == {missing_day.isoformat(): ["TW", "TWO"]}


def test_scaled_accumulation_retains_nonfinal_day_even_when_flow_archive_is_also_absent():
    bars = _bars() + _bars("5678.TWO")
    missing_day = bars[66].trade_date
    for bar in bars:
        if bar.trade_date == missing_day:
            bar.is_final = False
    flows = [flow for flow in _flows(bars) if flow.trade_date != missing_day]
    entries, audit = build_scaled_accumulation(bars, flows, run_date=bars[69].trade_date)
    assert entries == []
    assert audit["missing_market_dates"] == {missing_day.isoformat(): ["TW", "TWO"]}
