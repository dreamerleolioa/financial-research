from __future__ import annotations

from datetime import time

from ai_stock_sentinel.analysis.schemas import PositionAnalyzeRequest
from ai_stock_sentinel.graph.state import GraphState


def build_position_analyze_initial_state(
    payload: PositionAnalyzeRequest,
    *,
    now_time: time,
    market_close: time,
    prev_context: dict | None,
) -> GraphState:
    return {
        "symbol": payload.symbol,
        "entry_price": payload.entry_price,
        "entry_date": payload.entry_date,
        "quantity": payload.quantity,
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
        "profit_loss_pct": None,
        "cost_buffer_to_support": None,
        "position_status": None,
        "position_narrative": None,
        "trailing_stop": None,
        "trailing_stop_reason": None,
        "recommended_action": None,
        "exit_reason": None,
        "distance_to_trailing_stop_pct": None,
        "distance_to_support_pct": None,
        "unrealized_pnl": None,
        "holding_days": None,
        "prev_context": prev_context,
        "is_final": now_time >= market_close,
    }
