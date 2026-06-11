from __future__ import annotations

import pytest

from ai_stock_sentinel.data_sources.finmind_client import (
    FinMindClient,
    FinMindClientError,
    FinMindHourlyRequestLedger,
    FinMindResponseCache,
)


class _FakeFinMindResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_finmind_client_reuses_identical_request_cache_without_extra_budget() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append(dict(params))
        return _FakeFinMindResponse({"status": 200, "data": [{"date": "2026-06-10", "value": 1}]})

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        ledger=FinMindHourlyRequestLedger(clock=lambda: 1000.0),
        cache=FinMindResponseCache(clock=lambda: 1000.0),
        token_request_limit=1,
    )

    first = client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )
    second = client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert first == second == [{"date": "2026-06-10", "value": 1}]
    assert len(calls) == 1


def test_finmind_client_cache_is_scoped_by_token_identity() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append(dict(params))
        return _FakeFinMindResponse({"status": 200, "data": [{"token": params.get("token", "")}]})

    cache = FinMindResponseCache(clock=lambda: 1000.0)
    ledger = FinMindHourlyRequestLedger(clock=lambda: 1000.0)
    first_client = FinMindClient(
        api_token="first-token",
        request_get=fake_get,
        ledger=ledger,
        cache=cache,
        token_request_limit=10,
    )
    second_client = FinMindClient(
        api_token="second-token",
        request_get=fake_get,
        ledger=ledger,
        cache=cache,
        token_request_limit=10,
    )

    first = first_client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )
    second = second_client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert first == [{"token": "first-token"}]
    assert second == [{"token": "second-token"}]
    assert len(calls) == 2


def test_finmind_client_with_injected_request_get_does_not_share_default_cache() -> None:
    def shared_get(url: str, *, params: dict, timeout: int) -> _FakeFinMindResponse:
        return _FakeFinMindResponse({"status": 200, "data": [{"value": "shared"}]})

    def isolated_get(url: str, *, params: dict, timeout: int) -> _FakeFinMindResponse:
        return _FakeFinMindResponse({"status": 200, "data": [{"value": "isolated"}]})

    shared_client = FinMindClient(api_token="test-token", request_get=shared_get)
    shared = shared_client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    isolated_client = FinMindClient(api_token="test-token", request_get=isolated_get)
    isolated = isolated_client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert shared == [{"value": "shared"}]
    assert isolated == [{"value": "isolated"}]


def test_finmind_client_blocks_request_when_hourly_budget_is_exhausted() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append(dict(params))
        return _FakeFinMindResponse({"status": 200, "data": []})

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        ledger=FinMindHourlyRequestLedger(clock=lambda: 2000.0),
        cache=FinMindResponseCache(clock=lambda: 2000.0),
        token_request_limit=1,
    )

    client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    with pytest.raises(FinMindClientError) as exc_info:
        client.fetch_data(
            dataset="TaiwanStockMarginPurchaseShortSale",
            data_id="2454",
            start_date="2026-06-01",
            end_date="2026-06-10",
        )

    assert exc_info.value.code == "quota_exceeded"
    assert len(calls) == 1


def test_finmind_client_resets_budget_on_new_hour_bucket() -> None:
    now = 0.0
    calls: list[dict] = []

    def clock() -> float:
        return now

    def fake_get(url: str, *, params: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append(dict(params))
        return _FakeFinMindResponse({"status": 200, "data": []})

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        ledger=FinMindHourlyRequestLedger(clock=clock),
        cache=FinMindResponseCache(clock=clock, ttl_seconds=0),
        token_request_limit=1,
    )

    client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )
    now = 3600.0
    client.fetch_data(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_id="2454",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert len(calls) == 2
