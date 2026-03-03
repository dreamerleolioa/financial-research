from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.nodes import analyze_node, crawl_node, judge_node
from ai_stock_sentinel.graph.state import GraphState

MAX_RETRIES = 3


def build_graph(
    *,
    crawler: YFinanceCrawler,
    analyzer: StockAnalyzer,
    max_retries: int = MAX_RETRIES,
    _force_insufficient: bool = False,
):
    """組裝並編譯 LangGraph 狀態機。

    _force_insufficient: 測試用，強制讓 judge 永遠回傳 insufficient。
    """
    graph = StateGraph(GraphState)

    # 節點
    graph.add_node("crawl", partial(crawl_node, crawler=crawler))
    graph.add_node("analyze", partial(analyze_node, analyzer=analyzer))

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
    graph.add_edge("crawl", "judge")

    def _route(state: GraphState) -> str:
        if state["data_sufficient"]:
            return "analyze"
        if state["retry_count"] >= max_retries:
            return "analyze"  # 超過上限，強制往下走
        return "crawl"

    graph.add_conditional_edges(
        "judge",
        _route,
        {
            "analyze": "analyze",
            "crawl": "increment_retry",
        },
    )
    graph.add_edge("increment_retry", "crawl")
    graph.add_edge("analyze", END)

    return graph.compile()
