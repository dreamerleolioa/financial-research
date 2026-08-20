from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

from ai_stock_sentinel.data_sources import official_http
from ai_stock_sentinel.daily_radar import institutional_evidence
from ai_stock_sentinel.daily_radar.institutional_evidence import (
    OfficialInstitutionalEvidenceProvider,
    TPEX_3I_URL,
    TWSE_T86_URL,
    _build_payload,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_twse_institutional_evidence_uses_official_report_route() -> None:
    assert TWSE_T86_URL == "https://www.twse.com.tw/fund/T86"


def test_twse_institutional_evidence_retries_transient_timeout() -> None:
    calls = 0
    timeouts: list[float] = []

    def request_get(url: str, *, params: dict[str, str], timeout: float) -> _Response:
        nonlocal calls
        calls += 1
        timeouts.append(timeout)
        assert url == TWSE_T86_URL
        if calls == 1:
            raise TimeoutError("temporary TWSE timeout")
        row: list[Any] = [""] * 19
        row[0] = "2330"
        return _Response({"stat": "OK", "data": [row]})

    result = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=1,
        calendar_window_days=0,
    ).fetch(["2330.TW"], run_date=date(2026, 8, 13))

    assert calls == 2
    assert timeouts == [5.0, 5.0]
    assert result.errors == []
    assert set(result.payloads_by_symbol) == {"2330.TW"}


def test_twse_institutional_evidence_retries_invalid_json() -> None:
    calls = 0

    class InvalidJsonResponse(_Response):
        def json(self) -> dict[str, Any]:
            raise json.JSONDecodeError("temporary non-JSON body", "<html>", 0)

    def request_get(url: str, *, params: dict[str, str], timeout: float) -> _Response:
        nonlocal calls
        calls += 1
        assert url == TWSE_T86_URL
        if calls == 1:
            return InvalidJsonResponse({})
        row: list[Any] = [""] * 19
        row[0] = "2330"
        return _Response({"stat": "OK", "data": [row]})

    result = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=1,
        calendar_window_days=0,
    ).fetch(["2330.TW"], run_date=date(2026, 8, 13))

    assert calls == 2
    assert result.errors == []
    assert set(result.payloads_by_symbol) == {"2330.TW"}


def test_official_institutional_evidence_projects_twse_and_tpex_rows() -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    twse_row: list[Any] = [""] * 19
    twse_row[0] = "2330"
    twse_row[4] = "1,000"
    twse_row[10] = "200"
    twse_row[11] = "100"
    twse_row[18] = "1,300"
    tpex_row: list[Any] = [""] * 24
    tpex_row[0] = "2454"
    tpex_row[4] = "2,000"
    tpex_row[13] = "300"
    tpex_row[22] = "-100"
    tpex_row[23] = "2,200"

    def request_get(url: str, *, params: dict[str, str], timeout: int) -> _Response:
        assert timeout == 5
        calls.append((url, params))
        if url == TWSE_T86_URL:
            return _Response({"stat": "OK", "data": [twse_row]})
        assert url == TPEX_3I_URL
        return _Response({"stat": "ok", "tables": [{"data": [tpex_row]}]})

    provider = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        calendar_window_days=0,
    )
    result = provider.fetch(["2330.TW", "2454.TWO"], run_date=date(2026, 8, 13))

    assert result.errors == []
    assert set(result.payloads_by_symbol) == {"2330.TW", "2454.TWO"}
    assert result.payloads_by_symbol["2330.TW"]["three_party_net_shares"] == 1300.0
    assert result.payloads_by_symbol["2454.TWO"]["foreign_net_shares"] == 2000.0
    assert result.payloads_by_symbol["2454.TWO"]["flow_state"] == "same_day_net_buy"
    params_by_url = {url: params for url, params in calls}
    assert params_by_url[TWSE_T86_URL]["date"] == "20260813"
    assert params_by_url[TPEX_3I_URL]["d"] == "115/08/13"


def test_default_official_client_uses_curl_compatible_transport(monkeypatch) -> None:
    tpex_row: list[Any] = [""] * 24
    tpex_row[0] = "2454"
    tpex_row[4] = "2,000"
    tpex_row[13] = "300"
    tpex_row[22] = "-100"
    tpex_row[23] = "2,200"
    calls: list[str] = []

    def curl_get(url: str, **_kwargs: Any) -> _Response:
        calls.append(url)
        if url == TWSE_T86_URL:
            return _Response({"stat": "OK", "data": []})
        assert url == TPEX_3I_URL
        return _Response({"stat": "ok", "tables": [{"data": [tpex_row]}]})

    monkeypatch.setattr(official_http.curl_requests, "get", curl_get)
    assert institutional_evidence._import_requests_get() is official_http.official_request_get

    provider = OfficialInstitutionalEvidenceProvider(calendar_window_days=0)
    result = provider.fetch(["2454.TWO"], run_date=date(2026, 8, 18))

    assert result.errors == []
    assert calls == [TPEX_3I_URL]
    assert result.payloads_by_symbol["2454.TWO"]["data_dates"] == {
        "institutional_flow": "2026-08-18"
    }


def test_institutional_evidence_stops_after_each_market_has_recent_days() -> None:
    calls: list[tuple[str, str]] = []

    def request_get(url: str, *, params: dict[str, str], timeout: int) -> _Response:
        assert timeout == 5
        query_date = params.get("date") or params["d"]
        calls.append((url, query_date))
        if url == TWSE_T86_URL:
            row: list[Any] = [""] * 19
            row[0] = "2330"
            return _Response({"stat": "OK", "data": [row]})
        row = [""] * 24
        row[0] = "2454"
        return _Response({"stat": "ok", "tables": [{"data": [row]}]})

    result = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=2,
        calendar_window_days=10,
    ).fetch(["2330.TW", "2454.TWO"], run_date=date(2026, 8, 13))

    assert result.errors == []
    assert set(calls[:2]) == {
        (TWSE_T86_URL, "20260813"),
        (TPEX_3I_URL, "115/08/13"),
    }
    assert set(calls[2:]) == {
        (TWSE_T86_URL, "20260812"),
        (TPEX_3I_URL, "115/08/12"),
    }


def test_institutional_evidence_tolerates_replaced_historical_timeout() -> None:
    calls: list[tuple[str, str]] = []

    def request_get(url: str, *, params: dict[str, str], timeout: int) -> _Response:
        assert timeout == 5
        query_date = params.get("date") or params["d"]
        calls.append((url, query_date))
        if url == TWSE_T86_URL and params["date"] == "20260812":
            raise TimeoutError("historical TWSE timeout")
        if url == TWSE_T86_URL:
            row: list[Any] = [""] * 19
            row[0] = "2330"
            return _Response({"stat": "OK", "data": [row]})
        row = [""] * 24
        row[0] = "2454"
        return _Response({"stat": "ok", "tables": [{"data": [row]}]})

    result = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=2,
        calendar_window_days=10,
    ).fetch(["2330.TW", "2454.TWO"], run_date=date(2026, 8, 13))

    assert result.errors == []
    assert {query_date for _url, query_date in calls} == {
        "20260813",
        "115/08/13",
        "20260812",
        "115/08/12",
        "20260811",
    }


def test_institutional_evidence_keeps_non_transport_historical_error() -> None:
    def request_get(url: str, *, params: dict[str, str], timeout: int) -> _Response:
        assert timeout == 5
        if url == TWSE_T86_URL and params["date"] == "20260812":
            raise ValueError("invalid historical payload")
        if url == TWSE_T86_URL:
            row: list[Any] = [""] * 19
            row[0] = "2330"
            return _Response({"stat": "OK", "data": [row]})
        row = [""] * 24
        row[0] = "2454"
        return _Response({"stat": "ok", "tables": [{"data": [row]}]})

    result = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=2,
        calendar_window_days=3,
    ).fetch(["2330.TW", "2454.TWO"], run_date=date(2026, 8, 13))

    assert result.errors == [
        {
            "market": "TWSE",
            "query_date": "2026-08-12",
            "error_type": "ValueError",
        }
    ]


def test_institutional_evidence_caps_request_timeout_to_remaining_deadline(
    monkeypatch,
) -> None:
    clock = iter([100.0, 102.0])
    monkeypatch.setattr(institutional_evidence, "monotonic", lambda: next(clock))
    timeouts: list[float] = []

    def request_get(url: str, *, params: dict[str, str], timeout: float) -> _Response:
        timeouts.append(timeout)
        if url == TWSE_T86_URL:
            row: list[Any] = [""] * 19
            row[0] = "2330"
            return _Response({"stat": "OK", "data": [row]})
        row = [""] * 24
        row[0] = "2454"
        return _Response({"stat": "ok", "tables": [{"data": [row]}]})

    result = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=1,
        calendar_window_days=0,
        total_timeout=3,
    ).fetch(["2330.TW", "2454.TWO"], run_date=date(2026, 8, 13))

    assert result.errors == []
    assert timeouts == [0.5, 0.5]


def test_institutional_evidence_recomputes_deadline_for_queued_markets() -> None:
    timeouts: list[float] = []

    def request_get(url: str, *, params: dict[str, str], timeout: float) -> _Response:
        timeouts.append(timeout)
        time.sleep(min(0.7, timeout))
        if url == TWSE_T86_URL:
            row: list[Any] = [""] * 19
            row[0] = "2330"
            return _Response({"stat": "OK", "data": [row]})
        row = [""] * 24
        row[0] = "2454"
        return _Response({"stat": "ok", "tables": [{"data": [row]}]})

    OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=1,
        calendar_window_days=0,
        max_workers=1,
        total_timeout=1,
    ).fetch(["2330.TW", "2454.TWO"], run_date=date(2026, 8, 13))

    assert len(timeouts) == 2
    assert timeouts[1] < 0.5


def test_institutional_evidence_keeps_current_date_timeout_as_error() -> None:
    def request_get(url: str, *, params: dict[str, str], timeout: int) -> _Response:
        assert timeout == 5
        if url == TWSE_T86_URL and params["date"] == "20260813":
            raise TimeoutError("current TWSE timeout")
        if url == TWSE_T86_URL:
            row: list[Any] = [""] * 19
            row[0] = "2330"
            return _Response({"stat": "OK", "data": [row]})
        row = [""] * 24
        row[0] = "2454"
        return _Response({"stat": "ok", "tables": [{"data": [row]}]})

    result = OfficialInstitutionalEvidenceProvider(
        request_get=request_get,
        recent_market_days=2,
        calendar_window_days=3,
    ).fetch(["2330.TW", "2454.TWO"], run_date=date(2026, 8, 13))

    assert result.errors == [
        {
            "market": "TWSE",
            "query_date": "2026-08-13",
            "error_type": "TimeoutError",
        }
    ]


def test_institutional_evidence_default_client_uses_two_bounded_attempts_per_date(
    monkeypatch,
) -> None:
    calls = 0

    def curl_get(*_args: Any, **_kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        raise official_http.curl_requests.exceptions.Timeout("official timeout")

    monkeypatch.setattr(official_http.curl_requests, "get", curl_get)
    monkeypatch.setattr(official_http.time, "sleep", lambda _seconds: None)
    result = OfficialInstitutionalEvidenceProvider(
        calendar_window_days=0,
        total_timeout=5,
    ).fetch(["2330.TW"], run_date=date(2026, 8, 13))

    assert calls == 2
    assert result.errors == [
        {
            "market": "TWSE",
            "query_date": "2026-08-13",
            "error_type": "Timeout",
        },
        {
            "market": "TWSE",
            "error_type": "institutional_evidence_lookback_incomplete",
            "market_day_count": 0,
            "required_market_day_count": 1,
        },
    ]


def test_institutional_evidence_does_not_label_prior_date_as_same_day() -> None:
    payload = _build_payload(
        "2330.TW",
        {
            date(2026, 8, 12): {
                "foreign": 1000.0,
                "trust": 200.0,
                "dealer": 100.0,
                "total": 1300.0,
            }
        },
        run_date=date(2026, 8, 13),
        active_dates=[date(2026, 8, 13), date(2026, 8, 12)],
    )

    assert "same_day_actor" not in payload
    assert "same_day_net_buy" not in payload
    assert payload["data_dates"]["institutional_flow"] == "2026-08-12"
