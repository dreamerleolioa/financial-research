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
    """未提供 API Key 時應回傳 401。"""
    client = _client_with_db()
    resp = client.post("/internal/fetch-raw-data", json={"symbol": "2330.TW"})
    assert resp.status_code == 401


def test_fetch_raw_data_rejects_wrong_key():
    """提供錯誤 API Key 時應回傳 401。"""
    client = _client_with_db()
    resp = client.post(
        "/internal/fetch-raw-data",
        json={"symbol": "2330.TW"},
        headers={"X-Internal-Api-Key": "wrong-key"},
    )
    assert resp.status_code == 401


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
