from __future__ import annotations

from datetime import time
from typing import Any

from ai_stock_sentinel.analysis.context_generator import generate_fundamental_context
from ai_stock_sentinel.analysis.schemas import AnalyzeRequest
from ai_stock_sentinel.graph.state import GraphState


def raw_cache_inputs(raw_cache: Any) -> tuple[dict | None, dict | None, dict | None, str | None]:
    cached_snapshot = raw_cache.technical
    cached_institutional = raw_cache.institutional
    cached_fundamental = raw_cache.fundamental
    cached_fundamental_context = None
    if cached_fundamental:
        try:
            cached_fundamental_context = generate_fundamental_context(cached_fundamental)
        except Exception:
            pass
    return cached_snapshot, cached_institutional, cached_fundamental, cached_fundamental_context


def build_analyze_initial_state(
    payload: AnalyzeRequest,
    *,
    now_time: time,
    market_close: time,
    prev_context: dict | None,
    cached_snapshot: dict | None = None,
    cached_institutional: dict | None = None,
    cached_fundamental: dict | None = None,
    cached_fundamental_context: str | None = None,
) -> GraphState:
    return {
        "symbol": payload.symbol,
        "news_content": payload.news_text,
        "snapshot": cached_snapshot,
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
        "institutional_flow": cached_institutional,
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
        "fundamental_data": cached_fundamental,
        "fundamental_context": cached_fundamental_context,
        "prev_context": prev_context,
        "is_final": now_time >= market_close,
        "skip_ai": payload.skip_ai,
    }
