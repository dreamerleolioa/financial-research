from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from unittest.mock import MagicMock

from ai_stock_sentinel.data_sources.rss_news_client import RawNewsItem
from ai_stock_sentinel.graph.nodes import clean_node, crawl_node, fetch_institutional_node, fetch_news_node, judge_node, analyze_node
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
        "raw_news_items": None,
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


def test_judge_node_requires_news_when_none_available() -> None:
    state = _base_state(snapshot=_make_snapshot())

    result = judge_node(state)

    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_analyze_node_returns_analysis_string() -> None:
    from ai_stock_sentinel.models import AnalysisDetail

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = AnalysisDetail(summary="分析結果")

    state = _base_state(snapshot=_make_snapshot())
    result = analyze_node(state, analyzer=mock_analyzer)

    assert result["analysis"] == "分析結果"
    assert isinstance(result["analysis_detail"], AnalysisDetail)


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


def test_judge_node_insufficient_when_no_news_provided() -> None:
    """未提供新聞（cleaned_news/news_content 皆無）時，應要求 refresh。"""
    state = _base_state(snapshot=_make_snapshot(), cleaned_news=None)
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_sufficient_when_news_content_provided_without_cleaned_news() -> None:
    """有 news_content 但尚未 cleaned 時，允許進到 clean_node。"""
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=None,
        news_content="2026-03-04 台股焦點新聞",
    )
    result = judge_node(state)
    assert result["data_sufficient"] is True
    assert result["requires_news_refresh"] is False


# ── fetch_news_node ──────────────────────────────────────────────────────────

def _make_raw_news_item(**overrides) -> RawNewsItem:
    defaults = dict(
        source="google-news-rss",
        url="https://example.com/news/1",
        title="台積電 2 月營收年增 20%",
        published_at="Mon, 03 Mar 2026 08:00:00 GMT",
        summary="台積電公佈 2 月營收，年增 20%，優於市場預期。",
    )
    defaults.update(overrides)
    return RawNewsItem(**defaults)


def test_fetch_news_node_returns_raw_news_items() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = [_make_raw_news_item()]

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["raw_news_items"] is not None
    assert len(result["raw_news_items"]) == 1
    assert result["raw_news_items"][0]["source"] == "google-news-rss"


def test_fetch_news_node_sets_news_content_from_first_item() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = [_make_raw_news_item()]

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["news_content"] is not None
    assert "台積電" in result["news_content"]


def test_fetch_news_node_returns_empty_list_on_no_results() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = []

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["raw_news_items"] == []
    assert result.get("news_content") is None


def test_fetch_news_node_accumulates_errors_on_exception() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.side_effect = RuntimeError("connection refused")

    prior_errors = [{"code": "PRIOR", "message": "earlier error"}]
    state = _base_state(errors=prior_errors)
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["raw_news_items"] == []
    assert len(result["errors"]) == 2
    assert result["errors"][1]["code"] == "RSS_FETCH_ERROR"
    assert "connection refused" in result["errors"][1]["message"]


# ── clean_node ───────────────────────────────────────────────────────────────

def test_clean_node_produces_cleaned_news_from_news_content() -> None:
    mock_cleaner = MagicMock()
    mock_cleaner.clean.return_value = MagicMock(
        model_dump=lambda: {
            "date": "2026-03-03",
            "title": "台積電 2 月營收年增",
            "mentioned_numbers": ["2,600", "18.2%"],
            "sentiment_label": "positive",
        }
    )

    state = _base_state(news_content="2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%")
    result = clean_node(state, news_cleaner=mock_cleaner)

    assert result["cleaned_news"]["sentiment_label"] == "positive"
    assert result["cleaned_news"]["mentioned_numbers"] == ["2,600", "18.2%"]


def test_clean_node_skips_when_no_news_content() -> None:
    mock_cleaner = MagicMock()

    state = _base_state(news_content=None)
    result = clean_node(state, news_cleaner=mock_cleaner)

    mock_cleaner.clean.assert_not_called()
    assert result.get("cleaned_news") is None


def test_clean_node_accumulates_errors_on_exception() -> None:
    mock_cleaner = MagicMock()
    mock_cleaner.clean.side_effect = RuntimeError("LLM timeout")

    state = _base_state(news_content="some news")
    result = clean_node(state, news_cleaner=mock_cleaner)

    assert result.get("cleaned_news") is None
    assert any(e["code"] == "CLEAN_ERROR" for e in result["errors"])


def test_fetch_news_node_uses_symbol_prefix_as_query() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = []

    state = _base_state(symbol="2330.TW")
    fetch_news_node(state, rss_client=mock_rss)

    mock_rss.fetch_news.assert_called_once_with(query="2330")


# ── fetch_institutional_node ─────────────────────────────────────────────────

def test_fetch_institutional_node_writes_flow_to_state() -> None:
    """成功時，institutional_flow 應被寫入 state。"""
    mock_flow_data = {
        "symbol": "2330.TW",
        "foreign_buy": 1000.0,
        "investment_trust_buy": 200.0,
        "dealer_buy": 50.0,
        "margin_delta": None,
        "flow_label": "institutional_accumulation",
        "source_provider": "twse",
    }
    mock_fetcher = MagicMock(return_value=mock_flow_data)

    state = _base_state(snapshot=_make_snapshot())
    result = fetch_institutional_node(state, fetcher=mock_fetcher)

    assert result["institutional_flow"] is not None
    assert result["institutional_flow"]["flow_label"] == "institutional_accumulation"
    mock_fetcher.assert_called_once_with("2330.TW")


def test_fetch_institutional_node_stores_error_dict_on_failure() -> None:
    """fetcher 失敗回傳 error dict 時，仍寫入 institutional_flow，流程不中斷。"""
    mock_flow_data = {
        "symbol": "2330.TW",
        "error": "INSTITUTIONAL_FETCH_ERROR",
        "error_message": "all providers failed",
    }
    mock_fetcher = MagicMock(return_value=mock_flow_data)

    state = _base_state(snapshot=_make_snapshot())
    result = fetch_institutional_node(state, fetcher=mock_fetcher)

    assert result["institutional_flow"]["error"] == "INSTITUTIONAL_FETCH_ERROR"
    assert result.get("errors", []) == []  # 不額外累積 errors，flow 本身帶 error 欄位
