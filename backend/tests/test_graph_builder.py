from __future__ import annotations

from unittest.mock import MagicMock

from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.models import AnalysisDetail, StockSnapshot


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
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "technical_context": None,
        "institutional_context": None,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
    }


def test_graph_runs_and_returns_analysis() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = _make_stock_snapshot()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = AnalysisDetail(summary="分析結果")

    graph = build_graph(crawler=mock_crawler, analyzer=mock_analyzer)
    result = graph.invoke(_initial_state())

    assert result["analysis"] == "分析結果"
    assert result["errors"] == []


def test_graph_cleans_news_when_news_content_provided() -> None:
    """news_content 有值時，graph 應呼叫 news_cleaner 並將結果寫入 cleaned_news。"""
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = _make_stock_snapshot()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = AnalysisDetail(summary="分析結果")
    mock_cleaner = MagicMock()
    mock_cleaner.clean.return_value = MagicMock(
        model_dump=lambda: {
            "date": "2026-03-03",
            "title": "台積電",
            "mentioned_numbers": ["2,600"],
            "sentiment_label": "positive",
        }
    )

    state = _initial_state()
    state["news_content"] = "2026-03-03 台積電 2 月營收 2,600 億元"

    graph = build_graph(crawler=mock_crawler, analyzer=mock_analyzer, news_cleaner=mock_cleaner)
    result = graph.invoke(state)

    assert result["cleaned_news"] is not None
    assert result["cleaned_news"]["sentiment_label"] == "positive"
    mock_cleaner.clean.assert_called_once()


def test_graph_loop_guard_stops_after_max_retries() -> None:
    """Judge 永遠說 insufficient 時，graph 應在 max_retries 後停止並繼續執行 analyze。"""
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = _make_stock_snapshot()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = AnalysisDetail(summary="分析")

    graph = build_graph(
        crawler=mock_crawler,
        analyzer=mock_analyzer,
        max_retries=2,
        _force_insufficient=True,
    )
    result = graph.invoke(_initial_state())

    assert result["retry_count"] >= 2
