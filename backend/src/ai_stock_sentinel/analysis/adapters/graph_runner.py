from __future__ import annotations

from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.main import build_graph_deps


def build_graph_singleton():
    crawler, analyzer, rss_client, news_cleaner = build_graph_deps()
    return build_graph(crawler=crawler, analyzer=analyzer, rss_client=rss_client, news_cleaner=news_cleaner)


def invoke_graph(graph, initial_state: dict) -> dict:
    return graph.invoke(initial_state)
