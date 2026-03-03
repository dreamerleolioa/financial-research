from __future__ import annotations

from ai_stock_sentinel.graph.state import GraphState


def test_graph_state_fields_exist() -> None:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
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
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
    }
    assert state["requires_news_refresh"] is False
    assert state["requires_fundamental_update"] is False
