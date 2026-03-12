# backend/tests/test_portfolio_history.py
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.auth.dependencies import get_current_user


def _make_client(portfolio=None, records=None, total=0):
    mock_user = MagicMock()
    mock_user.id = 1

    mock_db = MagicMock()

    # mock db.get(UserPortfolio, portfolio_id)
    mock_db.get.return_value = portfolio

    # mock db.execute(...).scalar() for COUNT
    # mock db.execute(...).scalars().all() for records
    def _execute(stmt):
        result = MagicMock()
        result.scalar.return_value = total
        result.scalars.return_value.all.return_value = records or []
        return result

    mock_db.execute.side_effect = _execute

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def test_history_returns_404_when_portfolio_not_found():
    """持倉不存在時回傳 404。"""
    client = _make_client(portfolio=None)
    resp = client.get("/portfolio/999/history")
    assert resp.status_code == 404


def test_history_returns_404_when_portfolio_belongs_to_other_user():
    """持倉屬於其他使用者時回傳 404。"""
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 99  # 不是 user.id=1
    mock_portfolio.symbol = "2330.TW"
    client = _make_client(portfolio=mock_portfolio)
    resp = client.get("/portfolio/1/history")
    assert resp.status_code == 404


def test_history_returns_records():
    """應回傳指定持倉的歷史分析紀錄。"""
    from datetime import date
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2330.TW"

    mock_record = MagicMock()
    mock_record.record_date = date(2026, 3, 10)
    mock_record.signal_confidence = 72.5
    mock_record.action_tag = "Hold"
    mock_record.recommended_action = "觀望"
    mock_record.indicators = {}
    mock_record.final_verdict = "中性"
    mock_record.prev_action_tag = None
    mock_record.prev_confidence = None

    client = _make_client(portfolio=mock_portfolio, records=[mock_record], total=1)
    resp = client.get("/portfolio/1/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "2330.TW"
    assert data["total"] == 1
    assert len(data["records"]) == 1


def test_history_returns_empty_when_no_records():
    """無紀錄時回傳 total=0、records=[]。"""
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2454.TW"
    client = _make_client(portfolio=mock_portfolio, records=[], total=0)
    resp = client.get("/portfolio/1/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["records"] == []


def test_history_supports_pagination():
    """支援 limit/offset 分頁參數（不報錯即可）。"""
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2330.TW"
    client = _make_client(portfolio=mock_portfolio, records=[], total=0)
    resp = client.get("/portfolio/1/history?limit=5&offset=10")
    assert resp.status_code == 200
