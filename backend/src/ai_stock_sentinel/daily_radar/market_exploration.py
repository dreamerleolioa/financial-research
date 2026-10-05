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
REQUIRED_ARCHIVE_MARKETS = frozenset({"TW", "TWO"})


class MarketExplorationReadinessError(ValueError):
    def __init__(self, missing_market_dates: dict[str, list[str]], archive_dates: list[date]) -> None:
        self.missing_market_dates = missing_market_dates
        self.missing_markets = sorted({market for markets in missing_market_dates.values() for market in markets})
        self.archive_dates = [day.isoformat() for day in archive_dates]
        super().__init__("Required final market archives are incomplete")


def market_archive_window(
    bars: Iterable[Any], *, run_date: date, window_size: int,
    archive_dates: Iterable[date] | None = None,
) -> tuple[list[date], dict[str, list[str]]]:
    """Fix the calendar before checking market coverage; never shrink it to surviving rows."""
    observed: dict[date, set[str]] = defaultdict(set)
    known_dates = {run_date}
    for bar in bars:
        if (bar.trade_date <= run_date and bar.adjustment_mode == "unadjusted"
                and bar.market in REQUIRED_ARCHIVE_MARKETS):
            known_dates.add(bar.trade_date)
            if bar.is_final:
                observed[bar.trade_date].add(bar.market)
    days = sorted(known_dates | {day for day in archive_dates or [] if day <= run_date})[-window_size:]
    missing = {day.isoformat(): sorted(REQUIRED_ARCHIVE_MARKETS - observed.get(day, set()))
               for day in days if REQUIRED_ARCHIVE_MARKETS - observed.get(day, set())}
    return days, missing


def _load_archive_dates(session: Any, *, run_date: date, window_size: int, horizon_days: int) -> list[date]:
    from sqlalchemy import select, union
    from ai_stock_sentinel.db.models import TaiwanDailyBar, TaiwanInstitutionalReportSnapshot
    from ai_stock_sentinel.daily_radar.market_bar_repository import DEFAULT_MARKET_BAR_DATASET
    from ai_stock_sentinel.daily_radar.institutional_flow_repository import DEFAULT_INSTITUTIONAL_FLOW_DATASET
    start_date = run_date - timedelta(days=horizon_days)
    known_dates = union(
        select(TaiwanDailyBar.trade_date).where(
            TaiwanDailyBar.trade_date.between(start_date, run_date),
            TaiwanDailyBar.market.in_(REQUIRED_ARCHIVE_MARKETS), TaiwanDailyBar.dataset == DEFAULT_MARKET_BAR_DATASET,
            TaiwanDailyBar.adjustment_mode == "unadjusted",
        ),
        select(TaiwanInstitutionalReportSnapshot.trade_date).where(
            TaiwanInstitutionalReportSnapshot.trade_date.between(start_date, run_date),
            TaiwanInstitutionalReportSnapshot.market.in_(REQUIRED_ARCHIVE_MARKETS),
            TaiwanInstitutionalReportSnapshot.dataset == DEFAULT_INSTITUTIONAL_FLOW_DATASET,
            TaiwanInstitutionalReportSnapshot.status == "completed",
        ),
    ).subquery()
    days = session.scalars(select(known_dates.c.trade_date).order_by(known_dates.c.trade_date.desc()).limit(window_size)).all()
    return sorted(set(days))


def load_market_exploration(session: Any, *, run_date: date, required: bool = True):
    from sqlalchemy import select
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from ai_stock_sentinel.daily_radar.market_bar_repository import DEFAULT_MARKET_BAR_DATASET
    recent_dates = _load_archive_dates(session, run_date=run_date, window_size=65, horizon_days=240)
    bars = session.execute(select(
        TaiwanDailyBar.symbol, TaiwanDailyBar.market, TaiwanDailyBar.trade_date,
        TaiwanDailyBar.close, TaiwanDailyBar.volume, TaiwanDailyBar.amount,
        TaiwanDailyBar.is_final, TaiwanDailyBar.adjustment_mode,
    ).where(
        TaiwanDailyBar.trade_date.in_(recent_dates), TaiwanDailyBar.dataset == DEFAULT_MARKET_BAR_DATASET,
        TaiwanDailyBar.adjustment_mode == "unadjusted", TaiwanDailyBar.is_final.is_(True),
    ).order_by(TaiwanDailyBar.symbol, TaiwanDailyBar.trade_date)).all()
    days, missing = market_archive_window(bars, run_date=run_date, window_size=65, archive_dates=recent_dates)
    if missing:
        error = MarketExplorationReadinessError(missing, days)
        if required:
            raise error
        return [], {"run_date": run_date.isoformat(), "missing_markets": error.missing_markets,
                    "missing_market_dates": missing, "archive_dates": error.archive_dates, "status": "unavailable"}
    entries, audit = build_market_exploration(bars, run_date=run_date, archive_dates=days)
    from ai_stock_sentinel.daily_radar.scaled_accumulation import build_scaled_accumulation
    from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
        get_complete_institutional_archive_window, InstitutionalArchiveIntegrityError,
    )
    from ai_stock_sentinel.daily_radar.universe import merge_discovery_universe
    days = days[-60:]
    if len(days) < 60:
        audit["scaled_accumulation"] = {"status": "insufficient_history"}
        return entries, audit
    try:
        archived = get_complete_institutional_archive_window(session, start_date=days[0], end_date=run_date)
        scaled_entries, scaled_audit = build_scaled_accumulation(
            bars, [flow for daily in archived.values() for flow in daily], run_date=run_date, archive_dates=days,
        )
    except InstitutionalArchiveIntegrityError as exc:
        scaled_entries, scaled_audit = [], {"status": "archive_invalid", "code": exc.code,
            "market": exc.market, "trade_date": exc.trade_date.isoformat()}
    audit["scaled_accumulation"] = scaled_audit
    return merge_discovery_universe(entries, scaled_entries), audit


def build_market_exploration(
    bars: Iterable[Any], *, run_date: date, track_limit: int = 50,
    archive_dates: Iterable[date] | None = None,
) -> tuple[list[DailyRadarUniverseEntry], dict[str, Any]]:
    bars = list(bars)
    sessions, missing = market_archive_window(bars, run_date=run_date, window_size=65, archive_dates=archive_dates)
    by_symbol: dict[str, dict[date, Any]] = defaultdict(dict)
    market_dates: dict[str, set[date]] = defaultdict(set)
    for bar in bars:
        if bar.trade_date > run_date or not bar.is_final or bar.adjustment_mode != "unadjusted":
            continue
        market_dates[bar.market].add(bar.trade_date)
        if is_daily_radar_supported_symbol(bar.symbol):
            by_symbol[bar.symbol][bar.trade_date] = bar
    exclusions: dict[str, str] = {}
    if missing:
        return [], {"exploration_version": EXPLORATION_VERSION, "run_date": run_date.isoformat(),
                    "status": "unavailable", "missing_market_dates": missing,
                    "archive_dates": [day.isoformat() for day in sessions],
                    "scanned_symbol_count": len(by_symbol), "eligible_symbol_count": 0, "discovered_symbol_count": 0,
                    "excluded_symbol_reasons": {symbol: "market_archive_history_gap" for symbol in by_symbol},
                    "track_counts": {track: 0 for track in EXPLORATION_TRACKS}}
    ranked: dict[str, list[tuple[str, dict[str, Any]]]] = {track: [] for track in EXPLORATION_TRACKS}
    eligible_count = 0
    for symbol, rows in sorted(by_symbol.items()):
        if run_date not in rows:
            exclusions[symbol] = "current_day_missing"
            continue
        ordered = [rows[day] for day in sorted(rows)]
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
        "archive_dates": [day.isoformat() for day in sessions],
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


def build_turnover_contexts(
    bars: Iterable[Any], *, run_date: date, archive_dates: Iterable[date] | None = None,
) -> dict[str, dict[str, Any]]:
    bars = list(bars)
    days, missing = market_archive_window(bars, run_date=run_date, window_size=20, archive_dates=archive_dates)
    by_symbol: dict[str, dict[date, Any]] = defaultdict(dict)
    for bar in bars:
        if bar.trade_date <= run_date and bar.is_final and bar.adjustment_mode == "unadjusted":
            by_symbol[bar.symbol][bar.trade_date] = bar
    result = {}
    for symbol, rows in by_symbol.items():
        trace = {"source": "taiwan_market_daily_ohlcv", "as_of_date": run_date.isoformat(),
                 "source_dates": [day.isoformat() for day in days], "missing_reason": None}
        if missing:
            result[symbol] = trace | {"missing_reason": "market_archive_history_gap", "missing_market_dates": missing}
            continue
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
    recent_dates = _load_archive_dates(session, run_date=run_date, window_size=20, horizon_days=90)
    # Keep all market rows in this narrow query so a candidate's own gaps cannot shorten its calendar.
    bars = session.execute(select(TaiwanDailyBar.symbol, TaiwanDailyBar.market, TaiwanDailyBar.trade_date,
                                  TaiwanDailyBar.amount, TaiwanDailyBar.is_final, TaiwanDailyBar.adjustment_mode).where(
        TaiwanDailyBar.trade_date.in_(recent_dates), TaiwanDailyBar.dataset == DEFAULT_MARKET_BAR_DATASET,
        TaiwanDailyBar.adjustment_mode == "unadjusted", TaiwanDailyBar.is_final.is_(True),
    )).all()
    required_dates, missing = market_archive_window(bars, run_date=run_date, window_size=20, archive_dates=recent_dates)
    if not recent_dates:
        # No observed archive calendar is the documented legacy absence, not evidence of a missing session.
        missing = {}
    contexts = build_turnover_contexts(bars, run_date=run_date, archive_dates=recent_dates)
    for row in raw_rows:
        technical = dict(row.technical or {})
        ohlcv = dict(technical.get("ohlcv") or {})
        context = contexts.get(row.symbol, {"source": DEFAULT_MARKET_BAR_DATASET,
            "as_of_date": run_date.isoformat(), "source_dates": [day.isoformat() for day in required_dates] if recent_dates else [],
            "missing_reason": "market_archive_history_gap" if missing else "turnover_history_gap",
            **({"missing_market_dates": missing} if missing else {})})
        ohlcv.pop("avg_turnover_value_million", None)
        if "avg_turnover_value_million" in context:
            ohlcv["avg_turnover_value_million"] = context["avg_turnover_value_million"]
        elif context.get("missing_reason") == "market_archive_history_gap":
            # An observed archive gap is not a legacy absence: prefilter must reject it, never estimate it away.
            ohlcv["avg_turnover_value_million"] = None
        ohlcv["turnover_context"] = context
        row.technical = technical | {"ohlcv": ohlcv}
