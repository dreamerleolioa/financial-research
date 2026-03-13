# backend/tests/test_history_loader.py
from __future__ import annotations

from unittest.mock import MagicMock

from ai_stock_sentinel.services.history_loader import load_yesterday_context


def _make_db(row):
    """建立模擬的同步 SQLAlchemy Session。"""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute.return_value = mock_result
    return db


def test_returns_none_when_no_record():
    """昨日無紀錄時回傳 None。"""
    db = _make_db(None)
    result = load_yesterday_context("2330.TW", db)
    assert result is None


def test_returns_context_when_record_exists():
    """有昨日紀錄時回傳對應欄位。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 61.5
    mock_row.indicators = {"rsi_14": 65.2, "ma5": 975.0, "ma20": 960.0, "ma60": 940.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result is not None
    assert result["prev_action_tag"] == "Hold"
    assert result["prev_confidence"] == 61.5
    assert result["prev_rsi"] == 65.2


def test_ma_alignment_bullish():
    """ma5 > ma20 > ma60 時 ma_alignment 為 bullish。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 61.5
    mock_row.indicators = {"rsi_14": 65.2, "ma5": 975.0, "ma20": 960.0, "ma60": 940.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "bullish"


def test_ma_alignment_bearish():
    """ma5 < ma20 < ma60 時 ma_alignment 為 bearish。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Exit"
    mock_row.signal_confidence = 78.0
    mock_row.indicators = {"rsi_14": 72.0, "ma5": 920.0, "ma20": 940.0, "ma60": 960.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "bearish"


def test_ma_alignment_neutral_when_missing():
    """indicators 缺少 ma 值時 ma_alignment 為 neutral。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 55.0
    mock_row.indicators = {"rsi_14": 50.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "neutral"


def test_prev_confidence_none_when_null():
    """signal_confidence 為 None 時 prev_confidence 回傳 None。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = None
    mock_row.indicators = {}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_confidence"] is None


def test_backfill_yesterday_indicators_updates_is_final(monkeypatch) -> None:
    """昨日 is_final=False 時，backfill 應更新 indicators 並設 is_final=True。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None  # 預設昨日無資料

    # 製造昨日 is_final=False 的快取
    cache = MagicMock()
    cache.is_final = False
    cache.symbol = "2330.TW"

    # 第一次 execute 查昨日快取，回傳 cache
    db.execute.return_value.scalar_one_or_none.return_value = cache

    # mock yfinance 回傳含昨日收盤的 history
    import pandas as pd
    from datetime import date, timedelta
    yesterday = date.today() - timedelta(days=1)
    fake_history = pd.DataFrame(
        {"Close": [185.0, 187.0], "Volume": [10000, 12000]},
        index=pd.to_datetime([str(yesterday - timedelta(days=1)), str(yesterday)]),
    )

    with patch("ai_stock_sentinel.services.history_loader.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = fake_history
        backfill_yesterday_indicators(db, "2330.TW")

    db.execute.assert_called()  # 有執行 UPDATE SQL
    db.commit.assert_called_once()


def test_backfill_yesterday_indicators_skips_when_already_final(monkeypatch) -> None:
    """昨日 is_final=True 時，backfill 應跳過，不執行任何 DB 寫入。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    cache = MagicMock()
    cache.is_final = True
    db.execute.return_value.scalar_one_or_none.return_value = cache

    backfill_yesterday_indicators(db, "2330.TW")

    # 只有一次 execute（查詢），沒有 commit
    assert db.execute.call_count == 1
    db.commit.assert_not_called()


def test_backfill_yesterday_indicators_skips_when_no_cache() -> None:
    """昨日無快取時，backfill 應直接 return，不呼叫 yfinance。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    with patch("ai_stock_sentinel.services.history_loader.yf.Ticker") as mock_ticker:
        backfill_yesterday_indicators(db, "2330.TW")
        mock_ticker.assert_not_called()

    db.commit.assert_not_called()
