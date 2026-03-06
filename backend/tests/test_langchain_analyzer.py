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


# ---------------------------------------------------------------------------
# AnalysisDetail structured output tests
# ---------------------------------------------------------------------------


def test_parse_analysis_returns_analysis_detail_on_valid_json():
    """_parse_analysis() with valid JSON returns AnalysisDetail with parsed fields."""
    from ai_stock_sentinel.models import AnalysisDetail

    json_response = '{"summary": "台積電股價穩定，技術面偏多。", "risks": ["外資動向不確定", "匯率風險"], "technical_signal": "bullish"}'
    result = LangChainStockAnalyzer._parse_analysis(json_response)

    assert isinstance(result, AnalysisDetail)
    assert result.summary == "台積電股價穩定，技術面偏多。"
    assert result.risks == ["外資動向不確定", "匯率風險"]
    assert result.technical_signal == "bullish"


def test_parse_analysis_returns_fallback_on_invalid_json():
    """_parse_analysis() with non-JSON text falls back to AnalysisDetail with raw summary."""
    from ai_stock_sentinel.models import AnalysisDetail

    raw_text = "這是純文字分析結果，不是 JSON 格式。"
    result = LangChainStockAnalyzer._parse_analysis(raw_text)

    assert isinstance(result, AnalysisDetail)
    assert result.summary == raw_text
    assert result.risks == []
    assert result.technical_signal == "sideways"


def test_parse_analysis_handles_json_code_fence():
    """_parse_analysis() with ```json ... ``` fence strips it and parses correctly."""
    from ai_stock_sentinel.models import AnalysisDetail

    fenced = '```json\n{"summary": "台積電穩健。", "risks": ["匯率風險"], "technical_signal": "bullish"}\n```'
    result = LangChainStockAnalyzer._parse_analysis(fenced)

    assert isinstance(result, AnalysisDetail)
    assert result.summary == "台積電穩健。"
    assert result.risks == ["匯率風險"]
    assert result.technical_signal == "bullish"


def test_parse_analysis_handles_plain_code_fence():
    """_parse_analysis() with ``` ... ``` fence (no language tag) strips it and parses correctly."""
    from ai_stock_sentinel.models import AnalysisDetail

    fenced = '```\n{"summary": "盤整格局。", "risks": ["量能不足", "外資觀望"], "technical_signal": "sideways"}\n```'
    result = LangChainStockAnalyzer._parse_analysis(fenced)

    assert isinstance(result, AnalysisDetail)
    assert result.summary == "盤整格局。"
    assert result.risks == ["量能不足", "外資觀望"]
    assert result.technical_signal == "sideways"


def test_system_prompt_does_not_require_financial_number_extraction() -> None:
    """System Prompt 不應要求 LLM 從新聞提取財務數字。"""
    import ai_stock_sentinel.analysis.langchain_analyzer as mod
    prompt = mod._SYSTEM_PROMPT
    forbidden = ["提取關鍵數值", "EPS", "毛利率", "財報"]
    for term in forbidden:
        assert term not in prompt, f"System Prompt 不應包含財報相關指令：{term!r}"


def test_analyze_returns_analysis_detail_when_llm_returns_json():
    """analyze() end-to-end: when chain returns JSON, result is AnalysisDetail."""
    from unittest.mock import patch
    from ai_stock_sentinel.models import AnalysisDetail

    json_response = '{"summary": "台積電穩健。", "risks": ["匯率風險"], "technical_signal": "bullish"}'

    analyzer = LangChainStockAnalyzer(llm=MagicMock())
    snapshot = _make_snapshot()

    with patch.object(LangChainStockAnalyzer, "_parse_analysis", return_value=AnalysisDetail(
        summary="台積電穩健。",
        risks=["匯率風險"],
        technical_signal="bullish",
    )) as mock_parse:
        # Patch the chain so invoke returns the json string
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = json_response

        with patch("langchain_core.prompts.ChatPromptTemplate") as mock_template:
            mock_prompt = MagicMock()
            mock_template.from_messages.return_value = mock_prompt
            mock_prompt.__or__ = MagicMock(return_value=MagicMock(
                __or__=MagicMock(return_value=mock_chain)
            ))
            result = analyzer.analyze(snapshot)

    assert isinstance(result, AnalysisDetail)
    assert result.technical_signal == "bullish"
