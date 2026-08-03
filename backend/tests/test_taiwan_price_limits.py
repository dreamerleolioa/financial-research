from __future__ import annotations

import json
from threading import Event
from time import sleep

import pytest

from ai_stock_sentinel.data_sources.taiwan_price_limits import (
    TaiwanPriceLimitSnapshot,
    fetch_taiwan_price_limits,
    fetch_taiwan_price_limits_with_deadline,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self._offset = 0

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._body[self._offset:]
            self._offset = len(self._body)
            return chunk
        chunk = self._body[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)


@pytest.mark.parametrize(
    ("symbol", "current_price", "limit_up", "limit_down", "expected_status", "expected_channel"),
    [
        ("2330.TW", 2425.0, "2425.0000", "1985.0000", "limit_up", "tse_2330.tw"),
        ("6488.TWO", 701.0, "855.0000", "701.0000", "limit_down", "otc_6488.tw"),
        ("2330.TW", 2205.0, "2425.0000", "1985.0000", "normal", "tse_2330.tw"),
    ],
)
def test_fetch_taiwan_price_limits_uses_official_bounds(
    symbol: str,
    current_price: float,
    limit_up: str,
    limit_down: str,
    expected_status: str,
    expected_channel: str,
) -> None:
    requests: list[tuple[str, float]] = []

    def opener(request, *, timeout: float):
        requests.append((request.full_url, timeout))
        stock_code = symbol.split(".", maxsplit=1)[0]
        return _FakeResponse({
            "rtcode": "0000",
            "msgArray": [{
                "c": stock_code,
                "z": str(current_price),
                "u": limit_up,
                "w": limit_down,
            }],
        })

    result = fetch_taiwan_price_limits(
        symbol,
        opener=opener,
    )

    assert result.status == expected_status
    assert result.current_price == current_price
    assert result.limit_up_price == float(limit_up)
    assert result.limit_down_price == float(limit_down)
    assert expected_channel in requests[0][0]
    assert requests[0][1] == 0.5


def test_fetch_taiwan_price_limits_returns_unknown_for_unsupported_exchange() -> None:
    def unexpected_opener(*_args, **_kwargs):
        raise AssertionError("unsupported symbols must not call TWSE MIS")

    result = fetch_taiwan_price_limits(
        "AAPL",
        opener=unexpected_opener,
    )

    assert result.status == "unknown"
    assert result.current_price is None
    assert result.limit_up_price is None
    assert result.limit_down_price is None


def test_fetch_taiwan_price_limits_does_not_infer_missing_bounds() -> None:
    def opener(_request, *, timeout: float):
        assert timeout == 0.5
        return _FakeResponse({
            "rtcode": "0000",
            "msgArray": [{"c": "00646", "z": "52.0000", "u": "-", "w": "-"}],
        })

    result = fetch_taiwan_price_limits(
        "00646.TW",
        opener=opener,
    )

    assert result.status == "unknown"
    assert result.current_price == 52.0
    assert result.limit_up_price is None
    assert result.limit_down_price is None


def test_fetch_taiwan_price_limits_rejects_oversized_provider_response() -> None:
    class OversizedResponse(_FakeResponse):
        def read(self, size: int = -1) -> bytes:
            return b"x" * size

    def opener(_request, *, timeout: float):
        assert timeout == 0.5
        return OversizedResponse({})

    with pytest.raises(ValueError, match="exceeds the allowed size"):
        fetch_taiwan_price_limits(
            "2330.TW",
            opener=opener,
        )


def test_fetch_taiwan_price_limits_enforces_total_response_deadline() -> None:
    class SlowResponse(_FakeResponse):
        def read(self, size: int = -1) -> bytes:
            sleep(0.02)
            return super().read(1)

    def opener(_request, *, timeout: float):
        assert timeout == 0.01
        return SlowResponse({
            "rtcode": "0000",
            "msgArray": [{"c": "2330", "z": "100", "u": "110", "w": "90"}],
        })

    with pytest.raises(TimeoutError, match="total deadline"):
        fetch_taiwan_price_limits(
            "2330.TW",
            timeout=0.01,
            opener=opener,
        )


def test_price_limit_deadline_degrades_provider_failure_to_unknown() -> None:
    def raise_provider_error(_symbol: str) -> TaiwanPriceLimitSnapshot:
        raise TimeoutError("price limit timeout")

    result = fetch_taiwan_price_limits_with_deadline(
        "2330.TW",
        fetcher=raise_provider_error,
    )

    assert result == TaiwanPriceLimitSnapshot.unknown()


def test_price_limit_deadline_waits_for_the_full_provider_timeout(monkeypatch) -> None:
    import ai_stock_sentinel.data_sources.taiwan_price_limits as price_limit_module

    expected = TaiwanPriceLimitSnapshot(
        status="normal",
        current_price=100.0,
        limit_up_price=110.0,
        limit_down_price=90.0,
    )
    observed_timeouts: list[float] = []

    class _CompletedFuture:
        def result(self, *, timeout: float) -> TaiwanPriceLimitSnapshot:
            observed_timeouts.append(timeout)
            return expected

    monkeypatch.setattr(
        price_limit_module,
        "_submit_price_limit_fetch",
        lambda **_kwargs: _CompletedFuture(),
    )

    result = fetch_taiwan_price_limits_with_deadline("2330.TW")

    assert result == expected
    assert observed_timeouts == [0.5]


def test_price_limit_deadline_does_not_wait_for_slow_optional_provider() -> None:
    release_provider = Event()

    def slow_provider(_symbol: str) -> TaiwanPriceLimitSnapshot:
        release_provider.wait(timeout=1)
        return TaiwanPriceLimitSnapshot(
            status="normal",
            current_price=100.0,
            limit_up_price=110.0,
            limit_down_price=90.0,
        )

    try:
        result = fetch_taiwan_price_limits_with_deadline(
            "2330.TW",
            resolve_timeout=0,
            fetcher=slow_provider,
        )
    finally:
        release_provider.set()

    assert result == TaiwanPriceLimitSnapshot.unknown()
