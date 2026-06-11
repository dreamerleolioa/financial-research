from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from ai_stock_sentinel.daily_radar.market_context import (
    YFinanceMarketIndexContextProvider,
    build_market_context_from_technical_payload,
)


def _payload(
    *,
    close: float,
    previous_close: float,
    ma20: float,
    ma60: float,
    atr14: float = 100.0,
    data_date: str = "2026-06-02",
) -> dict[str, Any]:
    return {
        "ohlcv": {
            "close": close,
            "previous_close": previous_close,
        },
        "indicators": {
            "ma20": ma20,
            "ma60": ma60,
            "atr14": atr14,
        },
        "data_dates": {
            "ohlcv": data_date,
            "technical_indicators": data_date,
        },
    }


def test_market_context_classifies_constructive_regime() -> None:
    context = build_market_context_from_technical_payload(
        _payload(close=22000.0, previous_close=21800.0, ma20=21400.0, ma60=20800.0),
        run_date=date(2026, 6, 2),
        index_symbol="TAIEX",
        yfinance_symbol="^TWII",
    )

    assert context["data_dates"] == {"market_index": "2026-06-02"}
    assert context["market"] | {
        "index_symbol": "TAIEX",
        "regime": "constructive",
        "freshness": "fresh",
        "above_ma20": True,
        "above_ma60": True,
        "volatility_state": "normal",
        "market_risk_flags": [],
    } == context["market"]


def test_market_context_classifies_neutral_regime() -> None:
    context = build_market_context_from_technical_payload(
        _payload(close=22000.0, previous_close=22250.0, ma20=21400.0, ma60=20800.0, atr14=600.0),
        run_date=date(2026, 6, 2),
        index_symbol="TAIEX",
        yfinance_symbol="^TWII",
    )

    assert context["market"]["regime"] == "neutral"
    assert context["market"]["market_risk_flags"] == []


def test_market_context_classifies_risk_off_regime_with_traceable_flag() -> None:
    context = build_market_context_from_technical_payload(
        _payload(close=20500.0, previous_close=21000.0, ma20=21400.0, ma60=20800.0),
        run_date=date(2026, 6, 2),
        index_symbol="TAIEX",
        yfinance_symbol="^TWII",
    )

    assert context["market"]["regime"] == "risk_off"
    assert context["market"]["above_ma20"] is False
    assert context["market"]["above_ma60"] is False
    assert context["market"]["market_risk_flags"] == ["market_weakness"]


def test_market_context_marks_missing_without_faking_constructive_regime() -> None:
    context = build_market_context_from_technical_payload(
        None,
        run_date=date(2026, 6, 2),
        index_symbol="TAIEX",
        yfinance_symbol="^TWII",
    )

    assert context["data_dates"] == {}
    assert context["market"]["regime"] == "unknown"
    assert context["market"]["freshness"] == "missing"
    assert context["market"]["missing_reason"] == "market_index_ohlcv_missing"
    assert "market_weakness" not in context["market"]["market_risk_flags"]


def test_market_context_marks_stale_without_faking_constructive_regime() -> None:
    context = build_market_context_from_technical_payload(
        _payload(close=22000.0, previous_close=21800.0, ma20=21400.0, ma60=20800.0, data_date="2026-05-29"),
        run_date=date(2026, 6, 2),
        index_symbol="TAIEX",
        yfinance_symbol="^TWII",
    )

    assert context["data_dates"] == {"market_index": "2026-05-29"}
    assert context["market"]["regime"] == "unknown"
    assert context["market"]["freshness"] == "stale"
    assert context["market"]["missing_reason"] == "market_index_stale"
    assert "market_weakness" not in context["market"]["market_risk_flags"]


def test_yfinance_market_index_provider_fetches_single_configured_index_without_ticker_calls(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeYFinance:
        def download(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
            calls.append({"symbol": symbol, "kwargs": kwargs})
            dates = pd.bdate_range(end="2026-06-02", periods=60)
            data = []
            for index in range(60):
                price = 21000.0 + index * 10
                data.append([price - 40, price + 80, price - 120, price, 1_000_000 + index])
            return pd.DataFrame(data, index=dates, columns=["Open", "High", "Low", "Close", "Volume"])

        def Ticker(self, symbol: str) -> object:
            raise AssertionError(f"per-symbol yfinance Ticker call is forbidden: {symbol}")

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.market_context.yf", FakeYFinance())

    context = YFinanceMarketIndexContextProvider().build(run_date=date(2026, 6, 2), market="TW")

    assert calls == [
        {
            "symbol": "^TWII",
            "kwargs": {
                "start": date(2026, 2, 2),
                "end": date(2026, 6, 3),
                "interval": "1d",
                "threads": False,
                "progress": False,
            },
        }
    ]
    assert context["market"]["index_symbol"] == "TAIEX"
    assert context["market"]["yfinance_symbol"] == "^TWII"
    assert context["data_dates"] == {"market_index": "2026-06-02"}


def test_yfinance_market_index_provider_marks_fetch_failure_as_missing(monkeypatch) -> None:
    class FakeYFinance:
        def download(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("simulated index outage")

        def Ticker(self, symbol: str) -> object:
            raise AssertionError(f"per-symbol yfinance Ticker call is forbidden: {symbol}")

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.market_context.yf", FakeYFinance())

    context = YFinanceMarketIndexContextProvider().build(run_date=date(2026, 6, 2), market="TW")

    assert context["data_dates"] == {}
    assert context["market"]["regime"] == "unknown"
    assert context["market"]["freshness"] == "missing"
    assert context["market"]["missing_reason"] == "market_index_fetch_failed"
    assert "market_weakness" not in context["market"]["market_risk_flags"]
