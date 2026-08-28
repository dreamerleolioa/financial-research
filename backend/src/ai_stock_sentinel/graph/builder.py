from __future__ import annotations

from functools import partial
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from ai_stock_sentinel.data_sources.institutional_flow.tools import fetch_institutional_flow
from ai_stock_sentinel.data_sources.fundamental.tools import fetch_fundamental_data
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.nodes import (
    crawl_node,
    fetch_external_data_node,
    judge_node,
    preprocess_node,
    score_node,
    strategy_node,
)
from ai_stock_sentinel.graph.state import GraphState

MAX_RETRIES = 3


def build_graph(
    *,
    crawler: YFinanceCrawler,
    institutional_fetcher: Callable[[str], dict[str, Any]] | None = None,
    fundamental_fetcher: Callable[[str, float], dict[str, Any]] | None = None,
    max_retries: int = MAX_RETRIES,
    _force_insufficient: bool = False,
):
    """組裝 deterministic 市場資料、評分與策略狀態機。"""
    _institutional_fetcher = institutional_fetcher or (lambda symbol: fetch_institutional_flow(symbol, days=10))
    _fundamental_fetcher = fundamental_fetcher or fetch_fundamental_data

    graph = StateGraph(GraphState)

    # 節點
    graph.add_node("crawl", partial(crawl_node, crawler=crawler))
    graph.add_node("fetch_external_data", partial(
        fetch_external_data_node,
        institutional_fetcher=_institutional_fetcher,
        fundamental_fetcher=_fundamental_fetcher,
    ))
    graph.add_node("preprocess", preprocess_node)
    graph.add_node("score", score_node)
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
    graph.add_edge("crawl", "fetch_external_data")
    graph.add_edge("fetch_external_data", "judge")

    def _route(state: GraphState) -> str:
        if state["data_sufficient"]:
            return "preprocess"
        if state["retry_count"] >= max_retries:
            return "preprocess"  # 超過上限，帶著錯誤與缺口往下走
        return "increment_retry"

    graph.add_conditional_edges(
        "judge",
        _route,
        {
            "preprocess":      "preprocess",
            "increment_retry": "increment_retry",
        },
    )
    graph.add_edge("increment_retry", "crawl")
    graph.add_edge("preprocess", "score")
    graph.add_edge("score", "strategy")
    graph.add_edge("strategy", END)

    return graph.compile()
