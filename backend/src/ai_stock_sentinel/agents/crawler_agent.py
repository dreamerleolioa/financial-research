from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler


class StockCrawlerAgent:
    def __init__(
        self,
        crawler: YFinanceCrawler,
        analyzer: StockAnalyzer,
        news_cleaner: FinancialNewsCleaner | None = None,
    ) -> None:
        self.crawler = crawler
        self.analyzer = analyzer
        self.news_cleaner = news_cleaner

    def run(self, symbol: str = "2330.TW", news_content: str | None = None) -> dict[str, Any]:
        snapshot = self.crawler.fetch_basic_snapshot(symbol=symbol)
        analysis = self.analyzer.analyze(snapshot)
        result = {
            "snapshot": asdict(snapshot),
            "analysis": analysis,
        }

        if news_content and self.news_cleaner:
            result["cleaned_news"] = self.news_cleaner.clean(news_content).model_dump()

        return result
