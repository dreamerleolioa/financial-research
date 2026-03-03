from __future__ import annotations

import argparse
import json
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from ai_stock_sentinel.agents.crawler_agent import StockCrawlerAgent
from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.config import load_settings
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler


def build_agent() -> StockCrawlerAgent:
    load_dotenv()
    settings = load_settings()

    llm = None
    if settings.openai_api_key:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=0.2,
        )

    analyzer = LangChainStockAnalyzer(llm=llm)
    news_cleaner = FinancialNewsCleaner(model=settings.openai_model)
    crawler = YFinanceCrawler()
    return StockCrawlerAgent(crawler=crawler, analyzer=analyzer, news_cleaner=news_cleaner)


def read_news_input(news_file: str | None, news_text: str | None) -> str | None:
    if news_text:
        return news_text
    if news_file:
        with open(news_file, "r", encoding="utf-8") as file:
            return file.read()
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        return raw or None
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Stock Sentinel crawler")
    parser.add_argument("--symbol", type=str, default="2330.TW")
    parser.add_argument("--news-file", type=str, help="財經新聞文字檔路徑")
    parser.add_argument("--news-text", type=str, help="直接傳入財經新聞內容")
    args = parser.parse_args()

    agent = build_agent()
    news_content = read_news_input(news_file=args.news_file, news_text=args.news_text)
    result = agent.run(symbol=args.symbol, news_content=news_content)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
