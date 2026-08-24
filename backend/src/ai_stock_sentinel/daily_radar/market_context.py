from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any, Protocol

import yfinance as yf

from ai_stock_sentinel.daily_radar.raw_data import (
    _build_technical_payload,
    _frame_on_or_before_run_date,
)


MARKET_INDEX_SYMBOLS: dict[str, tuple[str, str]] = {
    "TW": ("TAIEX", "^TWII"),
    "US": ("SPX", "^GSPC"),
}
MAX_MARKET_INDEX_LAG_DAYS = 2


class MarketIndexContextProvider(Protocol):
    def build(self, *, run_date: date, market: str) -> Mapping[str, Any]: ...


class YFinanceMarketIndexContextProvider:
    def __init__(self, index_symbols: Mapping[str, tuple[str, str]] | None = None) -> None:
        self._index_symbols = dict(index_symbols or MARKET_INDEX_SYMBOLS)

    def build(self, *, run_date: date, market: str) -> Mapping[str, Any]:
        index_symbol, yfinance_symbol = self._index_config(market)
        start_date = run_date - timedelta(days=120)
        end_date = run_date + timedelta(days=1)
        attempts = (
            (
                "download",
                lambda: yf.download(
                    yfinance_symbol,
                    start=start_date,
                    end=end_date,
                    interval="1d",
                    threads=False,
                    progress=False,
                ),
            ),
            (
                "ticker_history",
                lambda: yf.Ticker(yfinance_symbol).history(
                    start=start_date,
                    end=end_date,
                    interval="1d",
                    actions=False,
                    auto_adjust=False,
                ),
            ),
        )
        last_context: dict[str, Any] | None = None
        fallback_triggered = False
        for fetch_method, fetch_history in attempts:
            fallback_triggered = fetch_method != "download"
            try:
                history = fetch_history()
                frame = _frame_on_or_before_run_date(history, run_date=run_date)
                payload = _build_technical_payload(index_symbol, frame, run_date=run_date)
                context = build_market_context_from_technical_payload(
                    payload,
                    run_date=run_date,
                    index_symbol=index_symbol,
                    yfinance_symbol=yfinance_symbol,
                )
            except Exception:
                continue
            context["provider_trace"] = {
                "provider": "yfinance",
                "fetch_method": fetch_method,
                "fallback_triggered": fallback_triggered,
            }
            if last_context is None or _context_freshness_rank(context) > _context_freshness_rank(
                last_context
            ):
                last_context = context
            if market_context_refresh_error(context, run_date=run_date) is None:
                return context

        if last_context is not None:
            last_context["provider_trace"]["fallback_triggered"] = fallback_triggered
            return last_context
        context = _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="missing",
            missing_reason="market_index_fetch_failed",
            data_date=None,
        )
        context["provider_trace"] = {
            "provider": "yfinance",
            "fetch_method": None,
            "fallback_triggered": True,
        }
        return context

    def _index_config(self, market: str) -> tuple[str, str]:
        return self._index_symbols.get(market.upper(), self._index_symbols["TW"])


def build_market_context_from_technical_payload(
    payload: Mapping[str, Any] | None,
    *,
    run_date: date,
    index_symbol: str,
    yfinance_symbol: str,
) -> dict[str, Any]:
    if payload is None:
        return _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="missing",
            missing_reason="market_index_ohlcv_missing",
            data_date=None,
        )

    data_date = _market_index_data_date(payload)
    if data_date is None:
        return _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="missing",
            missing_reason="market_index_data_date_missing",
            data_date=None,
        )
    if (run_date - data_date).days > MAX_MARKET_INDEX_LAG_DAYS:
        return _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="stale",
            missing_reason="market_index_stale",
            data_date=data_date,
        )

    ohlcv = _mapping(payload.get("ohlcv"))
    indicators = _mapping(payload.get("indicators"))
    close = _float(ohlcv.get("close"))
    previous_close = _float(ohlcv.get("previous_close"))
    ma20 = _float(indicators.get("ma20"))
    ma60 = _float(indicators.get("ma60"))
    atr14 = _float(indicators.get("atr14"))

    above_ma20 = close >= ma20 if close is not None and ma20 is not None else None
    above_ma60 = close >= ma60 if close is not None and ma60 is not None else None
    volatility_state = _volatility_state(close, atr14)
    regime = _regime(
        close=close,
        previous_close=previous_close,
        above_ma20=above_ma20,
        above_ma60=above_ma60,
        volatility_state=volatility_state,
    )
    risk_flags = ["market_weakness"] if regime == "risk_off" else []

    return {
        "record_date": run_date.isoformat(),
        "data_dates": {"market_index": data_date.isoformat()},
        "benchmark": {
            "symbol": index_symbol,
            "yfinance_symbol": yfinance_symbol,
            "price_history": _price_history(payload),
            "data_dates": {"market_index": data_date.isoformat()},
        },
        "market": {
            "index_symbol": index_symbol,
            "yfinance_symbol": yfinance_symbol,
            "regime": regime,
            "freshness": "fresh",
            "data_date": data_date.isoformat(),
            "close": close,
            "previous_close": previous_close,
            "ma20": ma20,
            "ma60": ma60,
            "above_ma20": above_ma20,
            "above_ma60": above_ma60,
            "volatility_state": volatility_state,
            "market_risk_flags": risk_flags,
        },
    }


def market_context_refresh_error(
    context: Mapping[str, Any],
    *,
    run_date: date,
) -> dict[str, Any] | None:
    market = _mapping(context.get("market"))
    freshness = str(market.get("freshness") or "missing")
    missing_reason = str(market.get("missing_reason") or "").strip() or None
    if context.get("record_date") != run_date.isoformat():
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": "stale",
            "missing_reason": "market_context_record_date_mismatch",
        }
    if freshness != "fresh":
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": freshness,
            "missing_reason": missing_reason or "market_context_not_fresh",
        }
    data_date_value = market.get("data_date")
    try:
        data_date = date.fromisoformat(str(data_date_value))
    except (TypeError, ValueError):
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": "missing",
            "missing_reason": "market_index_data_date_missing",
        }
    if data_date > run_date or (run_date - data_date).days > MAX_MARKET_INDEX_LAG_DAYS:
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": "stale",
            "missing_reason": "market_index_stale",
        }
    required_values = (
        market.get("close"),
        market.get("previous_close"),
        market.get("ma20"),
        market.get("ma60"),
    )
    if (
        any(_float(value) is None for value in required_values)
        or market.get("volatility_state") == "unknown"
        or market.get("regime") not in {"constructive", "neutral", "risk_off"}
    ):
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": freshness,
            "missing_reason": "market_index_indicators_incomplete",
        }
    return None


def _missing_context(
    *,
    run_date: date,
    index_symbol: str,
    yfinance_symbol: str,
    freshness: str,
    missing_reason: str,
    data_date: date | None,
) -> dict[str, Any]:
    data_dates = {"market_index": data_date.isoformat()} if data_date is not None else {}
    return {
        "record_date": run_date.isoformat(),
        "data_dates": data_dates,
        "market": {
            "index_symbol": index_symbol,
            "yfinance_symbol": yfinance_symbol,
            "regime": "unknown",
            "freshness": freshness,
            "data_date": data_date.isoformat() if data_date is not None else None,
            "missing_reason": missing_reason,
            "volatility_state": "unknown",
            "market_risk_flags": [f"market_context_{freshness}"],
        },
    }


def _market_index_data_date(payload: Mapping[str, Any]) -> date | None:
    data_dates = _mapping(payload.get("data_dates"))
    value = data_dates.get("ohlcv") or data_dates.get("market_index") or data_dates.get("technical_indicators")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _price_history(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"date": str(item.get("date")), "close": item.get("close")}
        for item in _as_list(payload.get("price_history"))
        if isinstance(item, Mapping)
    ]


def _volatility_state(close: float | None, atr14: float | None) -> str:
    if close is None or close <= 0 or atr14 is None:
        return "unknown"
    atr_pct = atr14 / close
    if atr_pct >= 0.04:
        return "high"
    if atr_pct >= 0.03:
        return "elevated"
    if atr_pct >= 0.02:
        return "stable"
    return "normal"


def _regime(
    *,
    close: float | None,
    previous_close: float | None,
    above_ma20: bool | None,
    above_ma60: bool | None,
    volatility_state: str,
) -> str:
    if above_ma20 is False or above_ma60 is False or volatility_state in {"elevated", "high"}:
        return "risk_off"
    if above_ma20 is True and above_ma60 is True and close is not None and previous_close is not None:
        if close >= previous_close and volatility_state in {"normal", "stable"}:
            return "constructive"
    return "neutral"


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _context_freshness_rank(context: Mapping[str, Any]) -> int:
    freshness = _mapping(context.get("market")).get("freshness")
    return {"fresh": 2, "stale": 1, "missing": 0}.get(str(freshness), -1)


__all__ = [
    "MarketIndexContextProvider",
    "YFinanceMarketIndexContextProvider",
    "build_market_context_from_technical_payload",
    "market_context_refresh_error",
]
