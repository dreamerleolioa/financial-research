from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock

from ai_stock_sentinel.graph.nodes import crawl_node, judge_node, analyze_node
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import StockSnapshot


def _make_snapshot() -> dict:
    return asdict(StockSnapshot(
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
    ))


def _base_state(**overrides) -> GraphState:
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
    state.update(overrides)
    return state


def test_crawl_node_returns_snapshot() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = StockSnapshot(
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

    result = crawl_node(_base_state(), crawler=mock_crawler)

    assert result["snapshot"]["symbol"] == "2330.TW"
    assert result["errors"] == []


def test_judge_node_stub_always_sufficient() -> None:
    state = _base_state(snapshot=_make_snapshot())

    result = judge_node(state)

    assert result["data_sufficient"] is True


def test_analyze_node_returns_analysis_string() -> None:
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = "分析結果"

    state = _base_state(snapshot=_make_snapshot())
    result = analyze_node(state, analyzer=mock_analyzer)

    assert result["analysis"] == "分析結果"


def test_crawl_node_accumulates_errors_on_failure() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.side_effect = RuntimeError("network timeout")

    prior_errors = [{"code": "PRIOR_ERROR", "message": "earlier error"}]
    state = _base_state(errors=prior_errors)
    result = crawl_node(state, crawler=mock_crawler)

    assert result["snapshot"] is None
    assert len(result["errors"]) == 2
    assert result["errors"][0]["code"] == "PRIOR_ERROR"
    assert result["errors"][1]["code"] == "CRAWL_ERROR"
    assert "network timeout" in result["errors"][1]["message"]
