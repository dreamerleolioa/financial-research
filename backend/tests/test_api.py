from __future__ import annotations

from dataclasses import asdict

from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.models import StockSnapshot


class DummyAgent:
    def run(self, symbol: str = "2330.TW", news_content: str | None = None) -> dict:
        snapshot = StockSnapshot(
            symbol=symbol,
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
        result = {
            "snapshot": asdict(snapshot),
            "analysis": "LLM 尚未設定（缺少 API Key 或模型），已保留 LangChain 分析介面。",
        }
        if news_content:
            result["cleaned_news"] = {
                "date": "2026-03-03",
                "title": "台積電 2 月營收年增",
                "mentioned_numbers": ["2,600", "18.2%"],
                "sentiment_label": "positive",
            }
        return result


class DummyFailingAgent:
    def run(self, symbol: str = "2330.TW", news_content: str | None = None) -> dict:
        raise RuntimeError("yfinance data source temporarily unavailable")


class DummyPartialAgent:
    def run(self, symbol: str = "2330.TW", news_content: str | None = None) -> dict:
        return {"cleaned_news": {"title": "partial"}}


def create_client() -> TestClient:
    api.app.dependency_overrides[api.get_agent] = lambda: DummyAgent()
    return TestClient(api.app)


def test_health_endpoint() -> None:
    client = create_client()
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_analyze_success_with_news_text() -> None:
    client = create_client()
    response = client.post(
        "/analyze",
        json={
            "symbol": "2330.TW",
            "news_text": "2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"]["symbol"] == "2330.TW"
    assert body["analysis"]
    assert body["cleaned_news"]["sentiment_label"] == "positive"
    assert body["errors"] == []


def test_analyze_validation_error_when_symbol_empty() -> None:
    client = create_client()
    response = client.post("/analyze", json={"symbol": ""})

    assert response.status_code == 422


def test_analyze_runtime_error_returns_error_code() -> None:
    api.app.dependency_overrides[api.get_agent] = lambda: DummyFailingAgent()
    client = TestClient(api.app)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] == {}
    assert body["analysis"] == ""
    assert body["errors"][0]["code"] == "ANALYZE_RUNTIME_ERROR"


def test_analyze_missing_payload_returns_traceable_error_codes() -> None:
    api.app.dependency_overrides[api.get_agent] = lambda: DummyPartialAgent()
    client = TestClient(api.app)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    error_codes = {item["code"] for item in body["errors"]}
    assert "MISSING_SNAPSHOT" in error_codes
    assert "MISSING_ANALYSIS" in error_codes
