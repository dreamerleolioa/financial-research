from __future__ import annotations

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
        assert timeout == 20
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
    assert len(calls) == 2
    assert set(calls) == {TWSE_T86_URL, TPEX_3I_URL}
    assert result.payloads_by_symbol["2454.TWO"]["data_dates"] == {
        "institutional_flow": "2026-08-18"
    }


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
