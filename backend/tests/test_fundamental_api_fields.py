import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from ai_stock_sentinel import api
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.session import get_db


def _make_graph(final_state):
    graph = MagicMock()
    graph.invoke.return_value = final_state
    return graph


def _fake_user():
    user = MagicMock()
    user.id = 1
    user.email = "test@example.com"
    user.name = "Test User"
    user.avatar_url = None
    user.is_active = True
    user.deleted_at = None
    return user


def _client_with_graph(graph):
    api.app.dependency_overrides[api.get_graph] = lambda: graph
    api.app.dependency_overrides[get_current_user] = _fake_user
    api.app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(api.app)


def _mock_graph_result():
    return {
        "snapshot": {"symbol": "2330.TW", "current_price": 1785.0},
        "analysis": "test",
        "analysis_detail": None,
        "cleaned_news": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "confidence_score": 50,
        "signal_confidence": 50,
        "data_confidence": 100,
        "cross_validation_note": None,
        "strategy_type": "defensive_wait",
        "entry_zone": "1747-1865",
        "stop_loss": "1712",
        "holding_period": "觀望",
        "action_plan_tag": "neutral",
        "action_plan": None,
        "institutional_flow": None,
        "raw_news_items": [],
        "errors": [],
        "rsi14": None,
        "high_20d": None,
        "low_20d": None,
        "support_20d": None,
        "resistance_20d": None,
        "fundamental_data": {
            "symbol": "2330.TW",
            "ttm_eps": 39.1,
            "pe_current": 25.6,
            "pe_band": "fair",
            "pe_percentile": 60.0,
            "dividend_yield": 1.8,
            "yield_signal": "low_yield",
        },
        "fundamental_context": "【基本面估值】當前 PE 25.6 倍，估值合理。",
    }


def test_analyze_response_includes_fundamental_data(monkeypatch):
    import ai_stock_sentinel.api as api_module
    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", lambda *a, **kw: None)
    client = _client_with_graph(_make_graph(_mock_graph_result()))
    resp = client.post("/analyze", json={"symbol": "2330.TW"})

    assert resp.status_code == 200
    body = resp.json()
    assert "fundamental_data" in body
    fd = body["fundamental_data"]
    assert fd["pe_band"] == "fair"
    assert fd["ttm_eps"] == pytest.approx(39.1)
