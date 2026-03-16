from __future__ import annotations

import json
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
            news_summary=None,
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
            news_summary=None,
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
            news_summary=None,
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
    """analyze() end-to-end: when JsonOutputParser chain returns a dict, result is AnalysisDetail."""
    from unittest.mock import patch
    from ai_stock_sentinel.models import AnalysisDetail

    dict_response = {"summary": "台積電穩健。", "risks": ["匯率風險"], "technical_signal": "bullish"}

    analyzer = LangChainStockAnalyzer(llm=MagicMock())
    snapshot = _make_snapshot()

    # Patch the chain so invoke returns a parsed dict (as JsonOutputParser would produce)
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = dict_response

    with patch("langchain_core.prompts.ChatPromptTemplate") as mock_template:
        mock_prompt = MagicMock()
        mock_template.from_messages.return_value = mock_prompt
        mock_prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=mock_chain)
        ))
        result = analyzer.analyze(snapshot)

    assert isinstance(result, AnalysisDetail)
    assert result.summary == "台積電穩健。"
    assert result.risks == ["匯率風險"]
    assert result.technical_signal == "bullish"


# ---------------------------------------------------------------------------
# Session 3: AnalysisDetail new fields + _parse_analysis None-safe
# ---------------------------------------------------------------------------

from ai_stock_sentinel.models import AnalysisDetail as _AnalysisDetail


def test_analysis_detail_has_institutional_flow_and_sentiment_label_fields():
    """AnalysisDetail 應包含 institutional_flow 與 sentiment_label 欄位，預設 None。"""
    detail = _AnalysisDetail(summary="摘要")
    assert hasattr(detail, "institutional_flow")
    assert hasattr(detail, "sentiment_label")
    assert detail.institutional_flow is None
    assert detail.sentiment_label is None


def test_parse_analysis_reads_new_fields_from_json():
    """_parse_analysis 能正確讀取 institutional_flow 與 sentiment_label。"""
    raw = '{"summary": "ok", "risks": [], "technical_signal": "bullish", "institutional_flow": "neutral", "sentiment_label": "positive"}'
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.institutional_flow == "neutral"
    assert result.sentiment_label == "positive"


def test_parse_analysis_handles_missing_new_fields_gracefully():
    """_parse_analysis 在 LLM 未回傳新欄位時 fallback 為 None，不崩潰。"""
    raw = '{"summary": "ok", "risks": [], "technical_signal": "sideways"}'
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.institutional_flow is None
    assert result.sentiment_label is None


def test_parse_analysis_empty_string_new_fields_become_none():
    """_parse_analysis 回傳空字串新欄位時應轉換為 None。"""
    raw = '{"summary": "ok", "risks": [], "technical_signal": "sideways", "institutional_flow": "", "sentiment_label": ""}'
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.institutional_flow is None
    assert result.sentiment_label is None


# ---------------------------------------------------------------------------
# Session 5: news_summary LLM Prompt
# ---------------------------------------------------------------------------

from ai_stock_sentinel.analysis.langchain_analyzer import _HUMAN_PROMPT


def test_human_prompt_contains_news_summary_section():
    """_HUMAN_PROMPT 應包含【消息面摘要】段落。"""
    assert "【消息面摘要】" in _HUMAN_PROMPT
    assert "{news_summary}" in _HUMAN_PROMPT


def test_estimate_cost_includes_news_summary_length():
    """_estimate_cost 應將 news_summary 納入長度估算：傳入長字串時費用更高。"""
    analyzer = LangChainStockAnalyzer(llm=None)
    snapshot = _make_snapshot()

    # 無 news_summary 不觸發
    try:
        analyzer._estimate_cost(
            snapshot=snapshot,
            news_summary=None,
            technical_context="",
            institutional_context="",
            confidence_score=50,
            cross_validation_note="",
        )
    except ValueError:
        pytest.fail("Should not raise without news_summary")

    # 極長的 news_summary 應觸發（加進去後超過門檻）
    with pytest.raises(ValueError):
        analyzer._estimate_cost(
            snapshot=snapshot,
            news_summary="N" * 1_400_000,
            technical_context="",
            institutional_context="",
            confidence_score=50,
            cross_validation_note="",
        )


def test_news_summary_fallback_when_cleaned_news_is_none():
    """analyze_node 在 cleaned_news 為 None 時，傳入 analyze() 的 news_summary 應為 None。"""
    from unittest.mock import patch, MagicMock
    from ai_stock_sentinel.graph.nodes import analyze_node
    from ai_stock_sentinel.models import StockSnapshot, AnalysisDetail
    from dataclasses import asdict

    snapshot = StockSnapshot(
        symbol="2330.TW", currency="TWD", current_price=500.0,
        previous_close=498.0, day_open=499.0, day_high=502.0, day_low=497.0,
        volume=1_000_000, recent_closes=[490.0, 495.0, 500.0],
        fetched_at="2026-03-07T00:00:00+00:00",
    )
    state = {
        "snapshot": asdict(snapshot),
        "cleaned_news": None,
        "technical_context": None,
        "institutional_context": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "errors": [],
    }

    captured_kwargs: dict = {}

    def fake_analyze(snap, **kwargs):
        captured_kwargs.update(kwargs)
        return AnalysisDetail(summary="test")

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.side_effect = fake_analyze

    analyze_node(state, analyzer=mock_analyzer)
    assert captured_kwargs.get("news_summary") is None


def test_analyze_node_passes_news_summary_to_analyzer():
    """analyze_node 在 cleaned_news 有值時，應組合 news_summary 並傳入 analyzer。"""
    from unittest.mock import MagicMock
    from ai_stock_sentinel.graph.nodes import analyze_node
    from ai_stock_sentinel.models import StockSnapshot, AnalysisDetail
    from dataclasses import asdict

    snapshot = StockSnapshot(
        symbol="2330.TW", currency="TWD", current_price=500.0,
        previous_close=498.0, day_open=499.0, day_high=502.0, day_low=497.0,
        volume=1_000_000, recent_closes=[490.0, 495.0, 500.0],
        fetched_at="2026-03-07T00:00:00+00:00",
    )
    state = {
        "snapshot": asdict(snapshot),
        "cleaned_news": {
            "title": "台積電法說會利多",
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "positive",
        },
        "technical_context": None,
        "institutional_context": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "errors": [],
    }

    captured_kwargs: dict = {}

    def fake_analyze(snap, **kwargs):
        captured_kwargs.update(kwargs)
        return AnalysisDetail(summary="test")

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.side_effect = fake_analyze

    analyze_node(state, analyzer=mock_analyzer)
    news_summary = captured_kwargs.get("news_summary")
    assert news_summary is not None
    assert "台積電法說會利多" in news_summary
    assert "positive" in news_summary


# ---------------------------------------------------------------------------
# Session 8: Dimensional analysis fields
# ---------------------------------------------------------------------------

def test_analysis_detail_has_dimensional_fields():
    """AnalysisDetail 應包含四個分維度欄位，預設 None。"""
    from ai_stock_sentinel.models import AnalysisDetail
    detail = AnalysisDetail(summary="摘要")
    assert hasattr(detail, "tech_insight")
    assert hasattr(detail, "inst_insight")
    assert hasattr(detail, "news_insight")
    assert hasattr(detail, "final_verdict")
    assert detail.tech_insight is None
    assert detail.inst_insight is None
    assert detail.news_insight is None
    assert detail.final_verdict is None


def test_analysis_detail_accepts_dimensional_field_values():
    """AnalysisDetail 應能接受四個分維度欄位的字串值。"""
    from ai_stock_sentinel.models import AnalysisDetail
    detail = AnalysisDetail(
        summary="摘要",
        tech_insight="均線多頭排列",
        inst_insight="外資連買",
        news_insight="法說會利多",
        final_verdict="三維共振",
    )
    assert detail.tech_insight == "均線多頭排列"
    assert detail.inst_insight == "外資連買"
    assert detail.news_insight == "法說會利多"
    assert detail.final_verdict == "三維共振"


def test_parse_analysis_reads_dimensional_fields():
    """_parse_analysis 能正確讀取四個分維度欄位。"""
    raw = json.dumps({
        "summary": "綜合摘要",
        "risks": [],
        "technical_signal": "bullish",
        "tech_insight": "均線多頭排列，RSI 62 健康。",
        "inst_insight": "外資連買 3 日，籌碼沉澱。",
        "news_insight": "法說會利多，情緒正面。",
        "final_verdict": "三維共振，信心偏高。",
    })
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.tech_insight == "均線多頭排列，RSI 62 健康。"
    assert result.inst_insight == "外資連買 3 日，籌碼沉澱。"
    assert result.news_insight == "法說會利多，情緒正面。"
    assert result.final_verdict == "三維共振，信心偏高。"


def test_parse_analysis_dimensional_fields_none_when_absent():
    """_parse_analysis 在 LLM 未回傳分維度欄位時 fallback 為 None，不崩潰。"""
    raw = '{"summary": "ok", "risks": [], "technical_signal": "sideways"}'
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.tech_insight is None
    assert result.inst_insight is None
    assert result.news_insight is None
    assert result.final_verdict is None


def test_parse_analysis_empty_string_dimensional_fields_become_none():
    """_parse_analysis 回傳空字串分維度欄位時應轉換為 None。"""
    raw = json.dumps({
        "summary": "ok", "risks": [], "technical_signal": "sideways",
        "tech_insight": "", "inst_insight": "", "news_insight": "", "final_verdict": "",
    })
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.tech_insight is None
    assert result.inst_insight is None
    assert result.news_insight is None
    assert result.final_verdict is None


def test_system_prompt_contains_dimensional_section():
    """System Prompt 應包含分維度輸出指令。"""
    import ai_stock_sentinel.analysis.langchain_analyzer as mod
    prompt = mod._SYSTEM_PROMPT
    assert "tech_insight" in prompt, "System Prompt 應包含 tech_insight 欄位說明"
    assert "inst_insight" in prompt, "System Prompt 應包含 inst_insight 欄位說明"
    assert "news_insight" in prompt, "System Prompt 應包含 news_insight 欄位說明"
    assert "final_verdict" in prompt, "System Prompt 應包含 final_verdict 欄位說明"
    assert "禁止跨維度混寫" in prompt or "禁止混入" in prompt, \
        "System Prompt 應包含禁止跨維度混寫的限制"


def test_system_prompt_json_schema_includes_dimensional_fields():
    """System Prompt 的 JSON schema 範例應包含四個分維度欄位。"""
    import ai_stock_sentinel.analysis.langchain_analyzer as mod
    prompt = mod._SYSTEM_PROMPT
    for field in ["tech_insight", "inst_insight", "news_insight", "final_verdict"]:
        assert f'"{field}"' in prompt, f"JSON schema 缺少欄位：{field}"


# ---------------------------------------------------------------------------
# Task 4: Position-aware prompt injection
# ---------------------------------------------------------------------------

def test_position_prompt_injected_when_position_context_provided():
    """When position context is provided, the LLM receives position-mode instructions."""
    from unittest.mock import patch, MagicMock
    from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer
    from ai_stock_sentinel.models import AnalysisDetail

    captured_prompts = []

    dict_response = {"summary": "test", "risks": [], "technical_signal": "bullish"}

    analyzer = LangChainStockAnalyzer(llm=MagicMock())
    snapshot = _make_snapshot()

    mock_chain = MagicMock()
    mock_chain.invoke.return_value = dict_response

    with patch("langchain_core.prompts.ChatPromptTemplate") as mock_template:
        mock_prompt = MagicMock()
        mock_template.from_messages.side_effect = lambda msgs: (
            captured_prompts.extend(msgs) or mock_prompt
        )
        mock_prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=mock_chain)
        ))
        analyzer.analyze(
            snapshot,
            position_context={
                "entry_price": 980.0,
                "profit_loss_pct": 7.14,
                "position_status": "profitable_safe",
                "position_narrative": "獲利已脫離成本區",
                "trailing_stop": 980.0,
                "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價",
                "recommended_action": "Hold",
            },
        )

    full_text = " ".join(str(m) for m in captured_prompts)
    assert "持有倉位" in full_text or "entry_price" in full_text or "持倉" in full_text
    assert "出場" in full_text or "減碼" in full_text


def test_analyze_without_position_context_unchanged():
    """Existing /analyze route: no position_context → no position block in prompt."""
    from unittest.mock import patch, MagicMock
    from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer

    captured_prompts = []

    dict_response = {"summary": "test", "risks": [], "technical_signal": "bullish"}

    analyzer = LangChainStockAnalyzer(llm=MagicMock())
    snapshot = _make_snapshot()

    mock_chain = MagicMock()
    mock_chain.invoke.return_value = dict_response

    with patch("langchain_core.prompts.ChatPromptTemplate") as mock_template:
        mock_prompt = MagicMock()
        mock_template.from_messages.side_effect = lambda msgs: (
            captured_prompts.extend(msgs) or mock_prompt
        )
        mock_prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=mock_chain)
        ))
        analyzer.analyze(snapshot, position_context=None)

    full_text = " ".join(str(m) for m in captured_prompts)
    assert "entry_price" not in full_text


# ---------------------------------------------------------------------------
# Task 3 (Phase 8): Signal continuity section
# ---------------------------------------------------------------------------

def test_position_prompt_includes_history_when_prev_context_provided():
    """有昨日上下文時，position prompt 應包含訊號轉向區塊。
    昨日數據來自 stock_analysis_cache（非 daily_analysis_log）。
    """
    from ai_stock_sentinel.analysis.langchain_analyzer import build_position_history_section

    prev = {
        "prev_action_tag":   "Hold",
        "prev_confidence":   61.5,
        "prev_rsi":          65.2,
        "prev_ma_alignment": "bullish",
    }
    section = build_position_history_section(prev)

    assert "昨日建議：Hold" in section
    assert "61.5" in section
    assert "RSI：65.2" in section


def test_position_prompt_empty_when_no_prev_context():
    """無昨日上下文時，history section 應為空字串。
    （stock_analysis_cache 無該日紀錄時 load_yesterday_context 回傳 None）
    """
    from ai_stock_sentinel.analysis.langchain_analyzer import build_position_history_section

    assert build_position_history_section(None) == ""
