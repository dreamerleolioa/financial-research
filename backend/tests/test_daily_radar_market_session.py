from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from ai_stock_sentinel.data_sources import official_http
from ai_stock_sentinel.daily_radar import market_session
from ai_stock_sentinel.daily_radar.market_session import (
    MarketSessionProviderError,
    TwseMarketSessionProvider,
)


class FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_twse_market_session_uses_resilient_official_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            FakeResponse({"stat": "SYSTEM_BUSY"}, status_code=503),
            FakeResponse(
                {
                    "stat": "OK",
                    "date": "20260819",
                    "tables": [{"data": [["發行量加權股價指數", "44719.35"]]}],
                }
            ),
        ]
    )
    calls = 0
    sleeps: list[float] = []

    def curl_get(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal calls
        calls += 1
        return next(responses)

    def unexpected_requests_get(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise AssertionError("legacy requests transport should not be used")

    monkeypatch.setattr(official_http.curl_requests, "get", curl_get)
    monkeypatch.setattr(official_http.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        market_session,
        "_import_requests_get",
        lambda: unexpected_requests_get,
        raising=False,
    )

    result = market_session.TwseMarketSessionProvider().resolve(
        run_date=date(2026, 8, 19),
        market="TW",
    )

    assert result.status == "open"
    assert calls == 2
    assert sleeps == [0.25]


def test_twse_market_session_reports_open_for_matching_market_data() -> None:
    requests_seen: list[tuple[str, dict[str, object]]] = []

    def request_get(url: str, **kwargs: object) -> FakeResponse:
        requests_seen.append((url, kwargs))
        return FakeResponse(
            {
                "stat": "OK",
                "date": "20260709",
                "tables": [{"data": [["發行量加權股價指數", "45354.61"]]}],
            }
        )

    provider = TwseMarketSessionProvider(request_get=request_get)

    result = provider.resolve(run_date=date(2026, 7, 9), market="TW")

    assert requests_seen == [
        (
            "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
            {
                "params": {"response": "json", "date": "20260709", "type": "ALLBUT0999"},
                "timeout": 15,
            },
        )
    ]
    assert result.status == "open"
    assert result.run_date == date(2026, 7, 9)
    assert result.provider == "twse"
    assert result.dataset == "MI_INDEX"


@pytest.mark.parametrize("run_date", [date(2026, 7, 10), date(2026, 7, 12)])
def test_twse_market_session_reports_closed_for_official_no_data_response(run_date: date) -> None:
    provider = TwseMarketSessionProvider(
        request_get=lambda *_args, **_kwargs: FakeResponse(
            {"stat": "很抱歉，沒有符合條件的資料!", "type": "ALLBUT0999"}
        )
    )

    result = provider.resolve(run_date=run_date, market="TW")

    assert result.status == "closed"
    assert result.run_date == run_date


def test_twse_market_session_fails_closed_on_unknown_provider_response() -> None:
    provider = TwseMarketSessionProvider(
        request_get=lambda *_args, **_kwargs: FakeResponse({"stat": "SYSTEM_BUSY"})
    )

    with pytest.raises(MarketSessionProviderError) as exc_info:
        provider.resolve(run_date=date(2026, 7, 10), market="TW")

    assert exc_info.value.code == "twse_market_session_status_unknown"


def test_twse_market_session_rejects_mismatched_response_date() -> None:
    provider = TwseMarketSessionProvider(
        request_get=lambda *_args, **_kwargs: FakeResponse(
            {
                "stat": "OK",
                "date": "20260709",
                "tables": [{"data": [["發行量加權股價指數", "45354.61"]]}],
            }
        )
    )

    with pytest.raises(MarketSessionProviderError) as exc_info:
        provider.resolve(run_date=date(2026, 7, 10), market="TW")

    assert exc_info.value.code == "twse_market_session_date_mismatch"
