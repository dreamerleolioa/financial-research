from __future__ import annotations

import argparse
import json

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from ai_stock_sentinel.config import configure_logging
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.builder import build_graph


def build_graph_deps():
    """Return the deterministic graph crawler dependency."""
    load_dotenv()
    return YFinanceCrawler()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Stock Sentinel deterministic analyzer")
    parser.add_argument("--symbol", type=str, default="2330.TW")
    args = parser.parse_args()

    graph = build_graph(crawler=build_graph_deps())

    initial_state = {
        "symbol": args.symbol,
        "snapshot": None,
        "analysis": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
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
        "prev_context": None,
        "is_final": True,
    }

    result = graph.invoke(initial_state)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
