from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.session import get_db


def _make_mock_log(record_date: str, action_tag: str, confidence: float):
    log = MagicMock()
    log.record_date = record_date
    log.signal_confidence = confidence
    log.action_tag = action_tag
    log.prev_action_tag = None
    log.prev_confidence = None
    log.indicators = {"rsi_14": 65.0}
    log.final_verdict = "測試診斷結論"
    log.analysis_is_final = True
    return log


def _make_client():
    mock_user = MagicMock()
    mock_user.id = 1
    mock_db = MagicMock()
    api.app.dependency_overrides[get_current_user] = lambda: mock_user
    api.app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(api.app)


def test_history_returns_list():
    """GET /history/{symbol} 應回傳 list 格式。
    fetch_symbol_history 查 stock_analysis_cache（mock 欄位與 DailyAnalysisLog 相同）。
    """
    mock_logs = [
        _make_mock_log("2026-03-01", "Hold", 61.5),
        _make_mock_log("2026-03-04", "Trim", 74.0),
    ]

    with patch("ai_stock_sentinel.api.fetch_symbol_history", return_value=mock_logs):
        client = _make_client()
        resp = client.get("/history/2330.TW?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["action_tag"] == "Hold"
        assert data[1]["signal_confidence"] == 74.0


def test_history_defaults_to_30_days():
    """未指定 days 參數時預設查詢 30 天。"""
    with patch("ai_stock_sentinel.api.fetch_symbol_history", return_value=[]) as mock_fetch:
        client = _make_client()
        resp = client.get("/history/2330.TW")
        assert resp.status_code == 200
        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("days", 30) == 30
