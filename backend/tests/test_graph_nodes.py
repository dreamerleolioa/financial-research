from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
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
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
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


def _make_cleaned_news(
    *,
    date_str: str | None = None,
    mentioned_numbers: list[str] | None = None,
) -> dict:
    today = date.today().isoformat()
    return {
        "date": date_str if date_str is not None else today,
        "title": "台積電 2 月營收年增",
        "mentioned_numbers": mentioned_numbers if mentioned_numbers is not None else ["2,600", "18.2%"],
        "sentiment_label": "positive",
    }


def test_judge_node_sufficient_when_snapshot_and_fresh_news() -> None:
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is True
    assert result["requires_news_refresh"] is False
    assert result["requires_fundamental_update"] is False


def test_judge_node_insufficient_when_snapshot_missing() -> None:
    state = _base_state(snapshot=None)
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_fundamental_update"] is True


def test_judge_node_insufficient_when_news_stale() -> None:
    stale_date = (date.today() - timedelta(days=8)).isoformat()
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(date_str=stale_date),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_insufficient_when_no_mentioned_numbers() -> None:
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(mentioned_numbers=[]),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_sufficient_when_no_news_provided() -> None:
    """cleaned_news 為 None 時（未提供新聞），不因此判定為 insufficient。"""
    state = _base_state(snapshot=_make_snapshot(), cleaned_news=None)
    result = judge_node(state)
    assert result["data_sufficient"] is True
