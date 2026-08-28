from __future__ import annotations

from datetime import time
from typing import Any

from ai_stock_sentinel.analysis.schemas import AnalyzeRequest
from ai_stock_sentinel.graph.state import GraphState


def raw_cache_inputs(raw_cache: Any) -> tuple[dict | None, dict | None, dict | None]:
    return raw_cache.technical, raw_cache.institutional, raw_cache.fundamental


def build_analyze_initial_state(
    payload: AnalyzeRequest,
    *,
    now_time: time,
    market_close: time,
    prev_context: dict | None,
    cached_snapshot: dict | None = None,
    cached_institutional: dict | None = None,
    cached_fundamental: dict | None = None,
) -> GraphState:
    return {
        "symbol": payload.symbol,
        "snapshot": cached_snapshot,
        "analysis": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "institutional_flow": cached_institutional,
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
        "fundamental_data": cached_fundamental,
        "prev_context": prev_context,
        "is_final": now_time >= market_close,
    }
