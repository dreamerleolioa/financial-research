# backend/tests/test_analysis_cache.py
from __future__ import annotations

from datetime import time
from unittest.mock import MagicMock, patch

import pytest

from ai_stock_sentinel import api
from ai_stock_sentinel.config import STRATEGY_VERSION


def _make_mock_cache(is_final: bool, action_tag: str = "Hold", confidence: float = 72.5):
    c = MagicMock()
    c.analysis_is_final = is_final
    c.symbol = "2330.TW"
    c.action_tag = action_tag
    c.signal_confidence = confidence
    c.recommended_action = "觀望"
    c.final_verdict = "中性"
    c.strategy_version = STRATEGY_VERSION
    return c


def test_final_cache_hit_returns_without_disclaimer():
    """is_final=TRUE 的快取命中時，intraday_disclaimer 為 None。"""
    mock_cache = _make_mock_cache(is_final=True)

    with patch("ai_stock_sentinel.api.get_analysis_cache", return_value=mock_cache):
        db = MagicMock()
        result = api._handle_cache_hit(mock_cache, now_time=time(10, 0))

    assert result.is_final is True
    assert result.intraday_disclaimer is None


def test_intraday_cache_returns_with_disclaimer():
    """is_final=FALSE + 盤中時間，回傳含免責聲明。"""
    mock_cache = _make_mock_cache(is_final=False)

    result = api._handle_cache_hit(mock_cache, now_time=time(10, 30))

    assert result.is_final is False
    assert result.intraday_disclaimer is not None


def test_stale_cache_is_none_after_market_close():
    """is_final=FALSE + 收盤後（≥13:30），_handle_cache_hit 回傳 None（強制重新分析）。"""
    mock_cache = _make_mock_cache(is_final=False)

    result = api._handle_cache_hit(mock_cache, now_time=time(14, 0))

    assert result is None


def test_build_response_intraday_has_disclaimer():
    """is_final=False 時 _build_analysis_response 含 intraday_disclaimer。"""
    result = api._build_analysis_response(
        symbol="2330.TW",
        action_tag="Hold",
        signal_confidence=65.0,
        recommended_action="觀望",
        final_verdict="中性",
        is_final=False,
    )
    assert result.is_final is False
    assert result.intraday_disclaimer is not None


def test_cache_version_mismatch_returns_none():
    """快取版本與當前版本不一致時，_handle_cache_hit 回傳 None（觸發重新分析）。"""
    mock_cache = _make_mock_cache(is_final=True)
    mock_cache.strategy_version = "0.9.0"  # 舊版本

    result = api._handle_cache_hit(mock_cache, now_time=time(10, 0))

    assert result is None


def test_cache_null_version_returns_none():
    """strategy_version 為 NULL 的舊快取，_handle_cache_hit 回傳 None（視為版本失效）。"""
    mock_cache = _make_mock_cache(is_final=True)
    mock_cache.strategy_version = None

    result = api._handle_cache_hit(mock_cache, now_time=time(10, 0))

    assert result is None


def test_build_response_final_no_disclaimer():
    """is_final=True 時 _build_analysis_response 無 intraday_disclaimer。"""
    result = api._build_analysis_response(
        symbol="2330.TW",
        action_tag="Hold",
        signal_confidence=65.0,
        recommended_action="觀望",
        final_verdict="中性",
        is_final=True,
    )
    assert result.is_final is True
    assert result.intraday_disclaimer is None
