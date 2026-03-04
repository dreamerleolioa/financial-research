from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler


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
