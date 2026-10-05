"""Cheap, point-in-time discovery from official unadjusted market archives.

These tracks discover symbols only. Adjusted selected-symbol history remains the
sole source of technical scoring; discontinuities and missing bars fail closed.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from math import isfinite
from typing import Any, Iterable

from ai_stock_sentinel.daily_radar.universe import DailyRadarUniverseEntry, is_daily_radar_supported_symbol

EXPLORATION_VERSION = "market-exploration-v1"
EXPLORATION_TRACKS = ("market_trend", "market_price_volume")


class MarketExplorationReadinessError(ValueError):
    def __init__(self, missing_markets: list[str]) -> None:
        self.missing_markets = missing_markets
        super().__init__("Current final market archives are incomplete")


def load_market_exploration(session: Any, *, run_date: date, required: bool = True):
    from sqlalchemy import select
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from ai_stock_sentinel.daily_radar.market_bar_repository import DEFAULT_MARKET_BAR_DATASET
    recent_dates = select(TaiwanDailyBar.trade_date).where(
        TaiwanDailyBar.trade_date >= run_date - timedelta(days=240),
        TaiwanDailyBar.trade_date <= run_date, TaiwanDailyBar.dataset == DEFAULT_MARKET_BAR_DATASET,
        TaiwanDailyBar.adjustment_mode == "unadjusted", TaiwanDailyBar.is_final.is_(True),
    ).distinct().order_by(TaiwanDailyBar.trade_date.desc()).limit(65)
    bars = session.execute(select(
        TaiwanDailyBar.symbol, TaiwanDailyBar.market, TaiwanDailyBar.trade_date,
        TaiwanDailyBar.close, TaiwanDailyBar.volume, TaiwanDailyBar.amount,
        TaiwanDailyBar.is_final, TaiwanDailyBar.adjustment_mode,
    ).where(
        TaiwanDailyBar.trade_date.in_(recent_dates), TaiwanDailyBar.dataset == DEFAULT_MARKET_BAR_DATASET,
        TaiwanDailyBar.adjustment_mode == "unadjusted", TaiwanDailyBar.is_final.is_(True),
    ).order_by(TaiwanDailyBar.symbol, TaiwanDailyBar.trade_date)).all()
    current_markets = {bar.market for bar in bars if bar.trade_date == run_date}
    missing = sorted({"TW", "TWO"} - current_markets)
    if missing:
        if required:
            raise MarketExplorationReadinessError(missing)
        return [], {"run_date": run_date.isoformat(), "missing_markets": missing, "status": "unavailable"}
    entries, audit = build_market_exploration(bars, run_date=run_date)
    from ai_stock_sentinel.daily_radar.scaled_accumulation import build_scaled_accumulation
    from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
        get_complete_institutional_archive_window, InstitutionalArchiveIntegrityError,
    )
    from ai_stock_sentinel.daily_radar.universe import merge_discovery_universe
    days = sorted({bar.trade_date for bar in bars})[-60:]
    if len(days) < 60:
        audit["scaled_accumulation"] = {"status": "insufficient_history"}
        return entries, audit
    try:
        archived = get_complete_institutional_archive_window(session, start_date=days[0], end_date=run_date)
        scaled_entries, scaled_audit = build_scaled_accumulation(
            bars, [flow for daily in archived.values() for flow in daily], run_date=run_date,
        )
    except InstitutionalArchiveIntegrityError as exc:
        scaled_entries, scaled_audit = [], {"status": "archive_invalid", "code": exc.code,
            "market": exc.market, "trade_date": exc.trade_date.isoformat()}
    audit["scaled_accumulation"] = scaled_audit
    return merge_discovery_universe(entries, scaled_entries), audit


def build_market_exploration(
    bars: Iterable[Any], *, run_date: date, track_limit: int = 50,
) -> tuple[list[DailyRadarUniverseEntry], dict[str, Any]]:
    by_symbol: dict[str, dict[date, Any]] = defaultdict(dict)
    market_dates: dict[str, set[date]] = defaultdict(set)
    for bar in bars:
        if bar.trade_date > run_date or not bar.is_final or bar.adjustment_mode != "unadjusted":
            continue
        market_dates[bar.market].add(bar.trade_date)
        if is_daily_radar_supported_symbol(bar.symbol):
            by_symbol[bar.symbol][bar.trade_date] = bar
    exclusions: dict[str, str] = {}
    ranked: dict[str, list[tuple[str, dict[str, Any]]]] = {track: [] for track in EXPLORATION_TRACKS}
    eligible_count = 0
    for symbol, rows in sorted(by_symbol.items()):
        if run_date not in rows:
            exclusions[symbol] = "current_day_missing"
            continue
        ordered = [rows[day] for day in sorted(rows)]
        sessions = sorted(day for day in market_dates[ordered[-1].market] if day <= run_date)
        required_days = sessions[-min(65, len(ordered)):]
        if len(ordered) < 21:
            exclusions[symbol] = "insufficient_exploration_history"
            continue
        if any(day not in rows for day in required_days):
            exclusions[symbol] = "history_gap"
            continue
        window = [rows[day] for day in required_days]
        closes = [_number(bar.close) for bar in window]
        volumes = [_number(bar.volume) for bar in window]
        if any(value is None or value <= 0 for value in closes + volumes):
            exclusions[symbol] = "invalid_price_or_volume"
            continue
        if any(abs(current / previous - 1) >= .25 for previous, current in zip(closes, closes[1:])):
            exclusions[symbol] = "price_discontinuity"
            continue
        amounts = [_number(bar.amount) for bar in window[-20:]]
        if any(value is None or value <= 0 for value in amounts):
            exclusions[symbol] = "turnover_missing"
            continue
        average_turnover = sum(amounts) / 20 / 1_000_000
        if average_turnover < 300 or closes[-1] < 20:
            exclusions[symbol] = "baseline_liquidity_or_price"
            continue
        eligible_count += 1
        ma20 = sum(closes[-20:]) / 20
        volume_ratio = volumes[-1] / (sum(volumes[-21:-1]) / 20)
        return20 = (closes[-1] / closes[-21] - 1) * 100
        metrics = {
            "exploration_version": EXPLORATION_VERSION,
            "source": "taiwan_market_daily_ohlcv", "adjustment_mode": "unadjusted",
            "as_of_date": run_date.isoformat(), "source_dates": [bar.trade_date.isoformat() for bar in window],
            "avg_turnover_value_million": round(average_turnover, 3),
            "return_20d_pct": round(return20, 4), "volume_ratio": round(volume_ratio, 4),
            "ma20": round(ma20, 4), "discovery_only": True,
        }
        if closes[-1] > ma20 and closes[-1] > closes[-2] and volume_ratio >= 1.25:
            ranked["market_price_volume"].append((symbol, metrics | {"score": volume_ratio, "matched": True}))
        if len(closes) >= 65:
            ma60 = sum(closes[-60:]) / 60
            prior_ma60 = sum(closes[-65:-5]) / 60
            return60 = (closes[-1] / closes[-61] - 1) * 100
            if closes[-1] >= ma20 >= ma60 and ma60 > prior_ma60 and return60 > 0:
                ranked["market_trend"].append((symbol, metrics | {
                    "score": return60, "matched": True, "ma60": round(ma60, 4),
                    "ma60_slope_5d_pct": round((ma60 / prior_ma60 - 1) * 100, 4),
                    "return_60d_pct": round(return60, 4),
                }))
    entries: dict[str, DailyRadarUniverseEntry] = {}
    for track, matches in ranked.items():
        matches.sort(key=lambda item: (-item[1]["score"], item[0]))
        for rank, (symbol, metrics) in enumerate(matches[:max(0, track_limit)], 1):
            track_metrics = metrics | {"rank": rank}
            existing = entries.get(symbol)
            if existing:
                entries[symbol] = replace(existing, tracks=(*existing.tracks, track),
                                          track_metrics=existing.track_metrics | {track: track_metrics})
            else:
                entries[symbol] = DailyRadarUniverseEntry(symbol=symbol, rank=len(entries)+1,
                    primary_track=track, tracks=(track,), track_metrics={track: track_metrics})
    return list(entries.values()), {
        "exploration_version": EXPLORATION_VERSION, "run_date": run_date.isoformat(),
        "scanned_symbol_count": len(by_symbol), "eligible_symbol_count": eligible_count,
        "discovered_symbol_count": len(entries), "excluded_symbol_reasons": exclusions,
        "market_dates": {market: max(days).isoformat() for market, days in market_dates.items() if days},
        "track_counts": {track: min(len(matches), max(0, track_limit)) for track, matches in ranked.items()},
    }


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (ValueError, TypeError):
        return None


def build_turnover_contexts(bars: Iterable[Any], *, run_date: date) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[date, Any]] = defaultdict(dict)
    market_dates: dict[str, set[date]] = defaultdict(set)
    for bar in bars:
        if bar.trade_date <= run_date and bar.is_final and bar.adjustment_mode == "unadjusted":
            by_symbol[bar.symbol][bar.trade_date] = bar
            market_dates[bar.market].add(bar.trade_date)
    result = {}
    for symbol, rows in by_symbol.items():
        market = rows[max(rows)].market
        days = sorted(market_dates[market])[-20:]
        trace = {"source": "taiwan_market_daily_ohlcv", "as_of_date": run_date.isoformat(),
                 "source_dates": [day.isoformat() for day in days], "missing_reason": None}
        if len(days) < 20 or days[-1] != run_date or any(day not in rows for day in days):
            result[symbol] = trace | {"missing_reason": "turnover_history_gap"}
            continue
        amounts = [_number(rows[day].amount) for day in days]
        if any(value is None or value <= 0 for value in amounts):
            result[symbol] = trace | {"missing_reason": "turnover_amount_missing"}
            continue
        result[symbol] = trace | {"avg_turnover_value_million": round(sum(amounts) / 20 / 1_000_000, 3)}
    return result


def attach_official_turnover(session: Any, rows: Iterable[Any], *, run_date: date) -> None:
    from sqlalchemy import select
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from ai_stock_sentinel.daily_radar.market_bar_repository import DEFAULT_MARKET_BAR_DATASET
    raw_rows = list(rows)
    if not raw_rows:
        return
    recent_dates = select(TaiwanDailyBar.trade_date).where(
        TaiwanDailyBar.trade_date >= run_date - timedelta(days=90), TaiwanDailyBar.trade_date <= run_date,
        TaiwanDailyBar.dataset == DEFAULT_MARKET_BAR_DATASET,
        TaiwanDailyBar.adjustment_mode == "unadjusted", TaiwanDailyBar.is_final.is_(True),
    ).distinct().order_by(TaiwanDailyBar.trade_date.desc()).limit(20)
    # Keep all market rows in this narrow query so a candidate's own gaps cannot shorten its calendar.
    bars = session.execute(select(TaiwanDailyBar.symbol, TaiwanDailyBar.market, TaiwanDailyBar.trade_date,
                                  TaiwanDailyBar.amount, TaiwanDailyBar.is_final, TaiwanDailyBar.adjustment_mode).where(
        TaiwanDailyBar.trade_date.in_(recent_dates), TaiwanDailyBar.dataset == DEFAULT_MARKET_BAR_DATASET,
        TaiwanDailyBar.adjustment_mode == "unadjusted", TaiwanDailyBar.is_final.is_(True),
    )).all()
    contexts = build_turnover_contexts(bars, run_date=run_date)
    for row in raw_rows:
        technical = dict(row.technical or {})
        ohlcv = dict(technical.get("ohlcv") or {})
        context = contexts.get(row.symbol, {"source": DEFAULT_MARKET_BAR_DATASET,
                                           "as_of_date": run_date.isoformat(), "missing_reason": "turnover_history_gap"})
        ohlcv.pop("avg_turnover_value_million", None)
        if "avg_turnover_value_million" in context:
            ohlcv["avg_turnover_value_million"] = context["avg_turnover_value_million"]
        ohlcv["turnover_context"] = context
        row.technical = technical | {"ohlcv": ohlcv}
