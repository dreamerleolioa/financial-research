from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event, Lock, Thread

import pytest

import ai_stock_sentinel.data_sources.finmind_client as finmind_client_module
from ai_stock_sentinel.data_sources.finmind_client import (
    FinMindClient,
    FinMindClientError,
    FinMindHourlyRequestLedger,
    FinMindResponseCache,
)
from ai_stock_sentinel.data_sources.finmind_token import FinMindTokenManager


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

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append({"params": dict(params), "headers": dict(headers)})
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
    assert calls[0]["headers"] == {"Authorization": "Bearer test-token"}
    assert "token" not in calls[0]["params"]


def test_finmind_client_cache_is_scoped_by_token_identity() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append({"params": dict(params), "headers": dict(headers)})
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        return _FakeFinMindResponse({"status": 200, "data": [{"token": token}]})

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
    assert all("token" not in call["params"] for call in calls)


def test_finmind_client_with_injected_request_get_does_not_share_default_cache() -> None:
    def shared_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        return _FakeFinMindResponse({"status": 200, "data": [{"value": "shared"}]})

    def isolated_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
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


def test_finmind_client_wraps_token_acquisition_failure() -> None:
    def failing_token_getter() -> str:
        raise RuntimeError("simulated login failure")

    client = FinMindClient(
        request_get=lambda *args, **kwargs: pytest.fail("data endpoint should not be called"),
        token_getter=failing_token_getter,
    )

    with pytest.raises(FinMindClientError) as exc_info:
        client.fetch_data(
            dataset="TaiwanStockPrice",
            data_id="6488",
            start_date="2026-06-01",
            end_date="2026-06-05",
        )

    assert exc_info.value.code == "token_error"
    assert exc_info.value.dataset == "TaiwanStockPrice"
    assert "simulated login failure" not in exc_info.value.message


def test_finmind_token_manager_keeps_static_token_after_invalidation() -> None:
    manager = FinMindTokenManager(
        user_id="legacy-user",
        password="legacy-password",
        static_token="static-token",
    )
    manager._refresh = lambda: pytest.fail("static token must not fall back to legacy login")  # type: ignore[method-assign]

    assert manager.token == "static-token"
    manager.invalidate()
    assert manager.token == "static-token"


def test_finmind_client_retries_transient_request_error_before_success() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append({"params": dict(params), "headers": dict(headers), "timeout": timeout})
        if len(calls) == 1:
            raise RuntimeError("read timed out")
        return _FakeFinMindResponse({"status": 200, "data": [{"date": "2026-06-10", "value": 1}]})

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        ledger=FinMindHourlyRequestLedger(clock=lambda: 1000.0),
        cache=FinMindResponseCache(clock=lambda: 1000.0),
        token_request_limit=3,
        request_retries=2,
        retry_backoff_seconds=0,
    )

    result = client.fetch_data(
        dataset="TaiwanStockSecuritiesLending",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert result == [{"date": "2026-06-10", "value": 1}]
    assert len(calls) == 2
    assert [call["timeout"] for call in calls] == [30, 30]


def test_finmind_client_releases_request_capacity_during_retry_backoff() -> None:
    calls = 0
    capacity = BoundedSemaphore(1)
    capacity_available_during_backoff: list[bool] = []

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("read timed out")
        return _FakeFinMindResponse({"status": 200, "data": []})

    def fake_sleep(seconds: float) -> None:
        acquired = capacity.acquire(blocking=False)
        capacity_available_during_backoff.append(acquired)
        if acquired:
            capacity.release()

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        request_retries=1,
        retry_backoff_seconds=1,
        sleep=fake_sleep,
        request_capacity=capacity,
    )

    client.fetch_data(
        dataset="TaiwanStockSecuritiesLending",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert capacity_available_during_backoff == [True]


def test_finmind_client_fetch_data_allows_request_timeout_override() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append({"params": dict(params), "headers": dict(headers), "timeout": timeout})
        return _FakeFinMindResponse({"status": 200, "data": []})

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        ledger=FinMindHourlyRequestLedger(clock=lambda: 1000.0),
        cache=FinMindResponseCache(clock=lambda: 1000.0),
    )

    client.fetch_data(
        dataset="TaiwanStockSecuritiesLending",
        data_id="2330",
        start_date="2026-06-01",
        end_date="2026-06-10",
        timeout=45,
    )

    assert calls[0]["timeout"] == 45


def test_finmind_clients_share_request_capacity_across_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    active_requests = 0
    max_active_requests = 0
    lock = Lock()
    capacity = BoundedSemaphore(2)
    two_requests_started = Event()
    two_requests_rejected = Event()
    release_requests = Event()
    rejected_requests = 0
    monkeypatch.setattr(finmind_client_module, "_DEFAULT_REQUEST_CAPACITY", capacity)

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        nonlocal active_requests, max_active_requests
        with lock:
            active_requests += 1
            max_active_requests = max(max_active_requests, active_requests)
            if active_requests == 2:
                two_requests_started.set()
        try:
            release_requests.wait(timeout=1)
            return _FakeFinMindResponse({"status": 200, "data": [{"data_id": params["data_id"]}]})
        finally:
            with lock:
                active_requests -= 1

    clients = [
        FinMindClient(
            api_token="test-token",
            request_get=fake_get,
        )
        for _ in range(4)
    ]

    def fetch(item: int) -> list[dict] | str:
        nonlocal rejected_requests
        try:
            return clients[item].fetch_data(
                dataset="TaiwanStockSecuritiesLending",
                data_id=str(2300 + item),
                start_date="2026-06-01",
                end_date="2026-06-10",
            )
        except FinMindClientError as exc:
            if exc.code == "capacity_exhausted":
                with lock:
                    rejected_requests += 1
                    if rejected_requests == 2:
                        two_requests_rejected.set()
            return exc.code

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch, item) for item in range(4)]
        assert two_requests_started.wait(timeout=1)
        assert two_requests_rejected.wait(timeout=1)
        release_requests.set()
        results = [future.result() for future in futures]

    assert sum(result == "capacity_exhausted" for result in results) == 2
    assert max_active_requests == 2


def test_finmind_client_fails_fast_without_spending_budget_when_request_capacity_is_full() -> None:
    calls: list[dict] = []
    capacity = BoundedSemaphore(1)
    assert capacity.acquire(blocking=False)

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append(dict(params))
        return _FakeFinMindResponse({"status": 200, "data": []})

    ledger = FinMindHourlyRequestLedger(clock=lambda: 1000.0)
    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        ledger=ledger,
        token_request_limit=1,
        request_capacity=capacity,
    )

    try:
        with pytest.raises(FinMindClientError) as exc_info:
            client.fetch_data(
                dataset="TaiwanStockSecuritiesLending",
                data_id="2330",
                start_date="2026-06-01",
                end_date="2026-06-10",
            )
    finally:
        capacity.release()

    assert exc_info.value.code == "capacity_exhausted"
    assert calls == []

    client.fetch_data(
        dataset="TaiwanStockSecuritiesLending",
        data_id="2331",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )
    with pytest.raises(FinMindClientError) as quota_exc_info:
        client.fetch_data(
            dataset="TaiwanStockSecuritiesLending",
            data_id="2332",
            start_date="2026-06-01",
            end_date="2026-06-10",
        )

    assert quota_exc_info.value.code == "quota_exceeded"
    assert [call["data_id"] for call in calls] == ["2331"]


def test_finmind_client_can_wait_for_capacity_within_a_bounded_deadline() -> None:
    capacity = BoundedSemaphore(1)
    assert capacity.acquire(blocking=False)
    started = Event()
    completed = Event()
    results: list[list[dict]] = []
    errors: list[Exception] = []

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        return _FakeFinMindResponse({"status": 200, "data": [{"data_id": params["data_id"]}]})

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        request_retries=0,
        request_capacity=capacity,
    )

    def fetch() -> None:
        started.set()
        try:
            results.append(
                client.fetch_data(
                    dataset="TaiwanStockSecuritiesLending",
                    data_id="2330",
                    start_date="2026-06-01",
                    end_date="2026-06-10",
                    capacity_wait_seconds=0.5,
                )
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()

    worker = Thread(target=fetch)
    worker.start()
    try:
        assert started.wait(timeout=1)
        assert not completed.wait(timeout=0.05)
    finally:
        capacity.release()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert errors == []
    assert results == [[{"data_id": "2330"}]]


def test_default_request_capacity_reads_environment_when_first_client_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finmind_client_module, "_DEFAULT_MAX_CONCURRENT_REQUESTS", None)
    monkeypatch.setattr(finmind_client_module, "_DEFAULT_REQUEST_CAPACITY", None)
    monkeypatch.setenv("FINMIND_MAX_CONCURRENT_REQUESTS", "2")

    first_client = FinMindClient(api_token="test-token", request_get=lambda *args, **kwargs: None)
    second_client = FinMindClient(api_token="test-token", request_get=lambda *args, **kwargs: None)

    assert finmind_client_module.finmind_max_concurrent_requests() == 2
    assert first_client._request_capacity is second_client._request_capacity


def test_finmind_client_raises_request_error_after_retry_budget_is_exhausted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
        calls.append({"params": dict(params), "headers": dict(headers)})
        raise RuntimeError(f"request failed with secret {headers['Authorization']}")

    client = FinMindClient(
        api_token="test-token",
        request_get=fake_get,
        ledger=FinMindHourlyRequestLedger(clock=lambda: 1000.0),
        cache=FinMindResponseCache(clock=lambda: 1000.0),
        token_request_limit=3,
        request_retries=2,
        retry_backoff_seconds=0,
    )

    with pytest.raises(FinMindClientError) as exc_info:
        client.fetch_data(
            dataset="TaiwanStockSecuritiesLending",
            data_id="2330",
            start_date="2026-06-01",
            end_date="2026-06-10",
        )

    assert exc_info.value.code == "request_error"
    assert "test-token" not in exc_info.value.message
    assert "test-token" not in caplog.text
    assert "test-token" not in "".join(traceback.format_exception(exc_info.value))
    assert len(calls) == 3


def test_finmind_client_blocks_request_when_hourly_budget_is_exhausted() -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
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

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> _FakeFinMindResponse:
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
