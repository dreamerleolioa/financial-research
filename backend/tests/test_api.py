from __future__ import annotations

from dataclasses import asdict
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.models import StockSnapshot

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_SNAPSHOT = StockSnapshot(
    symbol="2330.TW",
    currency="TWD",
    current_price=100.0,
    previous_close=99.0,
    day_open=99.5,
    day_high=101.0,
    day_low=98.5,
    volume=123456,
    recent_closes=[98.0, 99.0, 100.0],
    fetched_at="2026-03-03T00:00:00+00:00",
)

_CLEANED_NEWS = {
    "date": "2026-03-03",
    "title": "台積電 2 月營收年增",
    "mentioned_numbers": ["2,600", "18.2%"],
    "sentiment_label": "positive",
}


def _make_graph(final_state: dict[str, Any]):
    """Build a mock compiled graph that returns *final_state* when invoked."""
    graph = MagicMock()
    graph.invoke.return_value = final_state
    return graph


def _client_with_graph(graph) -> TestClient:
    api.app.dependency_overrides[api.get_graph] = lambda: graph
    return TestClient(api.app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint() -> None:
    client = TestClient(api.app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_analyze_returns_snapshot_and_analysis() -> None:
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果：看漲",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"]["symbol"] == "2330.TW"
    assert body["analysis"] == "分析結果：看漲"
    assert body["cleaned_news"] is None
    assert body["errors"] == []


def test_analyze_returns_cleaned_news_when_present() -> None:
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": _CLEANED_NEWS,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post(
        "/analyze",
        json={
            "symbol": "2330.TW",
            "news_text": "2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cleaned_news"]["sentiment_label"] == "positive"
    assert body["errors"] == []


def test_analyze_raw_news_items_not_exposed_in_response() -> None:
    """raw_news_items is internal graph state and must not appear in the response."""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "raw_news_items": [{"title": "secret"}],
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "raw_news_items" not in response.json()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_analyze_validation_error_when_symbol_empty() -> None:
    client = TestClient(api.app)
    response = client.post("/analyze", json={"symbol": ""})

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_analyze_graph_exception_returns_error_code() -> None:
    graph = MagicMock()
    graph.invoke.side_effect = RuntimeError("yfinance data source temporarily unavailable")
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] == {}
    assert body["analysis"] == ""
    assert body["errors"][0]["code"] == "ANALYZE_RUNTIME_ERROR"


def test_analyze_missing_snapshot_returns_traceable_error() -> None:
    graph = _make_graph(
        {
            "snapshot": None,
            "analysis": None,
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    error_codes = {item["code"] for item in body["errors"]}
    assert "MISSING_SNAPSHOT" in error_codes
    assert "MISSING_ANALYSIS" in error_codes


def test_analyze_graph_errors_propagated_to_response() -> None:
    """Errors accumulated during graph execution are included in response."""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [{"code": "RSS_FETCH_ERROR", "message": "timeout"}],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    error_codes = {item["code"] for item in body["errors"]}
    assert "RSS_FETCH_ERROR" in error_codes
