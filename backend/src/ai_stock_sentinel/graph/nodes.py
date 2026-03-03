from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.data_sources.rss_news_client import RssNewsClient
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


def clean_node(state: GraphState, *, news_cleaner: FinancialNewsCleaner) -> dict[str, Any]:
    """將 news_content 清潔成結構化 cleaned_news；若無 news_content 則跳過。"""
    news_content = state["news_content"]
    if not news_content:
        return {"cleaned_news": None}
    try:
        cleaned = news_cleaner.clean(news_content)
        return {"cleaned_news": cleaned.model_dump()}
    except Exception as exc:
        return {
            "cleaned_news": None,
            "errors": state["errors"] + [{"code": "CLEAN_ERROR", "message": str(exc)}],
        }


def fetch_news_node(state: GraphState, *, rss_client: RssNewsClient) -> dict[str, Any]:
    """透過 RSS 抓取新聞，回傳 raw_news_items 與 news_content（取第一篇標題+摘要）。"""
    symbol = state["symbol"]
    # 用股票代碼（去掉 .TW 後綴）作查詢詞，例如 "2330 台積電"
    query = symbol.split(".")[0]
    try:
        items = rss_client.fetch_news(query=query)
    except Exception as exc:
        return {
            "raw_news_items": [],
            "errors": state["errors"] + [{"code": "RSS_FETCH_ERROR", "message": str(exc)}],
        }

    raw_dicts = [asdict(item) for item in items]

    # 將最新一篇的 title + summary 合併成 news_content 供後續清潔
    news_content: str | None = None
    if items:
        first = items[0]
        parts = [p for p in [first.published_at, first.title, first.summary] if p]
        news_content = "\n".join(parts)

    return {
        "raw_news_items": raw_dicts,
        "news_content": news_content,
    }
