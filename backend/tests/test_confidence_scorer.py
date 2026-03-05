"""adjust_confidence_by_divergence 單元測試：純 rule-based 信心分數計算。"""
from __future__ import annotations

import pytest

from ai_stock_sentinel.analysis.confidence_scorer import adjust_confidence_by_divergence
from ai_stock_sentinel.analysis.confidence_scorer import derive_technical_score

BASE = 50


# ─── 四個主要情境 ──────────────────────────────────────────────────────────────

def test_three_dimensional_resonance():
    """三維共振：sentiment=positive + flow=institutional_accumulation + technical=bullish → +15"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    assert score == 65
    assert "三維訊號共振" in note
    assert "法人買超" in note


def test_bullish_news_with_distribution():
    """利多出貨：sentiment=positive + flow=distribution → -20"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="sideways",
    )
    assert score == 30
    assert "警示" in note
    assert "出貨" in note


def test_retail_chasing():
    """散戶追高：flow=retail_chasing → -15"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="neutral",
        inst_flow="retail_chasing",
        technical_signal="bullish",
    )
    assert score == 35
    assert "散戶追高" in note


def test_bearish_news_but_price_holds():
    """利空不跌：sentiment=negative + technical=bullish → +10"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="negative",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    assert score == 60
    assert "利空不跌" in note


# ─── 預設情境 ──────────────────────────────────────────────────────────────────

def test_default_no_adjustment():
    """無符合規則時 → adjustment=0，note="""""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="neutral",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    assert score == BASE
    assert note == ""


# ─── 優先序：第一個符合的規則勝出 ─────────────────────────────────────────────

def test_priority_three_resonance_over_retail_chasing():
    """三維共振優先序高於散戶追高規則（若同時符合，三維共振先匹配）"""
    # retail_chasing 情況下若 sentiment=positive 且 technical=bullish，三維共振不成立（flow 不是 institutional_accumulation）
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="retail_chasing",
        technical_signal="bullish",
    )
    # retail_chasing 規則：-15
    assert score == 35
    assert "散戶追高" in note


def test_priority_distribution_over_bearish_not_dropping():
    """利多出貨規則 (rule 2) 優先於利空不跌 (rule 4)：
    sentiment=positive + flow=distribution 應觸發利多出貨，而非利空不跌。"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="bullish",  # 即便技術偏多，仍走利多出貨規則
    )
    assert score == 30
    assert "警示" in note


# ─── Clamp [0, 100] ────────────────────────────────────────────────────────────

def test_clamp_upper_bound():
    """超過 100 → 夾在 100"""
    score, _ = adjust_confidence_by_divergence(
        95,  # 95 + 15 = 110 → clamp 100
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    assert score == 100


def test_clamp_lower_bound():
    """低於 0 → 夾在 0"""
    score, _ = adjust_confidence_by_divergence(
        10,  # 10 - 20 = -10 → clamp 0
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="sideways",
    )
    assert score == 0


# ─── base_score 傳入驗證 ────────────────────────────────────────────────────────

def test_custom_base_score():
    """custom base_score 傳入正確套用"""
    score, note = adjust_confidence_by_divergence(
        70,
        news_sentiment="negative",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    assert score == 80  # 70 + 10
    assert "利空不跌" in note


def test_base_score_50_default_neutral():
    """base=50 + 無規則 → 50"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="neutral",
        inst_flow="neutral",
        technical_signal="bearish",
    )
    assert score == 50
    assert note == ""


# ─── derive_technical_score ───────────────────────────────────────────────────

def test_technical_score_insufficient_data():
    """資料不足（< 20 根）回傳 50"""
    assert derive_technical_score([100.0] * 15, rsi=60.0, bias=0.0) == 50


def test_technical_score_all_bullish():
    """三個訊號全多頭：score=+3 → 70"""
    closes = list(range(80, 101))  # 21 根，遞增排列，ma5 > ma20
    # rsi=60 → +1, bias=2.0 → +1, ma多頭排列 → +1
    result = derive_technical_score(closes, rsi=60.0, bias=2.0)
    assert result == 70


def test_technical_score_all_bearish():
    """三個訊號全空頭：score=-3 → 30"""
    closes = list(range(120, 99, -1))  # 21 根，遞減
    # rsi=25 → -1, bias=-12 → -1, ma空頭排列 → -1
    result = derive_technical_score(closes, rsi=25.0, bias=-12.0)
    assert result == 30


def test_technical_score_neutral():
    """三個訊號中性：score=0 → 50"""
    closes = [100.0] * 21  # 全平，ma5=ma20=close
    result = derive_technical_score(closes, rsi=45.0, bias=0.0)
    assert result == 50


def test_technical_score_partial_bullish():
    """只有 RSI 多頭，bias=0 不觸發加分，MA 全平中性：score=+1 → 57"""
    closes = [100.0] * 21
    result = derive_technical_score(closes, rsi=55.0, bias=0.0)
    # RSI=55 → +1；bias=0.0 不滿足 0 < bias <= 5 → 0；MA 全平 → 0；總分 +1
    # round(50 + 1 * (20/3)) = round(56.67) = 57
    assert result == 57
