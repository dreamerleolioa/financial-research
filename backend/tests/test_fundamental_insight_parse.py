import json
from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer


def test_parse_analysis_includes_fundamental_insight():
    raw = json.dumps({
        "summary": "台積電估值偏高，建議觀望。",
        "risks": ["PE 偏高"],
        "technical_signal": "bearish",
        "institutional_flow": "neutral",
        "sentiment_label": "neutral",
        "tech_insight": "技術面偏空",
        "inst_insight": "法人觀望",
        "news_insight": "消息中性",
        "fundamental_insight": "PE 25.6 倍，位於歷史 75 百分位，估值偏貴。",
        "final_verdict": "多維偏空",
    })
    detail = LangChainStockAnalyzer._parse_analysis(raw)
    assert detail.fundamental_insight == "PE 25.6 倍，位於歷史 75 百分位，估值偏貴。"


def test_parse_analysis_fundamental_insight_defaults_to_none():
    raw = json.dumps({
        "summary": "test",
        "risks": [],
        "technical_signal": "sideways",
    })
    detail = LangChainStockAnalyzer._parse_analysis(raw)
    assert detail.fundamental_insight is None
