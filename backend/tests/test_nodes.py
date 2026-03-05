"""_derive_technical_signal 單元測試。"""
from __future__ import annotations

from ai_stock_sentinel.graph.nodes import _derive_technical_signal


def test_derive_technical_signal_bullish_trend():
    """遞增趨勢 → bullish"""
    closes = list(range(80, 106))  # 26 根遞增，ma5 > ma20
    result = _derive_technical_signal(closes)
    assert result == "bullish"


def test_derive_technical_signal_bearish_trend():
    """遞減趨勢 → bearish"""
    closes = list(range(120, 94, -1))  # 26 根遞減
    result = _derive_technical_signal(closes)
    assert result == "bearish"


def test_derive_technical_signal_flat():
    """全平 → sideways"""
    closes = [100.0] * 25
    result = _derive_technical_signal(closes)
    assert result == "sideways"


def test_derive_technical_signal_insufficient():
    """資料不足 → sideways"""
    result = _derive_technical_signal([100.0] * 15)
    assert result == "sideways"
