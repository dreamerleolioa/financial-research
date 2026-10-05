"""Discover sustained institutional participation without raw-share size bias."""
from collections import defaultdict
from datetime import date
from typing import Any, Iterable

from ai_stock_sentinel.daily_radar.market_exploration import build_turnover_contexts, _number
from ai_stock_sentinel.daily_radar.universe import DailyRadarUniverseEntry, merge_discovery_universe, is_daily_radar_supported_symbol

SCALED_ACCUMULATION_VERSION = "scaled-accumulation-v1"
SCALED_TRACKS = {"foreign_scaled_accumulation": "foreign_net_shares",
                 "trust_scaled_accumulation": "investment_trust_net_shares"}


def build_scaled_accumulation(bars: Iterable[Any], flows: Iterable[Any], *, run_date: date,
                              track_limit: int = 50) -> tuple[list[DailyRadarUniverseEntry], dict[str, Any]]:
    bars = [bar for bar in bars if bar.trade_date <= run_date and bar.is_final and bar.adjustment_mode == "unadjusted"]
    turnover = build_turnover_contexts(bars, run_date=run_date)
    days = sorted({bar.trade_date for bar in bars})[-60:]
    audit = {"version": SCALED_ACCUMULATION_VERSION, "as_of_date": run_date.isoformat(),
             "status": "insufficient_history", "excluded_symbol_reasons": {}, "track_counts": {}}
    if len(days) < 60 or days[-1] != run_date:
        return [], audit
    prices, buying = defaultdict(dict), defaultdict(dict)
    for bar in bars:
        if is_daily_radar_supported_symbol(bar.symbol):
            prices[bar.symbol][bar.trade_date] = bar
    for flow in flows:
        if flow.trade_date <= run_date:
            buying[flow.symbol][flow.trade_date] = flow
    matches = {track: [] for track in SCALED_TRACKS}
    exclusions = audit["excluded_symbol_reasons"]
    for symbol, rows in sorted(prices.items()):
        if any(day not in rows for day in days):
            exclusions[symbol] = "price_history_gap"
            continue
        closes = [_number(rows[day].close) for day in days]
        if any(value is None or value <= 0 for value in closes):
            exclusions[symbol] = "invalid_price"
            continue
        if any(abs(current / previous - 1) >= .25 for previous, current in zip(closes, closes[1:])):
            exclusions[symbol] = "price_discontinuity"
            continue
        actual = turnover.get(symbol, {}).get("avg_turnover_value_million")
        close = _number(rows[days[-1]].close)
        if actual is None or actual < 300 or close is None or close < 20:
            exclusions[symbol] = "baseline_liquidity_or_price"
            continue
        if any(day not in buying[symbol] for day in days):
            exclusions[symbol] = "institutional_history_gap"
            continue
        volume = [_number(rows[day].volume) for day in days]
        if any(value is None or value <= 0 for value in volume):
            exclusions[symbol] = "volume_missing"
            continue
        for track, field in SCALED_TRACKS.items():
            net = [_number(getattr(buying[symbol][day], field, None)) for day in days]
            if any(value is None for value in net):
                exclusions[symbol] = "institutional_value_missing"
                continue
            net20, net60 = sum(net[-20:]), sum(net)
            positive_days = sum(value > 0 for value in net[-20:])
            if net20 <= 0 or net60 <= 0 or positive_days <= 10:
                continue
            ratio20, ratio60 = net20 / sum(volume[-20:]), net60 / sum(volume)
            matches[track].append((symbol, {"version": SCALED_ACCUMULATION_VERSION, "as_of_date": run_date.isoformat(),
                "source": "taiwan_institutional_flow+taiwan_market_daily_ohlcv", "discovery_only": True,
                "source_dates": [day.isoformat() for day in days], "positive_days_20d": positive_days,
                "cumulative_net_shares_20d": net20, "cumulative_net_shares_60d": net60,
                "net_to_volume_20d": round(ratio20, 6), "net_to_volume_60d": round(ratio60, 6),
                "score": ratio20, "matched": True}))
    entries = []
    for track, values in matches.items():
        values.sort(key=lambda value: (-value[1]["score"], -value[1]["net_to_volume_60d"], value[0]))
        for rank, (symbol, metrics) in enumerate(values[:max(0, track_limit)], 1):
            entries.append(DailyRadarUniverseEntry(symbol=symbol, rank=rank, primary_track=track,
                tracks=(track,), track_metrics={track: metrics | {"rank": rank}}))
    return merge_discovery_universe([], entries), audit | {"status": "completed",
        "track_counts": {track: min(len(values), max(0, track_limit)) for track, values in matches.items()}}
