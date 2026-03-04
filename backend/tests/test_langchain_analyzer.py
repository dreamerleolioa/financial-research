from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer
from ai_stock_sentinel.models import StockSnapshot


def _make_snapshot(**kwargs) -> StockSnapshot:
    defaults = dict(
        symbol="2330.TW",
        currency="TWD",
        current_price=500.0,
        previous_close=498.0,
        day_open=499.0,
        day_high=502.0,
        day_low=497.0,
        volume=10_000_000,
        recent_closes=[490.0, 492.0, 495.0, 498.0, 500.0],
        fetched_at="2026-03-04T00:00:00",
    )
    defaults.update(kwargs)
    return StockSnapshot(**defaults)


def test_cost_guard_does_not_trigger_for_normal_request():
    """Normal request (~640 tokens) must not raise."""
    analyzer = LangChainStockAnalyzer(llm=MagicMock())
    snapshot = _make_snapshot()
    try:
        analyzer._estimate_cost(
            snapshot=snapshot,
            technical_context="BIAS 正常，RSI 55。",
            institutional_context="法人小量買超。",
            confidence_score=60,
            cross_validation_note="訊號一致。",
        )
    except ValueError:
        pytest.fail("Cost guard incorrectly triggered for normal request")


def test_cost_guard_triggers_for_oversized_prompt():
    """Prompt > 1,333,333 chars (> 333,333 tokens) must raise ValueError with token count and cost."""
    analyzer = LangChainStockAnalyzer(llm=MagicMock())
    snapshot = _make_snapshot()
    huge_text = "A" * 1_400_000

    with pytest.raises(ValueError) as exc_info:
        analyzer._estimate_cost(
            snapshot=snapshot,
            technical_context=huge_text,
            institutional_context="正常。",
            confidence_score=50,
            cross_validation_note="正常。",
        )

    msg = str(exc_info.value)
    assert "token" in msg.lower(), f"Expected token count in error: {msg}"
    assert "$" in msg, f"Expected cost in error: {msg}"


def test_cost_guard_error_message_contains_numbers():
    """Error message must include estimated token count and dollar amount."""
    import re
    analyzer = LangChainStockAnalyzer(llm=None)
    snapshot = _make_snapshot()
    huge_text = "B" * 1_400_000

    with pytest.raises(ValueError) as exc_info:
        analyzer._estimate_cost(
            snapshot=snapshot,
            technical_context=huge_text,
            institutional_context="",
            confidence_score=50,
            cross_validation_note="",
        )

    msg = str(exc_info.value)
    assert re.search(r'\d+', msg), f"No number found in error message: {msg}"
    assert "$" in msg, f"No dollar sign in error message: {msg}"
