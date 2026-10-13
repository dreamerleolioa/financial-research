"""Verify reconstructed Yahoo prices before extending adjusted technical history."""
from __future__ import annotations

from datetime import date
import math
from typing import Any

import pandas as pd

from ai_stock_sentinel.daily_radar.market_bar_repository import DEFAULT_MARKET_BAR_DATASET


PRICE_FIELDS = ("Open", "High", "Low", "Close")


class TechnicalRepairRejected(ValueError):
    """A stable reason code; never include symbols or provider response bodies."""


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise TechnicalRepairRejected("invalid_price_or_adjustment") from None
    if isinstance(value, bool) or not math.isfinite(result):
        raise TechnicalRepairRejected("invalid_price_or_adjustment")
    return result


def verified_repaired_frame(
    original: Any,
    repaired: Any,
    official: Any,
    *,
    symbol: str,
    run_date: date,
) -> tuple[Any, dict[str, Any]]:
    market, provider = ("TWO", "tpex") if symbol.endswith(".TWO") else ("TW", "twse")
    if (
        official.symbol != symbol
        or official.trade_date != run_date
        or official.market != market
        or official.source_provider != provider
        or official.dataset != DEFAULT_MARKET_BAR_DATASET
        or official.adjustment_mode != "unadjusted"
        or official.is_final is not True
    ):
        raise TechnicalRepairRejected("official_bar_invalid")
    volume = _number(official.volume)
    if volume < 0 or not volume.is_integer():
        raise TechnicalRepairRejected("official_volume_invalid")
    required = (*PRICE_FIELDS, "Adj Close")
    if original.empty or repaired.empty or any(field not in repaired for field in required):
        raise TechnicalRepairRejected("repair_history_missing")
    original_dates = [value.date() for value in original.index]
    repaired_dates = [value.date() for value in repaired.index]
    if (
        len(set(original_dates)) != len(original_dates)
        or len(set(repaired_dates)) != len(repaired_dates)
        or run_date not in repaired_dates
        or any(value >= run_date for value in original_dates)
    ):
        raise TechnicalRepairRejected("repair_dates_invalid")
    if set(repaired_dates) - set(original_dates) - {run_date}:
        raise TechnicalRepairRejected("intermediate_history_missing")
    by_date = dict(zip(repaired_dates, range(len(repaired_dates))))
    target = repaired.iloc[by_date[run_date]]
    raw_prices = {field: _number(target[field]) for field in PRICE_FIELDS}
    if (
        any(value <= 0 for value in raw_prices.values())
        or raw_prices["Low"] > min(raw_prices["Open"], raw_prices["Close"])
        or raw_prices["High"] < max(raw_prices["Open"], raw_prices["Close"])
    ):
        raise TechnicalRepairRejected("reconstructed_ohlc_invalid")
    for field in PRICE_FIELDS:
        if not math.isclose(
            raw_prices[field], _number(getattr(official, field.lower())),
            rel_tol=1e-6, abs_tol=1e-6,
        ):
            raise TechnicalRepairRejected("official_prices_mismatch")
    factor = _number(target["Adj Close"]) / raw_prices["Close"]
    if not math.isfinite(factor) or factor <= 0:
        raise TechnicalRepairRejected("adjustment_factor_invalid")
    # Every existing price must retain its adjustment basis. Never replace the
    # accepted history with the library's dividend/split repair heuristics.
    for position, data_date in enumerate(original_dates):
        if data_date not in by_date:
            raise TechnicalRepairRejected("repair_history_missing")
        baseline = original.iloc[position]
        candidate = repaired.iloc[by_date[data_date]]
        close = _number(candidate["Close"])
        adjustment = _number(candidate["Adj Close"])
        if close <= 0 or adjustment <= 0:
            raise TechnicalRepairRejected("adjustment_factor_invalid")
        ratio = adjustment / close
        for field in PRICE_FIELDS:
            if not math.isclose(
                _number(baseline[field]), _number(candidate[field]) * ratio,
                rel_tol=1e-6, abs_tol=1e-6,
            ):
                raise TechnicalRepairRejected("adjusted_history_changed")
    appended = original.iloc[[-1]].copy()
    target_index = pd.Timestamp(run_date)
    if original.index.tz is not None:
        target_index = target_index.tz_localize(original.index.tz)
    appended.index = pd.DatetimeIndex([target_index], name=original.index.name)
    for field in PRICE_FIELDS:
        appended[field] = _number(raw_prices[field] * factor)
    appended["Volume"] = int(volume)
    # Keep every accepted historical value; append only this date.
    result = pd.concat([original, appended])
    reconstructed = target.get("Repaired?", False)
    provenance = {
        "provider": "yfinance",
        "price_basis": "adjusted",
        "repair": {
            "method": "yfinance_price_repair",
            "reconstructed": bool(pd.notna(reconstructed) and reconstructed),
            "data_date": run_date.isoformat(),
            "official_price_provider": provider,
            "official_price_dataset": official.source_dataset,
            "volume_provider": provider,
            "adjustment_factor": factor,
        },
    }
    return result, provenance
