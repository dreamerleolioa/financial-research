# backend/tests/test_portfolio_router.py
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.auth.dependencies import get_current_user


def _make_client(active_count: int) -> TestClient:
    mock_user = MagicMock()
    mock_user.id = 1

    mock_result = MagicMock()
    mock_result.scalar.return_value = active_count

    mock_db = MagicMock()
    mock_db.execute.return_value = mock_result

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def test_add_portfolio_success():
    """active_count < 5 時應成功建立持倉，回傳 201。"""
    client = _make_client(active_count=3)
    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
    })
    assert resp.status_code == 201


def test_add_portfolio_rejects_when_limit_reached():
    """active_count >= 5 時應回傳 422，且 detail 含 '5'。"""
    client = _make_client(active_count=5)
    resp = client.post("/portfolio", json={
        "symbol": "2454.TW",
        "entry_price": 800.0,
        "entry_date": "2026-01-01",
        "quantity": 50,
    })
    assert resp.status_code == 422
    assert "5" in resp.json()["detail"]
