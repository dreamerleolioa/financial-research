from __future__ import annotations

from dataclasses import asdict
from datetime import date
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


NEWS_STALENESS_DAYS = 7


def _check_sufficiency(state: GraphState) -> tuple[bool, bool, bool]:
    """
    回傳 (data_sufficient, requires_news_refresh, requires_fundamental_update)。
    """
    requires_news_refresh = False
    requires_fundamental_update = False

    # 規則 1：snapshot 缺失
    if state["snapshot"] is None:
        requires_fundamental_update = True

    # 規則 2 & 3：新聞相關（只在有提供新聞時才判斷）
    cleaned_news = state["cleaned_news"]
    if cleaned_news is not None:
        # 規則 2：新聞過舊
        news_date_str = cleaned_news.get("date")
        if news_date_str:
            try:
                news_date = date.fromisoformat(news_date_str)
                if (date.today() - news_date).days > NEWS_STALENESS_DAYS:
                    requires_news_refresh = True
            except ValueError:
                requires_news_refresh = True

        # 規則 3：數字不足
        mentioned_numbers = cleaned_news.get("mentioned_numbers", [])
        if not mentioned_numbers:
            requires_news_refresh = True

    data_sufficient = not requires_news_refresh and not requires_fundamental_update
    return data_sufficient, requires_news_refresh, requires_fundamental_update


def judge_node(state: GraphState) -> dict[str, Any]:
    """判斷資料是否充分：snapshot 完整、新聞新鮮（≤7天）且含數字。"""
    data_sufficient, requires_news_refresh, requires_fundamental_update = _check_sufficiency(state)
    return {
        "data_sufficient": data_sufficient,
        "requires_news_refresh": requires_news_refresh,
        "requires_fundamental_update": requires_fundamental_update,
    }


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
