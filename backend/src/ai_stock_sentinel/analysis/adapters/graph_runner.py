from __future__ import annotations

from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.main import build_graph_deps


def build_graph_singleton():
    return build_graph(crawler=build_graph_deps())


def invoke_graph(graph, initial_state: dict) -> dict:
    return graph.invoke(initial_state)
