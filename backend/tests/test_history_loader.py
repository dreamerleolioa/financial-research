# backend/tests/test_history_loader.py
from __future__ import annotations

from unittest.mock import MagicMock

from ai_stock_sentinel.services.history_loader import (
    load_yesterday_context,
    _compute_indicators_from_history,
)


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
    """昨日 analysis_is_final=False 時，backfill 應更新 indicators 並設 analysis_is_final=True。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None  # 預設昨日無資料

    # 製造昨日 analysis_is_final=False 的快取
    cache = MagicMock()
    cache.analysis_is_final = False
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
    """昨日 analysis_is_final=True 時，backfill 應跳過，不執行任何 DB 寫入。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    cache = MagicMock()
    cache.analysis_is_final = True
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


# ─── _compute_indicators_from_history 新指標測試 ───────────────────────────────

class TestComputeIndicatorsFromHistory:
    def _make_df(self, closes: list[float]):
        import pandas as pd
        return pd.DataFrame({"Close": closes})

    def test_bollinger_fields_present_with_sufficient_data(self):
        """60 筆資料 → bollinger_mid/upper/lower/position 應存在"""
        closes = [float(100 + i * 0.1) for i in range(60)]
        df = self._make_df(closes)
        result = _compute_indicators_from_history(df)
        assert "bollinger_mid" in result
        assert "bollinger_upper" in result
        assert "bollinger_lower" in result
        assert "bollinger_position" in result

    def test_bollinger_fields_none_with_insufficient_data(self):
        """少於 20 筆 → bollinger 欄位應為 None"""
        closes = [100.0] * 10
        df = self._make_df(closes)
        result = _compute_indicators_from_history(df)
        assert result.get("bollinger_mid") is None
        assert result.get("bollinger_upper") is None
        assert result.get("bollinger_lower") is None
        assert result.get("bollinger_position") is None

    def test_macd_fields_present_with_sufficient_data(self):
        """60 筆資料 → macd_line/signal/hist/bias 應存在"""
        closes = [float(100 + i * 0.1) for i in range(60)]
        df = self._make_df(closes)
        result = _compute_indicators_from_history(df)
        assert "macd_line" in result
        assert "macd_signal" in result
        assert "macd_hist" in result
        assert "macd_bias" in result
        assert result["macd_bias"] in ("bullish", "bearish", "neutral", None)

    def test_macd_fields_none_with_insufficient_data(self):
        """少於 35 筆 → macd 欄位應為 None"""
        closes = [100.0] * 25
        df = self._make_df(closes)
        result = _compute_indicators_from_history(df)
        assert result.get("macd_line") is None
        assert result.get("macd_bias") is None

    def test_bollinger_position_near_upper(self):
        """價格拉高到上軌附近 → bollinger_position 應為 near_upper"""
        closes = [100.0] * 19 + [115.0]
        df = self._make_df(closes)
        result = _compute_indicators_from_history(df)
        assert result.get("bollinger_position") == "near_upper"

    def test_bollinger_position_near_lower(self):
        """價格壓低到下軌附近 → bollinger_position 應為 near_lower"""
        closes = [100.0] * 19 + [80.0]
        df = self._make_df(closes)
        result = _compute_indicators_from_history(df)
        assert result.get("bollinger_position") == "near_lower"


# ─── load_yesterday_context 新欄位測試 ─────────────────────────────────────────

def test_load_yesterday_context_includes_macd_bias():
    """load_yesterday_context 回傳值應包含 prev_macd_bias"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 60.0
    mock_row.indicators = {
        "rsi_14": 55.0,
        "ma5": 100.0, "ma20": 98.0, "ma60": 95.0,
        "macd_bias": "bullish",
        "bollinger_position": "above_mid",
    }
    db = _make_db(mock_row)
    result = load_yesterday_context("2330.TW", db)
    assert result is not None
    assert result["prev_macd_bias"] == "bullish"
    assert result["prev_bollinger_position"] == "above_mid"


def test_load_yesterday_context_new_fields_none_when_missing():
    """indicators 沒有 macd_bias/bollinger_position 時，對應欄位應為 None"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 60.0
    mock_row.indicators = {"rsi_14": 55.0}
    db = _make_db(mock_row)
    result = load_yesterday_context("2330.TW", db)
    assert result is not None
    assert result["prev_macd_bias"] is None
    assert result["prev_bollinger_position"] is None


def test_backfill_uses_3mo_period(monkeypatch) -> None:
    """backfill 應使用 period='3mo' 而非 '5d'"""
    from unittest.mock import MagicMock, patch, call
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators
    import pandas as pd
    from datetime import date, timedelta

    db = MagicMock()
    cache = MagicMock()
    cache.analysis_is_final = False
    db.execute.return_value.scalar_one_or_none.return_value = cache

    yesterday = date.today() - timedelta(days=1)
    fake_history = pd.DataFrame(
        {"Close": [185.0, 187.0]},
        index=pd.to_datetime([str(yesterday - timedelta(days=1)), str(yesterday)]),
    )

    with patch("ai_stock_sentinel.services.history_loader.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = fake_history
        backfill_yesterday_indicators(db, "2330.TW")
        mock_ticker.return_value.history.assert_called_once_with(period="3mo", interval="1d")
