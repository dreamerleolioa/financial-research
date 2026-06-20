from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class DailyPriceBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    estimated_amount: bool = False


class Phase1AvwapDataError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_phase1_avwap_payload(
    *,
    symbol: str,
    bars: Iterable[DailyPriceBar],
    data_date: date,
    dataset: str,
    adjustment_mode: str,
) -> dict[str, Any]:
    ordered_bars = sorted(
        (bar for bar in bars if bar.trade_date <= data_date),
        key=lambda bar: bar.trade_date,
    )
    if not ordered_bars:
        raise Phase1AvwapDataError("daily_price_history_unavailable")

    latest = ordered_bars[-1]
    if latest.trade_date != data_date:
        raise Phase1AvwapDataError("daily_price_row_missing_for_data_date")

    anchors = {
        "swing_low_60d": _anchor_payload(
            ordered_bars,
            anchor=_lowest_low(ordered_bars[-60:]),
            anchor_reason="swing_low_60d",
            current_close=latest.close,
        ),
        "breakout_20d": _anchor_payload(
            ordered_bars,
            anchor=_highest_high(ordered_bars[-20:]),
            anchor_reason="breakout_20d_high",
            current_close=latest.close,
        ),
        "high_volume_60d": _anchor_payload(
            ordered_bars,
            anchor=_highest_volume(ordered_bars[-60:]),
            anchor_reason="high_volume_60d",
            current_close=latest.close,
        ),
    }
    estimated = any(bar.estimated_amount for bar in ordered_bars)
    return {
        "symbol": symbol,
        "data_date": data_date.isoformat(),
        "dataset": dataset,
        "adjustment_mode": adjustment_mode,
        "source": {
            "provider": "finmind",
            "dataset": dataset,
            "adjustment_mode": adjustment_mode,
        },
        "source_granularity": "daily",
        "is_final": True,
        "ohlcv": {
            "open": latest.open,
            "high": latest.high,
            "low": latest.low,
            "close": latest.close,
            "volume": latest.volume,
            "amount": latest.amount,
        },
        "bars": [_bar_payload(bar) for bar in ordered_bars],
        "anchors": anchors,
        "data_quality": {
            "estimated": estimated,
            "source_granularity": "daily",
            "rows_used": len(ordered_bars),
            "missing_reason": None,
        },
    }


def build_missing_phase1_avwap_payload(
    *,
    symbol: str,
    data_date: date,
    dataset: str,
    adjustment_mode: str,
    missing_reason: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "data_date": data_date.isoformat(),
        "dataset": dataset,
        "adjustment_mode": adjustment_mode,
        "source": {
            "provider": "finmind",
            "dataset": dataset,
            "adjustment_mode": adjustment_mode,
        },
        "source_granularity": "daily",
        "is_final": True,
        "anchors": {},
        "data_quality": {
            "estimated": False,
            "source_granularity": "daily",
            "rows_used": 0,
            "missing_reason": missing_reason,
        },
    }


def _anchor_payload(
    bars: list[DailyPriceBar],
    *,
    anchor: DailyPriceBar,
    anchor_reason: str,
    current_close: float,
) -> dict[str, Any]:
    anchor_bars = [bar for bar in bars if bar.trade_date >= anchor.trade_date]
    avwap = _avwap(anchor_bars)
    return {
        "available": True,
        "anchor_date": anchor.trade_date.isoformat(),
        "anchor_reason": anchor_reason,
        "avwap": avwap,
        "distance_to_avwap_pct": _pct_distance(current_close, avwap),
        "source_granularity": "daily",
        "estimated": any(bar.estimated_amount for bar in anchor_bars),
    }


def _bar_payload(bar: DailyPriceBar) -> dict[str, Any]:
    return {
        "date": bar.trade_date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "amount": bar.amount,
        "estimated_amount": bar.estimated_amount,
    }


def _avwap(bars: list[DailyPriceBar]) -> float:
    volume = sum(bar.volume for bar in bars)
    if volume <= 0:
        raise ValueError("positive volume is required")
    return round(sum(bar.amount for bar in bars) / volume, 4)


def _pct_distance(price: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return round((price - reference) / reference * 100, 4)


def _lowest_low(bars: list[DailyPriceBar]) -> DailyPriceBar:
    return min(bars, key=lambda bar: (bar.low, bar.trade_date))


def _highest_high(bars: list[DailyPriceBar]) -> DailyPriceBar:
    return max(bars, key=lambda bar: (bar.high, bar.trade_date))


def _highest_volume(bars: list[DailyPriceBar]) -> DailyPriceBar:
    return max(bars, key=lambda bar: (bar.volume, bar.trade_date))


__all__ = [
    "DailyPriceBar",
    "Phase1AvwapDataError",
    "build_missing_phase1_avwap_payload",
    "build_phase1_avwap_payload",
]
