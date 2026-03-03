from __future__ import annotations

from ai_stock_sentinel.graph.state import GraphState


def test_graph_state_fields_exist() -> None:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
    }
    assert state["symbol"] == "2330.TW"
    assert state["retry_count"] == 0
    assert state["data_sufficient"] is False


def test_graph_state_includes_reason_flags() -> None:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
    }
    assert state["requires_news_refresh"] is False
    assert state["requires_fundamental_update"] is False


def test_graph_state_includes_raw_news_items() -> None:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "raw_news_items": [{"source": "google-news-rss", "title": "台積電新聞"}],
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
    }
    assert state["raw_news_items"] is not None
    assert len(state["raw_news_items"]) == 1
