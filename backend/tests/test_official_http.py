from __future__ import annotations

from typing import Any

import pytest
from curl_cffi import requests as curl_requests

from ai_stock_sentinel.data_sources import official_http


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_official_request_retries_timeout_before_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def request_get(url: str, **_kwargs: Any) -> _Response:
        calls.append(url)
        if len(calls) == 1:
            raise curl_requests.exceptions.Timeout("temporary timeout")
        return _Response(200)

    monkeypatch.setattr(official_http.curl_requests, "get", request_get)
    monkeypatch.setattr(official_http.time, "sleep", sleeps.append)

    response = official_http.official_request_get("https://official.example.test/data")

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize("status_code", [429, 503])
def test_official_request_retries_transient_http_status(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    responses = iter([_Response(status_code), _Response(200)])
    sleeps: list[float] = []
    monkeypatch.setattr(official_http.curl_requests, "get", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(official_http.time, "sleep", sleeps.append)

    response = official_http.official_request_get("https://official.example.test/data")

    assert response.status_code == 200
    assert sleeps == [0.25]


def test_official_request_does_not_retry_non_transient_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def request_get(*_args: Any, **_kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        return _Response(404)

    monkeypatch.setattr(official_http.curl_requests, "get", request_get)

    response = official_http.official_request_get("https://official.example.test/data")

    assert response.status_code == 404
    assert calls == 1


def test_official_request_raises_after_bounded_timeout_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def request_get(*_args: Any, **_kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        raise curl_requests.exceptions.Timeout("persistent timeout")

    monkeypatch.setattr(official_http.curl_requests, "get", request_get)
    monkeypatch.setattr(official_http.time, "sleep", sleeps.append)

    with pytest.raises(curl_requests.exceptions.Timeout, match="persistent timeout"):
        official_http.official_request_get("https://official.example.test/data")

    assert calls == 3
    assert sleeps == [0.25, 0.5]
