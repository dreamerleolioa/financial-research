"""Extend verified Yahoo adjustment history with final official market prices."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any

import pandas as pd

from ai_stock_sentinel.calibration.twse_rights_recovery import FACE_VALUE, _dated_rows, _report_rows
from ai_stock_sentinel.daily_radar.market_bar_repository import DEFAULT_MARKET_BAR_DATASET


PRICE_FIELDS = ("Open", "High", "Low", "Close")


class TechnicalRepairRejected(ValueError):
    """Stable reason plus bounded numeric evidence; never raw response bodies."""

    def __init__(self, reason, *, details=None):
        super().__init__(reason)
        self.details = details or {}


@dataclass(frozen=True)
class VerifiedFaceValueChange:
    symbol: str
    effective_date: date
    exchange_ratio: float
    sources: tuple[dict, dict]


def verify_face_value_change(symbol, *, start_date, conflict_date, end_date, capture):
    """Cross-check the resumption notice and published exchange terms."""
    params = {"response": "json", "startDate": start_date.strftime("%Y%m%d"),
              "endDate": end_date.strftime("%Y%m%d")}
    payload, notice_source = capture(FACE_VALUE, params)
    code = symbol.removesuffix(".TW")
    events = [(day, row) for day, row in _dated_rows(payload, params, "恢復買賣日期", ["詳細資料"])
              if str(row["股票代號"]).strip() == code]
    if len(events) != 1 or not conflict_date < events[0][0] <= end_date:
        raise TechnicalRepairRejected("face_value_event_unverified")
    effective_date, notice = events[0]
    day_key = effective_date.strftime("%Y%m%d")
    payload, terms_source = capture("/rwd/zh/change/TWTB7U", {"response": "json", "date": day_key})
    if not isinstance(payload.get("params"), dict) or payload["params"].get("date") != day_key:
        raise TechnicalRepairRejected("face_value_terms_unverified")
    expected_day = f"{effective_date.year - 1911:03d}/{effective_date:%m/%d}"
    fields = ("股票代號", "恢復買賣日期", "變更股票面額換股率", "變更前面額", "變更後面額", "恢復買賣參考價")
    matches = [row for row in _report_rows(payload, fields)
               if str(row["股票代號"]).strip() == code and row["恢復買賣日期"] == expected_day]
    if len(matches) != 1 or "".join(str(matches[0]["恢復買賣參考價"]).split()) != notice["詳細資料"]:
        raise TechnicalRepairRejected("face_value_terms_unverified")
    terms = matches[0]
    ratio = _number(terms["變更股票面額換股率"])
    old, new = _number(terms["變更前面額"]), _number(terms["變更後面額"])
    if min(old, new, ratio) <= 0 or ratio == 1 or not math.isclose(ratio, old / new, rel_tol=1e-8):
        raise TechnicalRepairRejected("face_value_ratio_unverified")
    return VerifiedFaceValueChange(symbol, effective_date, ratio, (notice_source, terms_source))


def _history_changed(day, field, accepted, observed, source):
    return TechnicalRepairRejected("adjusted_history_changed", details={"conflict": {
        "date": str(day), "field": field, "accepted": float(accepted),
        "observed": float(observed), "source": source}})


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
    adjustment_from_repaired_data: bool = True,
    face_value_change: VerifiedFaceValueChange | None = None,
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
    if face_value_change is not None and (
        unrepaired_history is None
        or face_value_change.symbol != symbol
        or not original_dates[0] < face_value_change.effective_date <= original_dates[-1]
    ):
        raise TechnicalRepairRejected("face_value_event_unverified")
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
    reconciled_dates: set[date] = set()
    if unrepaired_history is not None:
        if unrepaired_history.empty or any(field not in unrepaired_history for field in history_fields):
            raise TechnicalRepairRejected("unrepaired_history_missing")
        dates = [value.date() for value in unrepaired_history.index]
        if len(set(dates)) != len(dates) or dates != sorted(dates) or any(value > run_date for value in dates):
            raise TechnicalRepairRejected("unrepaired_history_dates_invalid")
        unrepaired_by_date = {
            data_date: unrepaired_history.iloc[position] for position, data_date in enumerate(dates)
        }
        # A fallback response is evidence for the complete accepted history,
        # not just for the rows that repair damaged. Validate it before using
        # any row, including agreement with repair's remaining finite values.
        for position, data_date in enumerate(original_dates):
            if data_date not in unrepaired_by_date:
                raise TechnicalRepairRejected("unrepaired_history_missing")
            if data_date not in by_date:
                raise TechnicalRepairRejected("repair_history_missing")
            prices = {
                field: _number(unrepaired_by_date[data_date][field]) for field in history_fields
            }
            if (
                min(prices.values()) <= 0
                or prices["Low"] > min(prices["Open"], prices["Close"])
                or prices["High"] < max(prices["Open"], prices["Close"])
            ):
                raise TechnicalRepairRejected("unrepaired_history_prices_invalid")
            ratio = prices["Adj Close"] / prices["Close"]
            baseline = original.iloc[position]
            for field in PRICE_FIELDS:
                if not math.isclose(
                    _number(baseline[field]), prices[field] * ratio, rel_tol=1e-6, abs_tol=1e-6,
                ):
                    raise _history_changed(data_date, field, baseline[field], prices[field] * ratio, "unrepaired_adjusted")
            candidate = repaired.iloc[by_date[data_date]]
            values = {field: _number(candidate[field]) for field in history_fields if pd.notna(candidate[field])}
            mismatches = [field for field, value in values.items()
                          if not math.isclose(value, prices[field], rel_tol=1e-6)]
            if not mismatches:
                continue
            if (face_value_change is None or data_date >= face_value_change.effective_date
                    or not all(math.isclose(value / face_value_change.exchange_ratio, prices[field], rel_tol=1e-6)
                               for field, value in values.items())):
                field = mismatches[0]
                raise _history_changed(data_date, field, prices[field], values[field], "repaired_raw")
            reconciled_dates.add(data_date)
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
        if missing_prices.any() or data_date in reconciled_dates:
            if unrepaired_history is None:
                raise TechnicalRepairRejected("repair_history_values_missing")
            candidate = unrepaired_by_date[data_date]
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
                raise _history_changed(data_date, field, baseline[field], candidate[field] * ratio, "candidate_adjusted")
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
            "method": ("official_final_bar_with_yahoo_adjustment" if adjustment_from_repaired_data
                       else "official_final_bar_with_yahoo_raw_adjustment"),
            # Identify the response supplying the factor; Repaired? can remain
            # false even when repair=True adjusts dividend factors.
            "adjustment_from_repaired_data": adjustment_from_repaired_data,
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
    if reconciled_dates and face_value_change is not None:
        provenance["repair"]["face_value_change"] = {
            "effective_date": str(face_value_change.effective_date),
            "exchange_ratio": face_value_change.exchange_ratio,
            "sources": list(face_value_change.sources),
            "reconciled_dates": sorted(str(day) for day in reconciled_dates),
        }
    return result, provenance


def verified_rights_extension(*, run_date, prior, observed, bars, sessions, terms, events):
    """Append one official candle to an immutable, previously accepted price series."""
    from datetime import timedelta

    dates = [index.date().isoformat() for index in prior.index]
    if not dates or dates != sorted(set(dates)) or dates[-1] >= str(run_date):
        raise TechnicalRepairRejected("prior_history_dates_invalid")
    pair = [dates[-1], str(run_date)]
    if (sessions != pair or [row["date"] for row in bars] != pair or events
            or not terms or terms['ex_date'] != str(run_date + timedelta(days=1))
            or terms['disclosed_on'] > str(run_date)):
        raise TechnicalRepairRejected("rights_extension_evidence_incomplete")
    from ai_stock_sentinel.daily_radar.disclosed_rights import subscription_factor

    factor = subscription_factor(terms, _number(bars[-1]['close']))
    if not 0 < factor < 1:
        raise TechnicalRepairRejected("rights_extension_factor_invalid")
    observations = {index.date().isoformat(): row for index, row in observed.iterrows()}
    if len(observations) != len(observed) or not set(pair).issubset(observations):
        raise TechnicalRepairRejected("rights_extension_dates_invalid")
    for day, official in zip(pair, bars, strict=True):
        values = {k: _number(official[k.lower()]) for k in PRICE_FIELDS}
        if not 0 < values['Low'] <= min(values['Open'], values['Close']) <= max(values['Open'], values['Close']) <= values['High']:
            raise TechnicalRepairRejected("official_ohlc_invalid")
        for k, value in values.items():
            if not math.isclose(_number(observations[day][k]), value * factor, rel_tol=1e-7, abs_tol=.00005):
                raise TechnicalRepairRejected("rights_extension_factor_mismatch")
            if day == dates[-1] and not math.isclose(_number(prior.iloc[-1][k]), value, rel_tol=1e-7, abs_tol=.005):
                raise TechnicalRepairRejected("prior_official_anchor_mismatch")
    volume = _number(bars[-1]['volume'])
    if volume < 0 or not volume.is_integer():
        raise TechnicalRepairRejected("official_volume_invalid")
    # Re-downloaded history may have been restated even before this issue.
    # The previous accepted snapshot owns every existing OHLCV observation.
    frame = prior.copy()
    frame.loc[pd.Timestamp(run_date), [*PRICE_FIELDS, 'Volume']] = [
        *(bars[-1][k.lower()] for k in PRICE_FIELDS), int(volume)]
    return frame
