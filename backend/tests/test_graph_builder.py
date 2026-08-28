from __future__ import annotations

from unittest.mock import MagicMock

from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.models import StockSnapshot


def _snapshot() -> StockSnapshot:
    closes = [100.0 + index for index in range(80)]
    return StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=closes[-1],
        previous_close=closes[-2],
        day_open=closes[-2],
        day_high=closes[-1] + 1,
        day_low=closes[-1] - 1,
        volume=1_000_000,
        recent_closes=closes,
        recent_highs=[value + 1 for value in closes],
        recent_lows=[value - 1 for value in closes],
        recent_volumes=[1_000_000 + index * 1_000 for index in range(80)],
        fetched_at="2026-08-28T00:00:00+00:00",
    )


def _initial_state() -> dict:
    return {
        "symbol": "2330.TW",
        "snapshot": None,
        "institutional_flow": None,
        "fundamental_data": None,
        "technical_profile": None,
        "analysis": None,
        "analysis_detail": None,
        "cleaned_news": None,
        "cleaned_news_items": [],
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "is_final": True,
    }


def _graph(crawler: MagicMock, *, max_retries: int = 3, force_insufficient: bool = False):
    return build_graph(
        crawler=crawler,
        institutional_fetcher=lambda _symbol: {"flow_label": "neutral"},
        fundamental_fetcher=lambda _symbol, _price: {},
        max_retries=max_retries,
        _force_insufficient=force_insufficient,
    )


def test_graph_runs_deterministic_analysis_without_llm_nodes() -> None:
    crawler = MagicMock()
    crawler.fetch_basic_snapshot.return_value = _snapshot()

    result = _graph(crawler).invoke(_initial_state())

    assert result["snapshot"]["symbol"] == "2330.TW"
    assert result["technical_profile"]["version"] == "technical-layer-v1"
    assert result["signal_confidence"] is not None
    assert result["strategy_type"] is not None
    assert result.get("analysis_detail") is None
    assert result["errors"] == []


def test_graph_retries_crawler_then_recovers() -> None:
    crawler = MagicMock()
    crawler.fetch_basic_snapshot.side_effect = [RuntimeError("temporary"), _snapshot()]

    result = _graph(crawler).invoke(_initial_state())

    assert crawler.fetch_basic_snapshot.call_count == 2
    assert result["snapshot"]["symbol"] == "2330.TW"
    assert result["retry_count"] == 1


def test_graph_stops_retrying_at_configured_limit() -> None:
    crawler = MagicMock()
    crawler.fetch_basic_snapshot.side_effect = RuntimeError("unavailable")

    result = _graph(crawler, max_retries=1).invoke(_initial_state())

    assert crawler.fetch_basic_snapshot.call_count == 2
    assert result["retry_count"] == 1
    assert any(error["code"] == "CRAWL_ERROR" for error in result["errors"])


def test_force_insufficient_test_hook_does_not_invoke_removed_news_path() -> None:
    crawler = MagicMock()
    crawler.fetch_basic_snapshot.return_value = _snapshot()

    result = _graph(crawler, max_retries=1, force_insufficient=True).invoke(_initial_state())

    assert result["retry_count"] == 1
    assert result["strategy_type"] is not None
