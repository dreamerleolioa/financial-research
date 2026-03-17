# backend/tests/test_fetch_raw_data.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.db.session import get_db


def _client_with_db() -> TestClient:
    mock_db = MagicMock()
    app = api.app
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def test_fetch_raw_data_requires_api_key():
    """未設定 INTERNAL_API_KEY 環境變數時應回傳 503（fail closed）。"""
    client = _client_with_db()
    resp = client.post("/internal/fetch-raw-data", json={"symbol": "2330.TW"})
    assert resp.status_code == 503


def test_fetch_raw_data_rejects_wrong_key():
    """INTERNAL_API_KEY 已設定但提供錯誤 Key 時應回傳 401。"""
    import ai_stock_sentinel.api as api_module
    original_key = api_module.INTERNAL_API_KEY
    api_module.INTERNAL_API_KEY = "configured-key"
    try:
        client = _client_with_db()
        resp = client.post(
            "/internal/fetch-raw-data",
            json={"symbol": "2330.TW"},
            headers={"X-Internal-Api-Key": "wrong-key"},
        )
        assert resp.status_code == 401
    finally:
        api_module.INTERNAL_API_KEY = original_key


def test_fetch_raw_data_success():
    """提供正確 API Key 時應回傳 200 含 status=ok。"""
    with patch("ai_stock_sentinel.api.fetch_and_store_raw_data", return_value=None):
        import ai_stock_sentinel.api as api_module
        original_key = api_module.INTERNAL_API_KEY
        api_module.INTERNAL_API_KEY = "test-key"
        try:
            client = _client_with_db()
            resp = client.post(
                "/internal/fetch-raw-data",
                json={"symbol": "2330.TW"},
                headers={"X-Internal-Api-Key": "test-key"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            assert resp.json()["symbol"] == "2330.TW"
        finally:
            api_module.INTERNAL_API_KEY = original_key


def test_fetch_raw_data_returns_404_for_unknown_symbol():
    """股票代號無效（yfinance 回傳空資料）時應回傳 404。"""
    import ai_stock_sentinel.api as api_module
    from dataclasses import dataclass

    @dataclass
    class EmptySnapshot:
        current_price: float = 0.0
        symbol: str = "INVALID"
        recent_closes: list = None

        def __post_init__(self):
            if self.recent_closes is None:
                self.recent_closes = []

    original_key = api_module.INTERNAL_API_KEY
    api_module.INTERNAL_API_KEY = "test-key"
    try:
        with patch("ai_stock_sentinel.api.YFinanceCrawler") as mock_crawler_cls:
            mock_crawler_cls.return_value.fetch_basic_snapshot.return_value = EmptySnapshot()
            client = _client_with_db()
            resp = client.post(
                "/internal/fetch-raw-data",
                json={"symbol": "INVALID"},
                headers={"X-Internal-Api-Key": "test-key"},
            )
        assert resp.status_code == 404
        assert "INVALID" in resp.json()["detail"]
    finally:
        api_module.INTERNAL_API_KEY = original_key


def test_fetch_raw_data_calls_data_sources():
    """應呼叫 YFinanceCrawler、fetch_institutional_flow、fetch_fundamental_data，
    並將結果傳入 fetch_and_store_raw_data。
    """
    import ai_stock_sentinel.api as api_module
    from dataclasses import dataclass

    @dataclass
    class FakeSnapshot:
        current_price: float = 100.0
        symbol: str = "2330.TW"
        recent_closes: list = None

        def __post_init__(self):
            if self.recent_closes is None:
                self.recent_closes = [100.0, 101.0]

    fake_snapshot = FakeSnapshot()
    fake_institutional = {"foreign_net": 1000}
    fake_fundamental = {"pe_ratio": 20.0}

    original_key = api_module.INTERNAL_API_KEY
    api_module.INTERNAL_API_KEY = "test-key"
    try:
        with patch("ai_stock_sentinel.api.fetch_and_store_raw_data") as mock_store, \
             patch("ai_stock_sentinel.api.YFinanceCrawler") as mock_crawler_cls, \
             patch("ai_stock_sentinel.api.fetch_institutional_flow", return_value=fake_institutional) as mock_inst, \
             patch("ai_stock_sentinel.api.fetch_fundamental_data", return_value=fake_fundamental) as mock_fund:
            mock_crawler_cls.return_value.fetch_basic_snapshot.return_value = fake_snapshot
            client = _client_with_db()
            resp = client.post(
                "/internal/fetch-raw-data",
                json={"symbol": "2330.TW"},
                headers={"X-Internal-Api-Key": "test-key"},
            )
        assert resp.status_code == 200
        mock_inst.assert_called_once_with("2330.TW", days=10)
        mock_fund.assert_called_once_with("2330.TW", 100.0)
        mock_store.assert_called_once()
        _, kwargs = mock_store.call_args
        assert kwargs["institutional"] == fake_institutional
        assert kwargs["fundamental"] == fake_fundamental
    finally:
        api_module.INTERNAL_API_KEY = original_key
