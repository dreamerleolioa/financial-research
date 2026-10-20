from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
import requests

from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundContextPayload, same_day_background_context_is_reusable,
)
from ai_stock_sentinel.daily_radar.default_background_context import DefaultBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.official_background_context import (
    OfficialBackgroundChipContextProvider,
    OfficialBackgroundContextError,
)
from ai_stock_sentinel.data_sources import official_http
from ai_stock_sentinel.phase1_avwap.provider import DailyPriceProviderError, TwseDailyPriceProvider


class Response:
    def __init__(self, payload: dict, status: int = 200, *, invalid_json: bool = False):
        self.payload = payload
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.invalid_json = invalid_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self.invalid_json:
            raise ValueError("private response body must not be logged")
        return self.payload


def prices(month: str) -> dict:
    return {
        "stat": "OK",
        "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
        "data": [[f"115/{month}/15", "100", "1000", "10", "11", "9", "10"]],
    }


def lending(rows: list) -> dict:
    return {
        "stat": "OK",
        "fields": ["成交日期", "證券代號名稱", "成交數量(交易單位)"],
        "data": rows,
    }


class FinMindStub:
    def __init__(self):
        self.calls = []

    def fetch(self, *, symbols, context_types, run_date, market):
        self.calls.extend(context_types)
        for context_type in context_types:
            for symbol in symbols:
                yield BackgroundContextPayload(
                    symbol=symbol,
                    context_type=context_type,
                    applicable_consumers=("daily_radar",),
                    source={"provider": "finmind"},
                    as_of_date=date(2026, 10, 16) if context_type == "lending" else run_date,
                    freshness="fresh",
                    payload={"latest_daily_lending_volume": 123},
                    missing_reason=None,
                    replay_key=f"fixture:{symbol}:{context_type}",
                )


def test_production_lending_uses_official_zero_activity_without_changing_margin(monkeypatch):
    env = json.loads((Path(__file__).parents[2] / "scripts/gcp/production-env.yaml").read_text())
    monkeypatch.delenv("DAILY_RADAR_LENDING_PROVIDER_MODE", raising=False)
    for key in ("DAILY_RADAR_BACKGROUND_PROVIDER_MODE", "DAILY_RADAR_LENDING_PROVIDER_MODE"):
        if key in env:
            monkeypatch.setenv(key, env[key])
    finmind = FinMindStub()
    official = OfficialBackgroundChipContextProvider(
        request_get=lambda *_args, **_kwargs: Response(lending([["115年10月19日", "2330 台積電", "100"]])),
        lookback_trading_days=1,
        max_lookback_calendar_days=1,
    )
    result = list(DefaultBackgroundChipContextProvider(
        finmind_provider=finmind, official_provider=official,
    ).fetch(symbols=["1216.TW"], context_types=["lending", "full_margin"], run_date=date(2026, 10, 19), market="TW"))
    by_type = {item.context_type: item for item in result}
    assert by_type["lending"].as_of_date == date(2026, 10, 19)
    assert by_type["lending"].freshness == "fresh"
    assert by_type["lending"].payload["latest_daily_lending_volume"] == 0
    assert by_type["lending"].payload["zero_filled_day_count"] == 1
    assert by_type["full_margin"].source["provider"] == "finmind"
    assert finmind.calls == ["full_margin"]


@pytest.mark.parametrize("bad_row", [
    ["invalid-date", "1216 統一", "100"],
    ["115年10月19日", "", "100"],
    ["115年10月19日", "1216 統一", "--"],
    ["115年10月19日", "1216 統一", "NaN"],
    ["115年10月19日", "1216 統一", "-1"],
    ["115年10月19日", "1216 統一"],
])
def test_partial_lending_parse_cannot_turn_a_missing_trade_into_zero(bad_row):
    provider = OfficialBackgroundChipContextProvider(
        request_get=lambda *_args, **_kwargs: Response(lending([
            ["115年10月19日", "2330 台積電", "100"], bad_row,
        ])),
        max_lookback_calendar_days=1,
        lookback_trading_days=1,
    )
    with pytest.raises(OfficialBackgroundContextError, match="official_lending_row_invalid"):
        list(provider.fetch(symbols=["1216.TW"], context_types=["lending"], run_date=date(2026, 10, 19), market="TW"))


def test_lending_official_failure_keeps_stale_fallback_instead_of_zero(monkeypatch):
    monkeypatch.setenv("DAILY_RADAR_LENDING_PROVIDER_MODE", "official_first")
    def fail(*_args, **_kwargs):
        raise TimeoutError("source unavailable")
    provider = DefaultBackgroundChipContextProvider(
        provider_mode="finmind_only", finmind_provider=FinMindStub(),
        official_provider=OfficialBackgroundChipContextProvider(request_get=fail),
    )
    [result] = list(provider.fetch(symbols=["1216.TW"], context_types=["lending"], run_date=date(2026, 10, 19), market="TW"))
    assert result.as_of_date == date(2026, 10, 16)
    assert result.payload["latest_daily_lending_volume"] == 123


def test_lending_rejects_unknown_override(monkeypatch):
    monkeypatch.setenv("DAILY_RADAR_LENDING_PROVIDER_MODE", "typo")
    with pytest.raises(ValueError, match="DAILY_RADAR_LENDING_PROVIDER_MODE"):
        DefaultBackgroundChipContextProvider()


def test_lending_requires_a_valid_market_date_before_zero_filling():
    provider = OfficialBackgroundChipContextProvider(
        request_get=lambda *_args, **_kwargs: Response(lending([])),
        lookback_trading_days=1, max_lookback_calendar_days=1,
    )
    with pytest.raises(OfficialBackgroundContextError, match="official_lending_market_date_unavailable"):
        list(provider.fetch(symbols=["1216.TW"], context_types=["lending"], run_date=date(2026, 10, 19), market="TW"))


def test_lending_previous_date_cannot_satisfy_today_with_zero_activity():
    provider = OfficialBackgroundChipContextProvider(
        request_get=lambda *_args, **_kwargs: Response(lending([["115年10月16日", "2330 台積電", "100"]])),
        lookback_trading_days=1, max_lookback_calendar_days=4,
    )
    [result] = list(provider.fetch(symbols=["1216.TW"], context_types=["lending"], run_date=date(2026, 10, 19), market="TW"))
    assert result.as_of_date == date(2026, 10, 16)
    assert not same_day_background_context_is_reusable(result, run_date=date(2026, 10, 19))


@pytest.mark.parametrize("status", [429, 503])
def test_avwap_default_transport_retries_transient_http_only(monkeypatch, status):
    calls = []
    def get(*_args, **kwargs):
        calls.append(kwargs)
        return Response(prices("10"), status if len(calls) == 1 else 200)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(official_http.curl_requests, "get", get)
    monkeypatch.setattr(official_http.time, "sleep", lambda *_args: None)
    rows = TwseDailyPriceProvider().fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19))
    assert len(rows) == 1
    assert len(calls) == 2
    assert all(call.get("verify", True) is True for call in calls)


def test_avwap_timeout_attempts_are_bounded_and_diagnosis_is_sanitized(monkeypatch, caplog):
    calls = 0
    def get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise official_http.curl_requests.exceptions.Timeout("private exception text")
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(official_http.curl_requests, "get", get)
    monkeypatch.setattr(official_http.time, "sleep", lambda *_args: None)
    with pytest.raises(DailyPriceProviderError, match="twse_stock_day_request_failed"):
        TwseDailyPriceProvider().fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19))
    assert calls == 2
    record = next(record for record in caplog.records if record.message.startswith("twse_stock_day_fetch_failed "))
    detail = json.loads(record.message.split(" ", 1)[1])
    assert detail["symbol"] == "4564.TW"
    assert detail["month"] == "2026-10-01"
    assert detail["stage"] == "request"
    assert detail["error_type"] == "Timeout"
    assert "private exception text" not in caplog.text


@pytest.mark.parametrize("status,invalid_json,stage", [(403, False, "http_status"), (200, True, "json")])
def test_avwap_records_http_or_json_failure_without_retrying_invalid_responses(monkeypatch, caplog, status, invalid_json, stage):
    calls = 0
    def get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response({}, status, invalid_json=invalid_json)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(official_http.curl_requests, "get", get)
    with pytest.raises(DailyPriceProviderError, match="twse_stock_day_request_failed"):
        TwseDailyPriceProvider().fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19))
    assert calls == 1
    detail = json.loads(next(r.message for r in caplog.records if r.message.startswith("twse_stock_day_fetch_failed ")).split(" ", 1)[1])
    assert detail["stage"] == stage
    assert detail["http_status"] == status
    assert detail["content_type"] == "application/json"
    assert "private response body" not in caplog.text


def test_avwap_keeps_successful_months_when_another_month_fails():
    calls = Counter()
    def get(_url, *, params, timeout):
        month = params["date"]
        calls[month] += 1
        if month == "20261001" and calls[month] == 1:
            return Response({}, 503)
        return Response(prices(month[4:6]))
    provider = TwseDailyPriceProvider(request_get=get)
    with pytest.raises(DailyPriceProviderError):
        provider.fetch_history("4564.TW", start_date=date(2026, 9, 1), end_date=date(2026, 10, 19))
    rows = provider.fetch_history("4564.TW", start_date=date(2026, 9, 1), end_date=date(2026, 10, 19))
    assert [row.trade_date for row in rows] == [date(2026, 9, 15), date(2026, 10, 15)]
    assert calls == {"20260901": 1, "20261001": 2}


def test_avwap_does_not_cache_an_empty_or_new_asof_month():
    calls = 0
    def get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response(prices("10") if calls > 1 else {"stat": "OK", "fields": [], "data": []})
    provider = TwseDailyPriceProvider(request_get=get)
    assert provider.fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19)) == []
    assert len(provider.fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19))) == 1
    provider.fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 20))
    assert calls == 3


def test_avwap_rejects_wrong_month_and_reuses_no_invalid_data():
    calls = 0
    def get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response(prices("09" if calls == 1 else "10"))
    provider = TwseDailyPriceProvider(request_get=get)
    with pytest.raises(DailyPriceProviderError, match="twse_stock_day_parser_error"):
        provider.fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19))
    assert len(provider.fetch_history("4564.TW", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19))) == 1
    assert calls == 2


def test_avwap_successful_month_cache_is_isolated_by_symbol():
    calls = []
    def get(_url, *, params, timeout):
        calls.append(params["stockNo"])
        return Response(prices("10"))
    provider = TwseDailyPriceProvider(request_get=get)
    for symbol in ["4564.TW", "2330.TW", "4564.TW"]:
        provider.fetch_history(symbol, start_date=date(2026, 10, 1), end_date=date(2026, 10, 19))
    assert calls == ["4564", "2330"]
