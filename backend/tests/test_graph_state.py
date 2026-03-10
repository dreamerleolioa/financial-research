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


def test_graph_state_has_news_display_field() -> None:
    """GraphState 必須包含 news_display 欄位。"""
    import typing
    hints = typing.get_type_hints(GraphState)
    assert "news_display" in hints


def test_graph_state_has_news_display_items_field() -> None:
    """GraphState 應有 news_display_items 欄位（list 型別）。"""
    import typing
    hints = typing.get_type_hints(GraphState)
    assert "news_display_items" in hints


def test_graph_state_has_position_fields():
    """GraphState must include all PositionState optional fields."""
    import typing

    hints = typing.get_type_hints(GraphState)
    for field in [
        "entry_price", "entry_date", "quantity",
        "profit_loss_pct", "cost_buffer_to_support",
        "position_status", "position_narrative",
        "trailing_stop", "trailing_stop_reason",
        "recommended_action", "exit_reason",
    ]:
        assert field in hints, f"GraphState missing field: {field}"
