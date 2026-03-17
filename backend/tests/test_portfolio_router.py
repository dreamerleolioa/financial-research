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


def _make_client_with_item(item: MagicMock, user_id: int = 1) -> TestClient:
    mock_user = MagicMock()
    mock_user.id = user_id

    mock_db = MagicMock()
    mock_db.get.return_value = item

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def _make_portfolio_item(user_id: int = 1) -> MagicMock:
    item = MagicMock()
    item.id = 42
    item.user_id = user_id
    item.symbol = "2330.TW"
    item.entry_price = 900.0
    item.quantity = 100
    item.entry_date = "2026-01-01"
    item.notes = None
    return item


# ── Task 5: PUT /portfolio/{id} ──────────────────────────────

def test_update_portfolio_success():
    """PUT /portfolio/{id} 應更新持倉資料並回傳 200。"""
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)
    resp = client.put("/portfolio/42", json={
        "entry_price": 950.0,
        "quantity": 200,
        "entry_date": "2026-02-01",
        "notes": "加碼",
    })
    assert resp.status_code == 200


def test_update_portfolio_forbidden():
    """非持倉擁有者呼叫 PUT /portfolio/{id} 應回傳 403。"""
    item = _make_portfolio_item(user_id=99)
    client = _make_client_with_item(item, user_id=1)
    resp = client.put("/portfolio/42", json={
        "entry_price": 950.0,
        "quantity": 200,
        "entry_date": "2026-02-01",
    })
    assert resp.status_code == 403


# ── Task 6: DELETE /portfolio/{id} ──────────────────────────

def test_delete_portfolio_success():
    """DELETE /portfolio/{id} 應回傳 204。"""
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)
    resp = client.delete("/portfolio/42")
    assert resp.status_code == 204


def test_delete_portfolio_forbidden():
    """非持倉擁有者呼叫 DELETE /portfolio/{id} 應回傳 403。"""
    item = _make_portfolio_item(user_id=99)
    client = _make_client_with_item(item, user_id=1)
    resp = client.delete("/portfolio/42")
    assert resp.status_code == 403


# ── Task 2: GET /portfolio/latest-history ────────────────────

def _make_latest_history_client(portfolios, log_rows) -> TestClient:
    """Helper: 建立帶有 mock DB 的 TestClient，模擬 latest-history endpoint 的兩次 execute。"""
    mock_user = MagicMock()
    mock_user.id = 1

    # 第一次 execute 回傳 portfolios，第二次回傳 log rows（subquery）
    portfolios_result = MagicMock()
    portfolios_result.all.return_value = portfolios

    log_result = MagicMock()
    log_result.mappings.return_value.all.return_value = log_rows

    mock_db = MagicMock()
    mock_db.execute.side_effect = [portfolios_result, log_result]

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def test_latest_history_returns_empty_when_no_portfolios():
    """無 active 持倉時應回傳空 dict。"""
    mock_user = MagicMock()
    mock_user.id = 1

    portfolios_result = MagicMock()
    portfolios_result.all.return_value = []

    mock_db = MagicMock()
    mock_db.execute.return_value = portfolios_result

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)

    resp = client.get("/portfolio/latest-history")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_latest_history_returns_latest_per_portfolio():
    """有持倉且有 log 時，應以 portfolio_id 為 key 回傳最新一筆。"""
    from datetime import date

    portfolio = MagicMock()
    portfolio.id = 42
    portfolio.symbol = "2330.TW"

    log_row = {
        "symbol": "2330.TW",
        "record_date": date(2026, 3, 10),
        "signal_confidence": 75.0,
        "action_tag": "Trim",
        "recommended_action": "部分獲利了結",
        "indicators": None,
        "final_verdict": None,
        "prev_action_tag": "Hold",
        "prev_confidence": 60.0,
    }

    client = _make_latest_history_client([portfolio], [log_row])
    resp = client.get("/portfolio/latest-history")
    assert resp.status_code == 200
    data = resp.json()
    assert "42" in data
    assert data["42"]["action_tag"] == "Trim"
    assert data["42"]["record_date"] == "2026-03-10"
    assert data["42"]["signal_confidence"] == 75.0
