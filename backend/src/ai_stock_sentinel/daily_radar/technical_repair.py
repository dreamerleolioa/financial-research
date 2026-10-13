"""Extend verified Yahoo adjustment history with final official market prices."""
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
    unrepaired_history: Any | None = None,
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
    required = (*PRICE_FIELDS, "Adj Close", "Dividends", "Stock Splits")
    if original.empty or repaired.empty or any(field not in repaired for field in required):
        raise TechnicalRepairRejected("repair_history_missing")
    original_dates = [value.date() for value in original.index]
    repaired_dates = [value.date() for value in repaired.index]
    if (
        len(set(original_dates)) != len(original_dates)
        or len(set(repaired_dates)) != len(repaired_dates)
        or run_date not in repaired_dates
        or any(value >= run_date for value in original_dates)
        or original_dates != sorted(original_dates)
        or repaired_dates != sorted(repaired_dates)
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
    official_prices = {
        field: _number(getattr(official, field.lower())) for field in PRICE_FIELDS
    }
    if (
        any(value <= 0 for value in official_prices.values())
        or official_prices["Low"] > min(official_prices["Open"], official_prices["Close"])
        or official_prices["High"] < max(official_prices["Open"], official_prices["Close"])
    ):
        raise TechnicalRepairRejected("official_ohlc_invalid")
    # Yahoo intraday reconstruction can stop before the closing auction. Its
    # opening price still establishes the same currency/share-price units; the
    # final official bar owns the whole day's OHLCV, including the true close.
    if not math.isclose(
        raw_prices["Open"], official_prices["Open"], rel_tol=1e-6, abs_tol=1e-6,
    ):
        raise TechnicalRepairRejected("official_price_units_mismatch")
    factor = _number(target["Adj Close"]) / raw_prices["Close"]
    if not math.isfinite(factor) or factor <= 0:
        raise TechnicalRepairRejected("adjustment_factor_invalid")
    history_fields = (*PRICE_FIELDS, "Adj Close")
    unrepaired_by_date: dict[date, Any] = {}
    if unrepaired_history is not None:
        if unrepaired_history.empty or any(field not in unrepaired_history for field in history_fields):
            raise TechnicalRepairRejected("unrepaired_history_missing")
        dates = [value.date() for value in unrepaired_history.index]
        if len(set(dates)) != len(dates) or dates != sorted(dates) or any(value > run_date for value in dates):
            raise TechnicalRepairRejected("unrepaired_history_dates_invalid")
        unrepaired_by_date = {
            data_date: unrepaired_history.iloc[position] for position, data_date in enumerate(dates)
        }
    # Every existing price must retain its adjustment basis. Never replace the
    # accepted history with the library's dividend/split repair heuristics.
    history_rows: dict[date, Any] = {}
    history_fallback_dates: list[str] = []
    for position, data_date in enumerate(original_dates):
        if data_date not in by_date:
            raise TechnicalRepairRejected("repair_history_missing")
        baseline = original.iloc[position]
        candidate = repaired.iloc[by_date[data_date]]
        missing_prices = candidate.loc[list(history_fields)].isna()
        if missing_prices.any():
            if unrepaired_history is None:
                raise TechnicalRepairRejected("repair_history_values_missing")
            if data_date not in unrepaired_by_date:
                raise TechnicalRepairRejected("unrepaired_history_missing")
            fallback = unrepaired_by_date[data_date]
            fallback_prices = {field: _number(fallback[field]) for field in history_fields}
            if (
                min(fallback_prices.values()) <= 0
                or fallback_prices["Low"] > min(fallback_prices["Open"], fallback_prices["Close"])
                or fallback_prices["High"] < max(fallback_prices["Open"], fallback_prices["Close"])
            ):
                raise TechnicalRepairRejected("unrepaired_history_prices_invalid")
            # A missing cell must not hide a changed finite price or adjustment.
            for field in history_fields:
                if not missing_prices[field] and not math.isclose(
                    _number(candidate[field]), fallback_prices[field], rel_tol=1e-6, abs_tol=0.0,
                ):
                    raise TechnicalRepairRejected("adjusted_history_changed")
            candidate = fallback
            history_fallback_dates.append(data_date.isoformat())
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
        history_rows[data_date] = candidate
    # A changed daily factor needs a reported ex-dividend event. Matching the
    # existing history alone cannot validate a corrupt target-day Adj Close.
    dividend = _number(target["Dividends"])
    split = _number(target["Stock Splits"])
    if dividend < 0 or split < 0:
        raise TechnicalRepairRejected("corporate_action_invalid")
    previous = history_rows[original_dates[-1]]
    previous_close = _number(previous["Close"])
    dividend_adjustment = 1 - dividend / previous_close
    if dividend_adjustment <= 0:
        raise TechnicalRepairRejected("corporate_action_invalid")
    previous_factor = _number(previous["Adj Close"]) / previous_close
    expected_factor = previous_factor / dividend_adjustment
    if not math.isfinite(expected_factor) or not math.isclose(
        factor, expected_factor, rel_tol=1e-6, abs_tol=0.0,
    ):
        raise TechnicalRepairRejected("adjustment_continuity_unverified")
    appended = original.iloc[[-1]].copy()
    target_index = pd.Timestamp(run_date)
    if original.index.tz is not None:
        target_index = target_index.tz_localize(original.index.tz)
    appended.index = pd.DatetimeIndex([target_index], name=original.index.name)
    for field in PRICE_FIELDS:
        appended[field] = _number(official_prices[field] * factor)
    appended["Volume"] = int(volume)
    # Keep every accepted historical value; append only this date.
    result = pd.concat([original, appended])
    provenance = {
        "provider": provider,
        "history_provider": "yfinance",
        "adjustment_provider": "yfinance",
        "price_basis": "adjusted",
        "repair": {
            "method": "official_final_bar_with_yahoo_adjustment",
            # The factor comes from the repair=True response. Repaired? can
            # remain false when yfinance repairs only dividend adjustments.
            "adjustment_from_repaired_data": True,
            "data_date": run_date.isoformat(),
            "official_price_provider": provider,
            "official_price_dataset": official.source_dataset,
            "volume_provider": provider,
            "adjustment_factor": factor,
        },
    }
    if history_fallback_dates:
        provenance["repair"]["history_validation_fallback"] = {
            "provider": "yfinance", "repair_enabled": False,
            "data_dates": history_fallback_dates,
        }
    return result, provenance
