from __future__ import annotations

import argparse
import json
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from ai_stock_sentinel.config import configure_logging
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.builder import build_graph


def build_graph_deps():
    """Return graph dependencies without constructing any external LLM client.

    The graph still accepts the legacy dependency tuple during the staged
    migration, but every application entry point forces deterministic mode.
    Passing ``None`` for the retired collaborators also makes an accidental
    model call fail closed instead of using production credentials.
    """
    load_dotenv()
    crawler = YFinanceCrawler()
    return crawler, None, None, None


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
    configure_logging()
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
        llm_enabled=False,
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
        "fundamental_data": None,
        "fundamental_context": None,
        "skip_ai": True,
    }

    result = graph.invoke(initial_state)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
