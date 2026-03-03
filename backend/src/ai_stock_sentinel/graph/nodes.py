from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import StockSnapshot


def crawl_node(state: GraphState, *, crawler: YFinanceCrawler) -> dict[str, Any]:
    """抓取股票快照，回傳更新的 state keys。"""
    try:
        snapshot = crawler.fetch_basic_snapshot(symbol=state["symbol"])
        return {"snapshot": asdict(snapshot), "errors": []}
    except Exception as exc:
        return {
            "snapshot": None,
            "errors": state["errors"] + [{"code": "CRAWL_ERROR", "message": str(exc)}],
        }


def judge_node(state: GraphState) -> dict[str, Any]:
    """判斷資料是否充分。此為 stub：永遠回傳 sufficient=True。"""
    return {"data_sufficient": True}


def analyze_node(state: GraphState, *, analyzer: StockAnalyzer) -> dict[str, Any]:
    """執行分析，回傳 analysis 字串。"""
    snapshot_dict = state["snapshot"]
    if not snapshot_dict:
        return {
            "analysis": None,
            "errors": state["errors"] + [{"code": "MISSING_SNAPSHOT", "message": "No snapshot available for analysis."}],
        }
    snapshot = StockSnapshot(**snapshot_dict)
    analysis = analyzer.analyze(snapshot)
    return {"analysis": analysis}
