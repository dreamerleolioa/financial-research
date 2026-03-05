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


def test_derive_technical_signal_mixed_signals():
    """衝突訊號（bullish RSI 但空頭 MA 排列）→ sideways（加權後中性）"""
    # 遞減序列（MA 空頭排列），最後幾根反彈（RSI 偏高）
    # 先下跌 20 根，再反彈 5 根，MA 仍空頭但 RSI 可能回升
    closes = list(range(120, 100, -1)) + list(range(100, 105))  # 25 根
    result = _derive_technical_signal(closes)
    # 混合訊號 → 不一定是 sideways，但不應全是 bullish（因 MA 仍空頭）
    assert result in ("sideways", "bearish")  # 不應是 bullish


def test_score_node_with_real_bullish_data():
    """整合測試：遞增 close 資料 + positive 新聞 → signal_confidence > 50"""
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "cleaned_news": {"sentiment_label": "positive"},
        "institutional_flow": None,
        "snapshot": {"recent_closes": list(range(80, 106))},
        "errors": [],
    }
    result = score_node(state)
    assert result["signal_confidence"] > 50
    assert result["data_confidence"] is not None
    assert result["confidence_score"] == result["signal_confidence"]  # 向後相容
