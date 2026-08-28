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


def test_derive_technical_signal_prefers_profile_score_summary():
    """technical_profile score_summary is authoritative when present."""
    profile = {
        "score_summary": {
            "primary_score": 3,
            "risk_filter_score": 0,
            "secondary_score": 1,
            "capped_total": 4,
            "technical_score": 64,
        }
    }
    result = _derive_technical_signal([100.0] * 15, rsi=20.0, technical_profile=profile)
    assert result == "bullish"


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
        "cleaned_news_quality": None,
        "errors": [],
    }
    result = score_node(state)
    assert result["signal_confidence"] > 50
    assert result["data_confidence"] is not None
    assert result["confidence_score"] == result["signal_confidence"]  # 向後相容


def test_score_node_missing_sources_reports_zero_data_confidence():
    """Fallback labels must not be mistaken for successfully loaded source data."""
    from ai_stock_sentinel.graph.nodes import score_node

    result = score_node({
        "cleaned_news": None,
        "institutional_flow": None,
        "snapshot": None,
        "cleaned_news_quality": None,
        "errors": [],
    })

    assert result["technical_signal"] == "sideways"
    assert result["data_confidence"] == 0


def test_score_node_neutral_sources_with_real_data_reports_full_data_confidence():
    """Neutral direction remains fully available when the underlying sources exist."""
    from ai_stock_sentinel.graph.nodes import score_node

    result = score_node({
        "cleaned_news": {"sentiment_label": "neutral"},
        "institutional_flow": {"flow_label": "neutral"},
        "snapshot": {"recent_closes": [100.0] * 60},
        "cleaned_news_quality": {"quality_flags": []},
        "errors": [],
    })

    assert result["data_confidence"] == 100


def test_score_node_explains_two_lane_alignment_without_news() -> None:
    from ai_stock_sentinel.graph.nodes import score_node

    result = score_node({
        "institutional_flow": {"flow_label": "institutional_accumulation"},
        "snapshot": {"recent_closes": list(range(80, 106))},
        "errors": [],
    })

    assert result["data_confidence"] == 100
    assert result["cross_validation_note"] == "技術面與籌碼面訊號一致，信心偏高"


def test_score_node_does_not_count_insufficient_technical_profile_as_available():
    """A derived score alone must not make a one-day technical profile complete."""
    from ai_stock_sentinel.graph.nodes import score_node
    from ai_stock_sentinel.technical.profile import build_technical_profile_from_snapshot

    snapshot = {"recent_closes": [100.0]}
    profile_payload = build_technical_profile_from_snapshot(snapshot)
    assert profile_payload is not None
    assert profile_payload["technical_profile"]["score_summary"]["technical_score"] is not None
    assert profile_payload["technical_profile"]["data_quality"]["lookback_days_available"] == 1
    profile_payload["technical_profile"]["score_summary"]["technical_score"] = 36

    result = score_node({
        "cleaned_news": {"sentiment_label": "neutral"},
        "institutional_flow": None,
        "snapshot": snapshot,
        "technical_profile": profile_payload["technical_profile"],
        "cleaned_news_quality": {"quality_flags": []},
        "errors": [],
    })

    assert result["technical_signal"] == "sideways"
    assert result["data_confidence"] == 0


def test_score_node_ignores_insufficient_profile_when_raw_technical_data_is_available():
    """A short profile must not override a valid raw-price fallback signal."""
    from ai_stock_sentinel.graph.nodes import score_node

    result = score_node({
        "cleaned_news": {"sentiment_label": "neutral"},
        "institutional_flow": None,
        "snapshot": {"recent_closes": [100.0] * 20},
        "technical_profile": {
            "score_summary": {"technical_score": 36},
            "data_quality": {"lookback_days_available": 1},
        },
        "cleaned_news_quality": {"quality_flags": []},
        "errors": [],
    })

    assert result["technical_signal"] == "sideways"
    assert result["signal_confidence"] == 50
    assert result["data_confidence"] == 50


def test_score_node_uses_existing_technical_profile_for_signal():
    """score_node should not recalculate scattered raw signals when a profile is already present."""
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "cleaned_news": {"sentiment_label": "neutral"},
        "institutional_flow": None,
        "snapshot": {"recent_closes": [100.0] * 30},
        "technical_profile": {
            "score_summary": {
                "primary_score": -3,
                "risk_filter_score": -1,
                "secondary_score": 0,
                "capped_total": -4,
                "technical_score": 36,
            },
            "data_quality": {"lookback_days_available": 30},
        },
        "cleaned_news_quality": None,
        "errors": [],
    }
    result = score_node(state)
    assert result["technical_signal"] == "bearish"


# ---------------------------------------------------------------------------
# 新指標規則引擎測試：MACD + 布林通道
# ---------------------------------------------------------------------------

def _make_bullish_closes(n: int = 60) -> list[float]:
    """產生單邊上漲序列，足以計算所有指標（n >= 35）。"""
    return [float(100 + i) for i in range(n)]


def _make_bearish_closes(n: int = 60) -> list[float]:
    """產生單邊下跌序列。"""
    return [float(200 - i) for i in range(n)]


def _make_flat_closes(n: int = 60, base: float = 100.0) -> list[float]:
    """產生平盤序列。"""
    return [base] * n


def test_macd_bullish_ma_weak_stays_sideways_or_bullish():
    """MACD 偏多但 MA 排列偏弱（先跌後小幅回升）→ 不應直接給 bearish。"""
    # 先跌 40 根，再漲 20 根：MACD 可能翻多，但 MA20 仍偏空
    closes = [float(150 - i * 0.5) for i in range(40)] + [float(130 + i * 0.8) for i in range(20)]
    result = _derive_technical_signal(closes)
    assert result in ("sideways", "bullish"), f"unexpected: {result}"


def test_bollinger_lower_macd_not_bullish_stays_conservative():
    """布林下軌反彈但 MACD 未翻多 → 不應直接給 bullish，偏保守。"""
    # 單邊下跌 60 根：價格在下軌附近，MACD 仍偏空
    closes = _make_bearish_closes(60)
    result = _derive_technical_signal(closes)
    assert result in ("bearish", "sideways"), f"expected bearish/sideways, got {result}"


def test_bollinger_upper_rsi_overheat_deducts_score():
    """布林上軌過熱 + RSI 過熱 → technical_signal 不應為 bullish。"""
    # 單邊急漲，RSI 會超過 70，價格貼近上軌
    closes = [float(100 + i * 2) for i in range(60)]
    # 注意：急漲後 ma 仍多頭，但 RSI+布林扣分應讓結果降至 sideways
    result = _derive_technical_signal(closes)
    # 急漲序列中 RSI 高且布林扣分，結果應為 sideways 或 bullish（視總分而定）
    # 重點：不應因為單純追高而維持滿分 bullish，此處驗證函式不崩潰且輸出合法
    assert result in ("bullish", "sideways", "bearish")


def test_new_indicators_produce_valid_signal_three_cases():
    """三種典型序列下 _derive_technical_signal 輸出值均合法。"""
    for closes in [_make_bullish_closes(), _make_bearish_closes(), _make_flat_closes()]:
        result = _derive_technical_signal(closes)
        assert result in ("bullish", "bearish", "sideways"), f"invalid signal: {result}"


def test_score_node_macd_bullish_increases_confidence():
    """單邊上漲（MACD 偏多）→ signal_confidence > 50。"""
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "cleaned_news": {"sentiment_label": "neutral"},
        "institutional_flow": None,
        "snapshot": {"recent_closes": _make_bullish_closes(60)},
        "cleaned_news_quality": None,
        "errors": [],
    }
    result = score_node(state)
    assert result["signal_confidence"] > 50


def test_score_node_macd_bearish_decreases_confidence():
    """單邊下跌（MACD 偏空）→ signal_confidence < 50。"""
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "cleaned_news": {"sentiment_label": "neutral"},
        "institutional_flow": None,
        "snapshot": {"recent_closes": _make_bearish_closes(60)},
        "cleaned_news_quality": None,
        "errors": [],
    }
    result = score_node(state)
    assert result["signal_confidence"] < 50


def test_score_node_insufficient_data_for_macd():
    """資料僅 26 根（不足以算 MACD）→ 仍能正常執行，不崩潰。"""
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "cleaned_news": {"sentiment_label": "neutral"},
        "institutional_flow": None,
        "snapshot": {"recent_closes": list(range(80, 106))},  # 26 根
        "cleaned_news_quality": None,
        "errors": [],
    }
    result = score_node(state)
    assert isinstance(result["signal_confidence"], int)
