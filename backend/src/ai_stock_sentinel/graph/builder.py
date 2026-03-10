from __future__ import annotations

from functools import partial
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.data_sources.institutional_flow.tools import fetch_institutional_flow
from ai_stock_sentinel.data_sources.fundamental.tools import fetch_fundamental_data
from ai_stock_sentinel.data_sources.rss_news_client import RssNewsClient
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.nodes import analyze_node, clean_node, crawl_node, fetch_fundamental_node, fetch_institutional_node, fetch_news_node, judge_node, preprocess_node, quality_gate_node, score_node, strategy_node
from ai_stock_sentinel.graph.state import GraphState

MAX_RETRIES = 3


def build_graph(
    *,
    crawler: YFinanceCrawler,
    analyzer: StockAnalyzer,
    rss_client: RssNewsClient | None = None,
    news_cleaner: FinancialNewsCleaner | None = None,
    institutional_fetcher: Callable[[str], dict[str, Any]] | None = None,
    fundamental_fetcher: Callable[[str, float], dict[str, Any]] | None = None,
    max_retries: int = MAX_RETRIES,
    _force_insufficient: bool = False,
):
    """組裝並編譯 LangGraph 狀態機。

    rss_client: 提供 RSS 新聞抓取；若為 None 則自動建立預設實例。
    news_cleaner: 提供新聞清潔；若為 None 則自動建立預設實例。
    _force_insufficient: 測試用，強制讓 judge 永遠回傳 insufficient。
    """
    _rss_client = rss_client or RssNewsClient()
    _news_cleaner = news_cleaner or FinancialNewsCleaner()
    _institutional_fetcher = institutional_fetcher or (lambda symbol: fetch_institutional_flow(symbol, days=10))
    _fundamental_fetcher = fundamental_fetcher or fetch_fundamental_data

    graph = StateGraph(GraphState)

    # 節點
    graph.add_node("crawl", partial(crawl_node, crawler=crawler))
    graph.add_node("fetch_institutional", partial(fetch_institutional_node, fetcher=_institutional_fetcher))
    graph.add_node("fetch_fundamental", partial(fetch_fundamental_node, fetcher=_fundamental_fetcher))
    graph.add_node("clean", partial(clean_node, news_cleaner=_news_cleaner))
    graph.add_node("quality_gate", quality_gate_node)
    graph.add_node("preprocess", preprocess_node)
    graph.add_node("score", score_node)
    graph.add_node("analyze", partial(analyze_node, analyzer=analyzer))
    graph.add_node("fetch_news", partial(fetch_news_node, rss_client=_rss_client))
    graph.add_node("strategy", strategy_node)

    def _judge(state: GraphState) -> dict[str, Any]:
        """呼叫 judge_node；若 _force_insufficient=True 則永遠回傳 insufficient（測試用）。"""
        if _force_insufficient:
            return {"data_sufficient": False}
        return judge_node(state)

    graph.add_node("judge", _judge)

    def _increment_retry(state: GraphState) -> dict[str, Any]:
        """在回到 crawl 前先增加 retry_count。"""
        return {"retry_count": state["retry_count"] + 1}

    graph.add_node("increment_retry", _increment_retry)

    # 邊
    graph.set_entry_point("crawl")
    graph.add_edge("crawl", "fetch_institutional")
    graph.add_edge("fetch_institutional", "fetch_fundamental")
    graph.add_edge("fetch_fundamental", "judge")

    def _route(state: GraphState) -> str:
        if state["data_sufficient"]:
            return "clean"
        if state["retry_count"] >= max_retries:
            return "clean"  # 超過上限，強制往下走
        if state["requires_news_refresh"]:
            return "fetch_news"
        return "crawl"

    graph.add_conditional_edges(
        "judge",
        _route,
        {
            "clean": "clean",
            "fetch_news": "fetch_news",
            "crawl": "increment_retry",
        },
    )
    graph.add_edge("fetch_news", "increment_retry")
    graph.add_edge("increment_retry", "crawl")
    graph.add_edge("clean", "quality_gate")
    graph.add_edge("quality_gate", "preprocess")
    graph.add_edge("preprocess", "score")
    graph.add_edge("score", "analyze")
    graph.add_edge("analyze", "strategy")
    graph.add_edge("strategy", END)

    return graph.compile()
