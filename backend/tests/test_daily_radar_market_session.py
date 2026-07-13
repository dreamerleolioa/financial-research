from __future__ import annotations

from datetime import date

import pytest

from ai_stock_sentinel.daily_radar.market_session import (
    MarketSessionProviderError,
    TwseMarketSessionProvider,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


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
