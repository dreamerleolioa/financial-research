from __future__ import annotations

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

import pandas as pd

from ai_stock_sentinel.data_sources.yfinance_client import (
    PORTFOLIO_YFINANCE_TIMEOUT_SECONDS,
    YFinanceCrawler,
    _DeadlineSession,
    curl_requests,
)
from ai_stock_sentinel.models import StockSnapshot


def _make_history(close_values: list[float], volume_values: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Close": close_values,
            "High": [value + 1 for value in close_values],
            "Low": [value - 1 for value in close_values],
            "Volume": volume_values,
        },
        index=pd.date_range("2026-07-27", periods=len(close_values), freq="D"),
    )


def test_fetch_basic_snapshot_prefers_fast_info_last_volume() -> None:
    crawler = YFinanceCrawler()

    mock_info = MagicMock()
    mock_info.currency = "TWD"
    mock_info.exchange = "TAI"
    mock_info.timezone = "Asia/Taipei"
    mock_info.last_price = 100.0
    mock_info.previous_close = 99.0
    mock_info.open = 98.5
    mock_info.day_high = 101.0
    mock_info.day_low = 98.0
    mock_info.last_volume = 123456

    mock_ticker = MagicMock()
    mock_ticker.fast_info = mock_info
    mock_ticker.history.return_value = _make_history([95.0, 98.0, 100.0], [111, 222, 333])
    mock_ticker.history_metadata = {
        "currentTradingPeriod": {
            "regular": {
                "start": int(datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc).timestamp()),
                "end": int(datetime(2026, 7, 31, 5, 30, tzinfo=timezone.utc).timestamp()),
            },
        },
    }

    with (
        patch("ai_stock_sentinel.data_sources.yfinance_client.yf.Ticker", return_value=mock_ticker),
        patch("ai_stock_sentinel.data_sources.yfinance_client.resolve_symbol_name", return_value="台積電"),
    ):
        snapshot = crawler.fetch_basic_snapshot("2330.TW")

    assert snapshot.name == "台積電"
    assert snapshot.exchange == "TAI"
    assert snapshot.exchange_timezone == "Asia/Taipei"
    assert snapshot.regular_market_open == "2026-07-31T01:00:00+00:00"
    assert snapshot.regular_market_close == "2026-07-31T05:30:00+00:00"
    assert snapshot.volume == 123456
    assert snapshot.volume_source == "realtime"
    mock_ticker.history.assert_called_once_with(
        period="1y",
        interval="1d",
        timeout=10.0,
    )


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

    with (
        patch("ai_stock_sentinel.data_sources.yfinance_client.yf.Ticker", return_value=mock_ticker),
        patch("ai_stock_sentinel.data_sources.yfinance_client.resolve_symbol_name", return_value="台積電"),
    ):
        snapshot = crawler.fetch_basic_snapshot("2330.TW")

    assert snapshot.volume == 333
    assert snapshot.volume_source == "history_fallback"


def test_fetch_basic_snapshot_keeps_quote_when_optional_market_metadata_raises() -> None:
    crawler = YFinanceCrawler()

    class FastInfo:
        currency = "TWD"
        last_price = 100.0
        previous_close = 99.0
        open = 98.5
        day_high = 101.0
        day_low = 98.0
        last_volume = 123456

        @property
        def exchange(self):
            raise KeyError("exchangeName")

        @property
        def timezone(self):
            raise KeyError("exchangeTimezoneName")

    mock_ticker = MagicMock()
    mock_ticker.fast_info = FastInfo()
    mock_ticker.history.return_value = _make_history([95.0, 98.0, 100.0], [111, 222, 333])
    mock_ticker.history_metadata = {}

    with (
        patch("ai_stock_sentinel.data_sources.yfinance_client.yf.Ticker", return_value=mock_ticker),
        patch("ai_stock_sentinel.data_sources.yfinance_client.resolve_symbol_name", return_value="台積電"),
    ):
        snapshot = crawler.fetch_basic_snapshot("2330.TW")

    assert snapshot.current_price == 100.0
    assert snapshot.exchange is None
    assert snapshot.exchange_timezone is None


def test_fetch_basic_snapshot_captures_observation_time_before_history_fetch() -> None:
    crawler = YFinanceCrawler()
    events: list[str] = []
    observed_at = datetime(2026, 7, 31, 5, 29, 59, tzinfo=timezone.utc)

    class RecordingDateTime:
        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            events.append("observed")
            return observed_at

    class FastInfo:
        currency = "TWD"
        open = 98.5
        day_high = 101.0
        day_low = 98.0
        last_volume = 123456

        @property
        def last_price(self):
            events.append("price")
            return 100.0

        @property
        def previous_close(self):
            events.append("previous_close")
            return 99.0

    mock_ticker = MagicMock()
    mock_ticker.fast_info = FastInfo()
    mock_ticker.history_metadata = {}

    def delayed_history(**_kwargs):
        events.append("history")
        return _make_history([95.0, 98.0, 100.0], [111, 222, 333])

    mock_ticker.history.side_effect = delayed_history

    with (
        patch("ai_stock_sentinel.data_sources.yfinance_client.datetime", RecordingDateTime),
        patch("ai_stock_sentinel.data_sources.yfinance_client.yf.Ticker", return_value=mock_ticker),
        patch("ai_stock_sentinel.data_sources.yfinance_client.resolve_symbol_name", return_value="台積電"),
    ):
        snapshot = crawler.fetch_basic_snapshot("2330.TW")

    assert events.index("price") < events.index("observed") < events.index("previous_close")
    assert events.index("observed") < events.index("history")
    assert snapshot.fetched_at == observed_at.isoformat()


def test_deadline_session_clamps_each_http_hop_to_remaining_deadline() -> None:
    session = _DeadlineSession()
    response = object()

    with patch.object(curl_requests.Session, "request", return_value=response) as request:
        with session.deadline(0.25):
            assert session.request("GET", "https://query.example.test", timeout=30) is response

    timeout = request.call_args.kwargs["timeout"]
    assert 0 < timeout <= 0.25


def test_deadline_session_rejects_requests_after_deadline() -> None:
    session = _DeadlineSession()

    with session.deadline(0):
        with pytest.raises(TimeoutError, match="provider deadline"):
            session.request("GET", "https://query.example.test")


def test_fetch_portfolio_snapshot_uses_bounded_provider_and_skips_name_lookup() -> None:
    crawler = YFinanceCrawler()
    snapshot = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100,
        previous_close=99,
        day_open=99,
        day_high=101,
        day_low=98,
        volume=100,
        recent_closes=[99, 100],
        fetched_at="2026-07-31T02:00:00+00:00",
    )

    with patch.object(crawler, "fetch_basic_snapshot", return_value=snapshot) as fetch:
        assert crawler.fetch_portfolio_snapshot("2330.TW") is snapshot

    fetch.assert_called_once_with(
        "2330.TW",
        provider_timeout=PORTFOLIO_YFINANCE_TIMEOUT_SECONDS,
        resolve_name=False,
    )


def test_fetch_basic_snapshot_includes_recent_high_low_volume_series() -> None:
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

    with (
        patch("ai_stock_sentinel.data_sources.yfinance_client.yf.Ticker", return_value=mock_ticker),
        patch("ai_stock_sentinel.data_sources.yfinance_client.resolve_symbol_name", return_value="台積電"),
    ):
        snapshot = crawler.fetch_basic_snapshot("2330.TW")

    assert snapshot.recent_highs == [96.0, 99.0, 101.0]
    assert snapshot.recent_lows == [94.0, 97.0, 99.0]
    assert snapshot.recent_high_dates == ["2026-07-27", "2026-07-28", "2026-07-29"]
    assert snapshot.recent_low_dates == ["2026-07-27", "2026-07-28", "2026-07-29"]
    assert snapshot.recent_volumes == [111.0, 222.0, 333.0]
    assert snapshot.recent_volume_dates == ["2026-07-27", "2026-07-28", "2026-07-29"]
    assert snapshot.data_dates == {"ohlcv": "2026-07-29"}


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
