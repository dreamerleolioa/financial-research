from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

import pandas as pd

from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.models import StockSnapshot


def _make_history(close_values: list[float], volume_values: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"Close": close_values, "Volume": volume_values})


def test_fetch_basic_snapshot_prefers_fast_info_last_volume() -> None:
    crawler = YFinanceCrawler()

    mock_info = MagicMock()
    mock_info.currency = "TWD"
    mock_info.last_price = 100.0
    mock_info.previous_close = 99.0
    mock_info.open = 98.5
    mock_info.day_high = 101.0
    mock_info.day_low = 98.0
    mock_info.last_volume = 123456

    mock_ticker = MagicMock()
    mock_ticker.fast_info = mock_info
    mock_ticker.history.return_value = _make_history([95.0, 98.0, 100.0], [111, 222, 333])

    with patch("ai_stock_sentinel.data_sources.yfinance_client.yf.Ticker", return_value=mock_ticker):
        snapshot = crawler.fetch_basic_snapshot("2330.TW")

    assert snapshot.volume == 123456
    assert snapshot.volume_source == "realtime"


def test_fetch_basic_snapshot_falls_back_to_history_volume_when_last_volume_missing() -> None:
    crawler = YFinanceCrawler()

    mock_info = MagicMock()
    mock_info.currency = "TWD"
    mock_info.last_price = 100.0
    mock_info.previous_close = 99.0
    mock_info.open = 98.5
    mock_info.day_high = 101.0
    mock_info.day_low = 98.0
    mock_info.last_volume = 0

    mock_ticker = MagicMock()
    mock_ticker.fast_info = mock_info
    mock_ticker.history.return_value = _make_history([95.0, 98.0, 100.0], [111, 222, 333])

    with patch("ai_stock_sentinel.data_sources.yfinance_client.yf.Ticker", return_value=mock_ticker):
        snapshot = crawler.fetch_basic_snapshot("2330.TW")

    assert snapshot.volume == 333
    assert snapshot.volume_source == "history_fallback"


# ─── StockSnapshot 位階欄位測試 ───────────────────────────────────────────────

def test_stock_snapshot_computes_high_low_support_resistance_from_closes():
    """20 筆以上資料時，high_20d/low_20d/support_20d/resistance_20d 應正確計算。"""
    closes = [float(90 + i) for i in range(25)]  # 90.0 .. 114.0（25筆）
    snapshot = StockSnapshot(
        symbol="TEST",
        currency="TWD",
        current_price=114.0,
        previous_close=113.0,
        day_open=112.0,
        day_high=115.0,
        day_low=111.0,
        volume=1000,
        recent_closes=closes,
        fetched_at="2026-03-06T00:00:00+00:00",
    )
    # 最後 20 筆：95.0 ~ 114.0
    assert snapshot.high_20d == pytest.approx(114.0)
    assert snapshot.low_20d == pytest.approx(95.0)
    assert snapshot.support_20d == pytest.approx(95.0 * 0.99)
    assert snapshot.resistance_20d == pytest.approx(114.0 * 1.01)


def test_stock_snapshot_price_levels_use_all_data_when_less_than_20():
    """少於 20 筆但 >= 2 筆，應使用全部資料計算。"""
    closes = [100.0, 110.0, 90.0]
    snapshot = StockSnapshot(
        symbol="TEST",
        currency="TWD",
        current_price=90.0,
        previous_close=110.0,
        day_open=100.0,
        day_high=111.0,
        day_low=89.0,
        volume=500,
        recent_closes=closes,
        fetched_at="2026-03-06T00:00:00+00:00",
    )
    assert snapshot.high_20d == pytest.approx(110.0)
    assert snapshot.low_20d == pytest.approx(90.0)
    assert snapshot.support_20d == pytest.approx(90.0 * 0.99)
    assert snapshot.resistance_20d == pytest.approx(110.0 * 1.01)


def test_stock_snapshot_price_levels_none_when_insufficient_data():
    """少於 2 筆資料時，位階欄位應保留 None。"""
    snapshot = StockSnapshot(
        symbol="TEST",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=98.0,
        day_high=101.0,
        day_low=97.0,
        volume=0,
        recent_closes=[100.0],
        fetched_at="2026-03-06T00:00:00+00:00",
    )
    assert snapshot.high_20d is None
    assert snapshot.low_20d is None
    assert snapshot.support_20d is None
    assert snapshot.resistance_20d is None
