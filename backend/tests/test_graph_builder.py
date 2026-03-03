from __future__ import annotations

from unittest.mock import MagicMock

from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.models import StockSnapshot


def _make_stock_snapshot() -> StockSnapshot:
    return StockSnapshot(
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


def _initial_state(symbol: str = "2330.TW") -> dict:
    return {
        "symbol": symbol,
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
    }


def test_graph_runs_and_returns_analysis() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = _make_stock_snapshot()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = "分析結果"

    graph = build_graph(crawler=mock_crawler, analyzer=mock_analyzer)
    result = graph.invoke(_initial_state())

    assert result["analysis"] == "分析結果"
    assert result["errors"] == []


def test_graph_loop_guard_stops_after_max_retries() -> None:
    """Judge 永遠說 insufficient 時，graph 應在 max_retries 後停止並繼續執行 analyze。"""
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = _make_stock_snapshot()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = "分析"

    graph = build_graph(
        crawler=mock_crawler,
        analyzer=mock_analyzer,
        max_retries=2,
        _force_insufficient=True,
    )
    result = graph.invoke(_initial_state())

    assert result["retry_count"] >= 2
