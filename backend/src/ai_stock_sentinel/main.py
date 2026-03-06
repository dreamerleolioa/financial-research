from __future__ import annotations

import argparse
import json
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.config import load_settings
from ai_stock_sentinel.data_sources.rss_news_client import RssNewsClient
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.builder import build_graph


def build_graph_deps():
    """Return (crawler, analyzer, rss_client, news_cleaner) ready to pass to build_graph()."""
    load_dotenv()
    settings = load_settings()

    llm = None

    # 優先用 Anthropic
    if settings.anthropic_api_key:
        try:
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
                temperature=0.2,
            )
        except ImportError:
            pass  # langchain-anthropic 未安裝，繼續嘗試 OpenAI

    # Fallback 到 OpenAI
    if llm is None and settings.openai_api_key:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=0.2,
        )

    analyzer = LangChainStockAnalyzer(llm=llm)
    crawler = YFinanceCrawler()
    rss_client = RssNewsClient()
    model_name = settings.anthropic_model if settings.anthropic_api_key else settings.openai_model
    news_cleaner = FinancialNewsCleaner(model=model_name)
    return crawler, analyzer, rss_client, news_cleaner


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

    news_content = read_news_input(news_file=args.news_file, news_text=args.news_text)

    crawler, analyzer, rss_client, news_cleaner = build_graph_deps()
    graph = build_graph(
        crawler=crawler,
        analyzer=analyzer,
        rss_client=rss_client,
        news_cleaner=news_cleaner,
    )

    initial_state = {
        "symbol": args.symbol,
        "news_content": news_content,
        "snapshot": None,
        "analysis": None,
        "analysis_detail": None,
        "cleaned_news": None,
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "technical_context": None,
        "institutional_context": None,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "data_confidence": None,
        "signal_confidence": None,
        "high_20d": None,
        "low_20d": None,
        "support_20d": None,
        "resistance_20d": None,
        "rsi14": None,
        "action_plan_tag": None,
        "action_plan": None,
    }

    result = graph.invoke(initial_state)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
