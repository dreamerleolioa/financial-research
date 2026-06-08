# backend/tests/test_portfolio_router.py
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.db.models import TradeReview, UserPortfolio
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.user_models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, compiler, **kw):
    return "JSON"


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
    """active_count < 8 時應成功建立持倉，回傳 201。"""
    client = _make_client(active_count=7)
    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
    })
    assert resp.status_code == 201


def test_add_portfolio_assigns_position_group_id_uuid():
    client = _make_client(active_count=7)
    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
    })

    assert resp.status_code == 201
    mock_db = api.app.dependency_overrides[get_db]()
    entry = mock_db.add.call_args.args[0]
    assert uuid.UUID(entry.position_group_id).version == 4


def test_add_portfolio_rejects_when_limit_reached():
    """active_count >= 8 時應回傳 422，且 detail 含 '8'。"""
    client = _make_client(active_count=8)
    resp = client.post("/portfolio", json={
        "symbol": "2454.TW",
        "entry_price": 800.0,
        "entry_date": "2026-01-01",
        "quantity": 50,
    })
    assert resp.status_code == 422
    assert "8" in resp.json()["detail"]


@pytest.mark.parametrize("entry_price", [0, -1])
def test_add_portfolio_rejects_non_positive_entry_price(entry_price):
    client = _make_client(active_count=7)

    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": entry_price,
        "entry_date": "2026-01-01",
        "quantity": 100,
    })

    assert resp.status_code == 422


def _make_client_with_item(item: MagicMock, user_id: int = 1) -> TestClient:
    mock_user = MagicMock()
    mock_user.id = user_id

    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = item if item.user_id == user_id else None

    mock_db = MagicMock()
    mock_db.get.return_value = item
    mock_db.execute.return_value = locked_result

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def _make_client_with_db(mock_db: MagicMock, user_id: int = 1) -> TestClient:
    mock_user = MagicMock()
    mock_user.id = user_id

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def _make_portfolio_item(user_id: int = 1) -> MagicMock:
    item = MagicMock()
    item.id = 42
    item.user_id = user_id
    item.position_group_id = "group-42"
    item.symbol = "2330.TW"
    item.entry_price = 900.0
    item.quantity = 100
    item.entry_date = date(2026, 1, 1)
    item.is_active = True
    item.exit_date = None
    item.exit_price = None
    item.exit_quantity = None
    item.exit_fees = None
    item.exit_taxes = None
    item.realized_pnl = None
    item.realized_return_pct = None
    item.holding_days = None
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


@pytest.mark.parametrize("entry_price", [0, -1])
def test_update_portfolio_rejects_non_positive_entry_price(entry_price):
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.put("/portfolio/42", json={
        "entry_price": entry_price,
        "quantity": 200,
        "entry_date": "2026-02-01",
        "notes": "加碼",
    })

    assert resp.status_code == 422


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


def test_close_portfolio_success():
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 100,
        "fees": 10.0,
        "taxes": 5.0,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False
    assert data["exit_date"] == "2026-01-11"
    assert data["exit_price"] == 950.0
    assert data["exit_quantity"] == 100
    assert data["exit_fees"] == 10.0
    assert data["exit_taxes"] == 5.0
    assert data["realized_pnl"] == 4985.0
    assert data["realized_return_pct"] == pytest.approx(5.5389, abs=0.0001)
    assert data["holding_days"] == 10
    assert data["position_group_id"] == "group-42"
    assert item.position_group_id == "group-42"
    assert item.is_active is False


def test_close_portfolio_forbidden():
    item = _make_portfolio_item(user_id=99)
    client = _make_client_with_item(item, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 100,
    })

    assert resp.status_code == 403


def test_close_portfolio_rejects_already_closed():
    item = _make_portfolio_item(user_id=1)
    item.is_active = False
    client = _make_client_with_item(item, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 100,
    })

    assert resp.status_code == 409


def test_close_portfolio_partial_close_success():
    item = _make_portfolio_item(user_id=1)
    item.notes = "初始筆記"
    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = item
    mock_db = MagicMock()
    mock_db.execute.return_value = locked_result
    client = _make_client_with_db(mock_db, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 50,
        "fees": 10.0,
        "taxes": 5.0,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False
    assert data["quantity"] == 50
    assert data["exit_date"] == "2026-01-11"
    assert data["exit_price"] == 950.0
    assert data["exit_quantity"] == 50
    assert data["exit_fees"] == 10.0
    assert data["exit_taxes"] == 5.0
    assert data["realized_pnl"] == 2485.0
    assert data["realized_return_pct"] == pytest.approx(5.5222, abs=0.0001)
    assert data["holding_days"] == 10
    assert data["position_group_id"] == "group-42"
    assert item.is_active is True
    assert item.quantity == 50
    closed_item = mock_db.add.call_args.args[0]
    assert isinstance(closed_item, UserPortfolio)
    assert closed_item.user_id == item.user_id
    assert closed_item.position_group_id == item.position_group_id
    assert closed_item.symbol == item.symbol
    assert closed_item.entry_price == item.entry_price
    assert closed_item.entry_date == item.entry_date
    assert closed_item.notes == item.notes
    assert closed_item.is_active is False
    assert closed_item.quantity == 50
    assert closed_item.exit_quantity == 50
    mock_db.commit.assert_called_once()


def test_close_portfolio_rejects_over_quantity_without_commit():
    item = _make_portfolio_item(user_id=1)
    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = item
    mock_db = MagicMock()
    mock_db.execute.return_value = locked_result
    client = _make_client_with_db(mock_db, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 101,
    })

    assert resp.status_code == 422
    assert resp.json()["detail"] == "出場股數不可大於持有股數"
    assert item.is_active is True
    assert item.quantity == 100
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("exit_price", "Infinity"),
    ("fees", "NaN"),
    ("taxes", "Infinity"),
])
def test_close_portfolio_rejects_non_finite_numbers(field, value):
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)
    payload = {
        "exit_date": '"2026-01-11"',
        "exit_price": "950.0",
        "exit_quantity": "100",
        "fees": "0",
        "taxes": "0",
    }
    payload[field] = value
    body = "{" + ",".join(f'"{key}":{raw_value}' for key, raw_value in payload.items()) + "}"

    resp = client.post(
        "/portfolio/42/close",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 422


def test_close_portfolio_rejects_exit_date_before_entry_date():
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2025-12-31",
        "exit_price": 950.0,
        "exit_quantity": 100,
    })

    assert resp.status_code == 422


def test_close_portfolio_rejects_legacy_zero_entry_price_without_commit():
    item = _make_portfolio_item(user_id=1)
    item.entry_price = 0
    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = item
    mock_db = MagicMock()
    mock_db.execute.return_value = locked_result
    client = _make_client_with_db(mock_db, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 100,
    })

    assert resp.status_code == 422
    assert resp.json()["detail"] == "成本價必須大於 0"
    assert item.is_active is True
    mock_db.commit.assert_not_called()


def test_close_portfolio_does_not_execute_daily_analysis_log_delete():
    item = _make_portfolio_item(user_id=1)
    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = item
    mock_db = MagicMock()
    mock_db.execute.return_value = locked_result
    client = _make_client_with_db(mock_db, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 100,
    })

    assert resp.status_code == 200
    mock_db.delete.assert_not_called()


@pytest.fixture()
def portfolio_db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, TradeReview.__table__])
    with Session(engine) as session:
        yield session


@pytest.fixture()
def portfolio_db_client(portfolio_db_session: Session) -> TestClient:
    api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    api.app.dependency_overrides[get_db] = lambda: portfolio_db_session
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(get_current_user, None)
        api.app.dependency_overrides.pop(get_db, None)


def test_close_portfolio_partial_close_persists_active_and_closed_rows(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
        notes="核心持股",
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 40,
        "fees": 10.0,
        "taxes": 5.0,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] != 42
    assert data["is_active"] is False
    assert data["quantity"] == 40
    assert data["exit_quantity"] == 40
    assert data["realized_pnl"] == 1985.0

    rows = portfolio_db_session.execute(
        select(UserPortfolio).order_by(UserPortfolio.is_active.desc(), UserPortfolio.id.asc())
    ).scalars().all()
    assert len(rows) == 2
    active, closed = rows
    assert active.id == 42
    assert active.is_active is True
    assert active.quantity == 60
    assert active.exit_date is None
    assert closed.is_active is False
    assert closed.quantity == 40
    assert closed.exit_quantity == 40
    assert closed.notes == "核心持股"
    assert active.position_group_id == closed.position_group_id


def test_close_portfolio_full_close_preserves_position_group_id(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-full-close",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 100,
    })

    assert resp.status_code == 200
    assert resp.json()["position_group_id"] == "group-full-close"
    row = portfolio_db_session.get(UserPortfolio, 42)
    assert row.position_group_id == "group-full-close"


def _add_closed_portfolio(
    session: Session,
    portfolio_id: int = 42,
    user_id: int = 1,
    position_group_id: str = "group-review",
) -> UserPortfolio:
    item = UserPortfolio(
        id=portfolio_id,
        user_id=user_id,
        position_group_id=position_group_id,
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
        is_active=False,
        exit_date=date(2026, 1, 11),
        exit_price=950,
        exit_quantity=100,
        realized_pnl=5000,
        realized_return_pct=5.5556,
        holding_days=10,
    )
    session.add(item)
    session.commit()
    return item


def test_create_trade_review_first_post_creates_minimal_saved_review(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio_id"] == 42
    assert data["user_id"] == 1
    assert data["position_group_id"] == "group-review"
    assert data["symbol"] == "2330.TW"
    assert data["review_version"] == "trade-review-v1"
    assert data["llm_summary"] is None
    assert set(data["review_result"]) == {
        "data_quality", "trade_result", "entry_review", "holding_review", "exit_review", "operation_review",
    }
    assert set(data["evidence_payload"]) == {
        "trade", "position_group_id", "path_metrics", "entry_indicators", "exit_indicators", "detected_events", "data_quality",
    }
    assert data["evidence_payload"]["position_group_id"] == "group-review"
    reviews = portfolio_db_session.execute(select(TradeReview)).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].review_result == data["review_result"]
    assert reviews[0].evidence_payload == data["evidence_payload"]


def test_create_trade_review_second_post_returns_existing_without_duplicate(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)

    first = portfolio_db_client.post("/portfolio/42/review")
    second = portfolio_db_client.post("/portfolio/42/review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    reviews = portfolio_db_session.execute(select(TradeReview)).scalars().all()
    assert len(reviews) == 1


def test_get_trade_review_returns_existing_review(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    created = portfolio_db_client.post("/portfolio/42/review").json()

    resp = portfolio_db_client.get("/portfolio/42/review")

    assert resp.status_code == 200
    assert resp.json() == created


def test_create_trade_review_rejects_active_portfolio(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 422
    assert portfolio_db_session.execute(select(TradeReview)).scalars().all() == []


def test_create_trade_review_rejects_other_user_portfolio(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(User(id=2, google_sub="user-2", email="other@example.com"))
    _add_closed_portfolio(portfolio_db_session, portfolio_id=42, user_id=2)

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 403
    assert portfolio_db_session.execute(select(TradeReview)).scalars().all() == []


def test_list_closed_portfolio_returns_realized_fields():
    item = _make_portfolio_item(user_id=1)
    item.is_active = False
    item.exit_date = date(2026, 1, 11)
    item.exit_price = 950.0
    item.exit_quantity = 100
    item.exit_fees = 10.0
    item.exit_taxes = 5.0
    item.realized_pnl = 4985.0
    item.realized_return_pct = 5.5389
    item.holding_days = 10

    result = MagicMock()
    result.scalars.return_value.all.return_value = [item]
    mock_db = MagicMock()
    mock_db.execute.return_value = result
    client = _make_client_with_db(mock_db, user_id=1)

    resp = client.get("/portfolio/closed")

    assert resp.status_code == 200
    assert resp.json()[0]["realized_pnl"] == 4985.0
    assert resp.json()[0]["holding_days"] == 10
    assert resp.json()[0]["position_group_id"] == "group-42"


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
    portfolio.entry_date = date(2026, 3, 1)
    portfolio.exit_date = None

    log_row = {
        "portfolio_id": 42,
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
