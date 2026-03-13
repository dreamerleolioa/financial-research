from __future__ import annotations

from dataclasses import asdict
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.session import get_db
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


def _fake_user():
    user = MagicMock()
    user.id = 1
    user.email = "test@example.com"
    user.name = "Test User"
    user.avatar_url = None
    user.is_active = True
    user.deleted_at = None
    return user


def _fake_db():
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.execute.return_value.scalar.return_value = 0
    return db


def _client_with_graph(graph) -> TestClient:
    api.app.dependency_overrides[api.get_graph] = lambda: graph
    api.app.dependency_overrides[get_current_user] = _fake_user
    api.app.dependency_overrides[get_db] = _fake_db
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


# ---------------------------------------------------------------------------
# Session 2: action_plan_tag & institutional_flow_label
# ---------------------------------------------------------------------------


def test_api_response_includes_action_plan_tag_field() -> None:
    """AnalyzeResponse 必須包含 action_plan_tag 欄位。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "action_plan_tag": "opportunity",
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "action_plan_tag" in body
    assert body["action_plan_tag"] == "opportunity"


def test_api_response_action_plan_tag_none_when_absent() -> None:
    """graph 未回傳 action_plan_tag 時，欄位應為 None。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["action_plan_tag"] is None


def test_api_response_includes_institutional_flow_label_field() -> None:
    """institutional_flow 有 flow_label 且無 error 時，institutional_flow_label 應浮出。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": {
            "flow_label": "institutional_accumulation",
            "foreign_buy": 1000.0,
        },
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "institutional_flow_label" in body
    assert body["institutional_flow_label"] == "institutional_accumulation"


def test_api_response_institutional_flow_label_none_when_error() -> None:
    """institutional_flow 含 error 欄位時，institutional_flow_label 應為 None。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": {
            "error": "INSTITUTIONAL_FETCH_ERROR",
        },
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["institutional_flow_label"] is None


# ---------------------------------------------------------------------------
# Session 3: sentiment_label, action_plan, data_sources
# ---------------------------------------------------------------------------


def test_api_response_includes_sentiment_label_from_cleaned_news() -> None:
    """cleaned_news 有 sentiment_label 時，AnalyzeResponse.sentiment_label 應浮出。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": _CLEANED_NEWS,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["sentiment_label"] == "positive"


def test_api_response_sentiment_label_is_none_when_cleaned_news_absent() -> None:
    """cleaned_news 為 None 時，sentiment_label 應為 None。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["sentiment_label"] is None


def test_api_response_includes_action_plan_dict() -> None:
    """action_plan 在 state 有值時，AnalyzeResponse.action_plan 應回傳 dict。"""
    _action_plan = {
        "action": "觀望",
        "target_zone": "現價附近",
        "defense_line": "890",
        "momentum_expectation": "中性",
    }
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
        "action_plan": _action_plan,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["action_plan"] == _action_plan


def test_api_response_data_sources_includes_yfinance_when_snapshot_present() -> None:
    """snapshot 有值時，data_sources 應包含 yfinance。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "yfinance" in response.json()["data_sources"]


def test_api_response_data_sources_includes_google_news_rss_when_raw_news_present() -> None:
    """raw_news_items 有值時，data_sources 應包含 google-news-rss。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": [{"title": "新聞一", "url": None}],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "google-news-rss" in response.json()["data_sources"]


# ---------------------------------------------------------------------------
# Task 5: POST /analyze/position
# ---------------------------------------------------------------------------

_POSITION_SNAPSHOT = StockSnapshot(
    symbol="2330.TW",
    currency="TWD",
    current_price=1050.0,
    previous_close=1040.0,
    day_open=1040.0,
    day_high=1060.0,
    day_low=1035.0,
    volume=5000000,
    recent_closes=[1000.0, 1010.0, 1020.0, 1030.0, 1040.0, 1050.0],
    fetched_at="2026-03-10T00:00:00+00:00",
)

_POSITION_FINAL_STATE = {
    "snapshot": asdict(_POSITION_SNAPSHOT),
    "analysis": "持倉診斷結果",
    "analysis_detail": {
        "summary": "持倉安全",
        "risks": [],
        "technical_signal": "bullish",
        "institutional_flow": "accumulation",
        "sentiment_label": "positive",
        "tech_insight": "多頭排列",
        "inst_insight": "法人買超",
        "news_insight": "無重大利空",
        "final_verdict": "建議繼續持有",
        "fundamental_insight": None,
    },
    "entry_price": 980.0,
    "profit_loss_pct": 7.14,
    "position_status": "profitable_safe",
    "position_narrative": "目前獲利已脫離成本區，持股安全緩衝充足。",
    "trailing_stop": 980.0,
    "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價保本",
    "recommended_action": "Hold",
    "exit_reason": None,
    "cleaned_news": None,
    "errors": [],
    "confidence_score": 70,
    "institutional_flow": {"flow_label": "accumulation"},
}

_DISTRIBUTION_POSITION_FINAL_STATE = {
    **_POSITION_FINAL_STATE,
    "entry_price": 800.0,
    "profit_loss_pct": 31.25,
    "position_status": "profitable_safe",
    "recommended_action": "Trim",
    "exit_reason": "法人持續出貨，建議逢高分批減碼保護獲利",
    "institutional_flow": {"flow_label": "distribution"},
}


def test_analyze_position_returns_position_analysis_block() -> None:
    """The /analyze/position endpoint must return a position_analysis object."""
    graph = _make_graph(_POSITION_FINAL_STATE)
    client = _client_with_graph(graph)

    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
    })
    assert response.status_code == 200
    body = response.json()
    assert "position_analysis" in body
    pa = body["position_analysis"]
    assert "entry_price" in pa
    assert "profit_loss_pct" in pa
    assert "position_status" in pa
    assert "trailing_stop" in pa
    assert "recommended_action" in pa
    assert pa["recommended_action"] in ("Hold", "Trim", "Exit")


def test_analyze_position_entry_price_required() -> None:
    """entry_price is required for /analyze/position."""
    client = TestClient(api.app)
    response = client.post("/analyze/position", json={"symbol": "2330.TW"})
    assert response.status_code == 422


def test_analyze_position_optional_fields_accepted() -> None:
    """entry_date and quantity are accepted but optional."""
    graph = _make_graph(_POSITION_FINAL_STATE)
    client = _client_with_graph(graph)

    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
        "entry_date": "2026-01-15",
        "quantity": 1000,
    })
    assert response.status_code == 200


def test_analyze_position_exit_reason_not_null_when_distribution_profit() -> None:
    """Spec §7: exit_reason must not be null when flow=distribution and profit>0."""
    graph = _make_graph(_DISTRIBUTION_POSITION_FINAL_STATE)
    client = _client_with_graph(graph)

    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 800.0,
    })
    assert response.status_code == 200
    body = response.json()
    pa = body["position_analysis"]
    if pa["recommended_action"] in ("Trim", "Exit"):
        assert pa["exit_reason"] is not None


# ---------------------------------------------------------------------------
# Full result cache persistence
# ---------------------------------------------------------------------------

def test_upsert_analysis_cache_stores_full_result() -> None:
    """upsert_analysis_cache should persist full_result when provided."""
    from unittest.mock import MagicMock
    from ai_stock_sentinel.api import upsert_analysis_cache

    db = MagicMock()
    data = {
        "symbol": "2330.TW",
        "signal_confidence": 55,
        "action_tag": "neutral",
        "recommended_action": "觀望",
        "indicators": {},
        "final_verdict": "分析結果",
        "is_final": False,
        "full_result": {"snapshot": {"symbol": "2330.TW"}, "analysis": "分析結果"},
    }

    upsert_analysis_cache(db, data)

    db.execute.assert_called_once()
    call_kwargs = db.execute.call_args
    params = call_kwargs[0][1]  # second positional arg is the params dict
    assert "full_result" in params
    assert params["full_result"] is not None


def test_cache_hit_returns_full_result_fields(monkeypatch) -> None:
    """When cache has full_result, /analyze should return all fields."""
    from unittest.mock import MagicMock
    import ai_stock_sentinel.api as api_module
    from ai_stock_sentinel.db.session import get_db

    full = {
        "snapshot": {"symbol": "2330.TW", "current_price": 1865.0},
        "analysis": "完整分析內容",
        "signal_confidence": 37,
        "action_plan_tag": "neutral",
        "news_display_items": [{"title": "新聞標題"}],
        "fundamental_data": {"pe_ratio": 28.1},
        "is_final": False,
        "intraday_disclaimer": None,
        "errors": [],
    }

    cache = MagicMock()
    cache.symbol = "2330.TW"
    cache.is_final = False
    cache.full_result = full
    cache.signal_confidence = 37
    cache.action_tag = "neutral"
    cache.recommended_action = None
    cache.final_verdict = "完整分析內容"
    cache.indicators = {}

    monkeypatch.setattr(api_module, "get_analysis_cache", lambda db, symbol: cache)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)

    fake_db = MagicMock()
    api.app.dependency_overrides[get_db] = lambda: fake_db

    graph = _make_graph({})  # should not be invoked
    client = _client_with_graph(graph)
    response = client.post("/analyze", json={"symbol": "2330.TW"})

    api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] == "完整分析內容"
    assert body["fundamental_data"] == {"pe_ratio": 28.1}
    assert body["news_display_items"] == [{"title": "新聞標題"}]
    assert body["snapshot"]["symbol"] == "2330.TW"


def test_analyze_cache_is_called_with_full_result(monkeypatch) -> None:
    """POST /analyze should pass full_result to upsert_analysis_cache."""
    import ai_stock_sentinel.api as api_module
    from ai_stock_sentinel.db.session import get_db

    graph = _make_graph(
        {
            "snapshot": {"symbol": "2330.TW", "current_price": 100.0},
            "analysis": "分析結果",
            "signal_confidence": 55,
            "action_plan_tag": "neutral",
            "errors": [],
        }
    )

    captured = {}

    def fake_upsert(db, data):
        captured["data"] = data

    monkeypatch.setattr(api_module, "upsert_analysis_cache", fake_upsert)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: None)

    fake_db = MagicMock()
    api.app.dependency_overrides[get_db] = lambda: fake_db
    client = _client_with_graph(graph)
    client.post("/analyze", json={"symbol": "2330.TW"})
    api.app.dependency_overrides.pop(get_db, None)

    assert "full_result" in captured.get("data", {})
    full = captured["data"]["full_result"]
    assert full.get("analysis") == "分析結果"
    assert full.get("snapshot", {}).get("symbol") == "2330.TW"
