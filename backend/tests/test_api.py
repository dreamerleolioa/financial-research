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


def test_analyze_response_includes_analysis_detail() -> None:
    """AnalyzeResponse 必須包含 analysis_detail 欄位（含 summary/risks/technical_signal）。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "台積電股價穩定",
            "analysis_detail": {
                "summary": "台積電股價穩定，技術面偏多。",
                "risks": ["外資動向不確定", "匯率風險"],
                "technical_signal": "bullish",
            },
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "analysis_detail" in body, "Missing 'analysis_detail' in response"
    detail = body["analysis_detail"]
    assert detail["summary"] == "台積電股價穩定，技術面偏多。"
    assert detail["risks"] == ["外資動向不確定", "匯率風險"]
    assert detail["technical_signal"] == "bullish"


def test_analyze_response_includes_strategy_fields() -> None:
    """AnalyzeResponse 必須包含 strategy/confidence 欄位（值可為 None）。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
            "confidence_score": 65,
            "cross_validation_note": "三維共振，信心偏高",
            "strategy_type": "mid_term",
            "entry_zone": "現價附近分批買進",
            "stop_loss": "近20日低點 - 3% 或跌破 MA60",
            "holding_period": "1-3 個月",
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    for field in ["confidence_score", "cross_validation_note", "strategy_type",
                  "entry_zone", "stop_loss", "holding_period"]:
        assert field in body, f"Missing field '{field}' in response"
    assert body["confidence_score"] == 65
    assert body["strategy_type"] == "mid_term"


# ---------------------------------------------------------------------------
# NQ-5: cleaned_news_quality in API response
# ---------------------------------------------------------------------------


def test_analyze_response_has_cleaned_news_quality_field() -> None:
    """AnalyzeResponse 必須包含 cleaned_news_quality 欄位（值可為 None）。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "cleaned_news_quality" in body, "Missing 'cleaned_news_quality' in response"


def test_analyze_response_cleaned_news_quality_contains_score_and_flags() -> None:
    """當 cleaned_news_quality 非 None 時，必須包含 quality_score 與 quality_flags。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "cleaned_news_quality": {
                "quality_score": 65,
                "quality_flags": ["DATE_UNKNOWN"],
            },
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    quality = body["cleaned_news_quality"]
    assert quality is not None
    assert quality["quality_score"] == 65
    assert quality["quality_flags"] == ["DATE_UNKNOWN"]


def test_analyze_response_cleaned_news_quality_none_when_absent() -> None:
    """graph 未回傳 cleaned_news_quality 時，欄位應為 None。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["cleaned_news_quality"] is None


def test_analyze_response_has_news_display_field() -> None:
    """AnalyzeResponse 必須包含 news_display 欄位（值可為 None）。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "news_display" in response.json()


def test_analyze_response_news_display_contains_expected_fields() -> None:
    """news_display 非 None 時，應包含 title、date、source_url。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "news_display": {
            "title": "台積電 Q1 法說會",
            "date": "2026-03-05",
            "source_url": "https://example.com/news/1",
        },
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    display = body["news_display"]
    assert display["title"] == "台積電 Q1 法說會"
    assert display["date"] == "2026-03-05"
    assert display["source_url"] == "https://example.com/news/1"
