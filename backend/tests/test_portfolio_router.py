# backend/tests/test_portfolio_router.py
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.portfolio import router as portfolio_router_module
from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.daily_radar.repository import upsert_shared_background_context
from ai_stock_sentinel.db.models import (
    Phase1AvwapSnapshot,
    PositionEvent,
    PositionLifecyclePlan,
    PositionLifecycleReview,
    SharedBackgroundContext,
    StockRawData,
    TradeReview,
    UserPortfolio,
)
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.user_models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, compiler, **kw):
    return "JSON"


def _make_client() -> TestClient:
    mock_user = MagicMock()
    mock_user.id = 1

    mock_db = MagicMock()

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def test_add_portfolio_success(monkeypatch: pytest.MonkeyPatch):
    """新增持倉應成功建立持倉，回傳 201。"""
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    client = _make_client()
    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
    })
    assert resp.status_code == 201


def test_add_portfolio_assigns_position_group_id_uuid(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    client = _make_client()
    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
    })

    assert resp.status_code == 201
    mock_db = api.app.dependency_overrides[get_db]()
    entry = next(call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], UserPortfolio))
    assert uuid.UUID(entry.position_group_id).version == 4


def test_add_portfolio_allows_more_than_eight_active_holdings(monkeypatch: pytest.MonkeyPatch):
    """active 持股已達 8 筆時仍可新增持倉。"""
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    client = _make_client()
    resp = client.post("/portfolio", json={
        "symbol": "2454.TW",
        "entry_price": 800.0,
        "entry_date": "2026-01-01",
        "quantity": 50,
    })
    assert resp.status_code == 201
    mock_db = api.app.dependency_overrides[get_db]()
    mock_db.execute.assert_not_called()


def test_add_portfolio_rejects_invalid_symbol(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: False)
    client = _make_client()

    resp = client.post("/portfolio", json={
        "symbol": "9999.TW",
        "entry_price": 800.0,
        "entry_date": "2026-01-01",
        "quantity": 50,
    })

    assert resp.status_code == 404
    assert "查詢目標不存在" in resp.json()["detail"]


@pytest.mark.parametrize("entry_price", [0, -1])
def test_add_portfolio_rejects_non_positive_entry_price(entry_price):
    client = _make_client()

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


def test_update_portfolio_does_not_write_add_entry_event(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-update-no-event",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42", json={
        "entry_price": 950.0,
        "quantity": 200,
        "entry_date": "2026-02-01",
        "notes": "record correction only",
    })

    assert resp.status_code == 200
    assert portfolio_db_session.execute(select(PositionEvent)).scalars().all() == []


def test_add_entry_endpoint_creates_add_entry_event_and_updates_active_row(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-entry",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-10",
        "price": 1000.0,
        "quantity": 50,
        "fees": 20.0,
        "taxes": 0.0,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
        "note": "confirmed scale-in",
    })

    assert resp.status_code == 201
    data = resp.json()
    assert set(data) == {"portfolio", "event"}
    assert data["portfolio"]["id"] == 42
    assert data["portfolio"]["symbol"] == "2330.TW"
    assert data["portfolio"]["name"] == "台積電"
    assert data["portfolio"]["entry_price"] == 933.33
    assert data["portfolio"]["quantity"] == 150
    assert data["portfolio"]["entry_date"] == "2026-01-01"
    assert data["portfolio"]["notes"] is None
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.event_type == "add_entry"
    assert event.source == "user_recorded_at_event_time"
    assert event.source_portfolio_id == 42
    assert event.event_date == date(2026, 1, 10)
    assert float(event.price) == 1000.0
    assert event.quantity == 50
    assert float(event.fees) == 20.0
    assert float(event.taxes) == 0.0
    assert event.reason_category == "plan_execution"
    assert event.reason_code == "planned_scale_in"
    assert event.plan_adherence == "yes"
    assert event.confidence_level == "high"
    assert event.note == "confirmed scale-in"
    item = portfolio_db_session.get(UserPortfolio, 42)
    assert item.quantity == 150
    assert float(item.entry_price) == 933.33


def test_add_entry_endpoint_can_save_plan_adherence_no_for_condition_violation(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-entry-no",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-10",
        "price": 850.0,
        "quantity": 20,
        "reason_code": "averaging_down",
        "plan_adherence": "no",
        "confidence_level": "low",
    })

    assert resp.status_code == 201
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.event_type == "add_entry"
    assert event.reason_code == "averaging_down"
    assert event.plan_adherence == "no"
    assert event.confidence_level == "low"
    assert event.source == "user_recorded_at_event_time"


def test_add_entry_endpoint_rejects_invalid_fixed_option_without_event(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-entry-invalid",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-10",
        "price": 1000.0,
        "quantity": 50,
        "reason_code": "price_went_down",
        "plan_adherence": "yes",
        "confidence_level": "high",
    })

    assert resp.status_code == 422
    assert portfolio_db_session.execute(select(PositionEvent)).scalars().all() == []


def test_add_entry_endpoint_rejects_closed_position_without_event(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-entry-closed",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
        is_active=False,
        exit_date=date(2026, 1, 5),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-10",
        "price": 1000.0,
        "quantity": 50,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
    })

    assert resp.status_code == 409
    assert portfolio_db_session.execute(select(PositionEvent)).scalars().all() == []


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
    closed_item = next(call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], UserPortfolio))
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
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            UserPortfolio.__table__,
            PositionEvent.__table__,
            PositionLifecyclePlan.__table__,
            PositionLifecycleReview.__table__,
            Phase1AvwapSnapshot.__table__,
            TradeReview.__table__,
            StockRawData.__table__,
            SharedBackgroundContext.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture()
def portfolio_db_client(portfolio_db_session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(portfolio_router_module, "ensure_trade_review_market_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(portfolio_router_module, "resolve_symbol_name", lambda symbol: "台積電" if symbol == "2330.TW" else None)
    api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    api.app.dependency_overrides[get_db] = lambda: portfolio_db_session
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(get_current_user, None)
        api.app.dependency_overrides.pop(get_db, None)


def test_list_portfolio_includes_display_name(
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

    resp = portfolio_db_client.get("/portfolio")

    assert resp.status_code == 200
    assert resp.json()[0]["symbol"] == "2330.TW"
    assert resp.json()[0]["name"] == "台積電"


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

    events = portfolio_db_session.execute(select(PositionEvent).order_by(PositionEvent.id)).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "partial_exit"
    assert events[0].source == "user_recorded_at_event_time"
    assert events[0].source_portfolio_id == closed.id
    assert events[0].quantity == 40
    assert float(events[0].fees) == 10.0
    assert float(events[0].taxes) == 5.0


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
    events = portfolio_db_session.execute(select(PositionEvent)).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "full_exit"
    assert events[0].source_portfolio_id == 42


def test_decision_context_status_reports_missing_plan_without_changing_portfolio_response(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-missing-plan",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    portfolio_resp = portfolio_db_client.get("/portfolio")
    status_resp = portfolio_db_client.get("/portfolio/decision-context-status")

    assert portfolio_resp.status_code == 200
    assert set(portfolio_resp.json()[0]) == {"id", "symbol", "name", "entry_price", "quantity", "entry_date", "notes"}
    assert status_resp.status_code == 200
    data = status_resp.json()["42"]
    assert data["portfolio_id"] == 42
    assert data["position_group_id"] == "group-missing-plan"
    assert data["symbol"] == "2330.TW"
    assert data["has_operation_plan"] is False
    assert data["operation_plan_status"] == "missing"
    assert data["missing_operation_plan"] is True
    assert data["decision_context"] == "insufficient"
    assert data["source"] is None
    assert data["created_after_entry"] is None
    assert data["planned_invalidation_present"] is False
    assert data["shared_context"]["consumer"] == "portfolio_diagnosis"


def test_decision_context_status_attaches_shared_context_without_portfolio_action(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-shared-context",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["portfolio_diagnosis"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 1, 2),
        freshness="fresh",
        payload={"major_holder_ratio": 0.61},
        missing_reason=None,
    )
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/decision-context-status")

    assert resp.status_code == 200
    data = resp.json()["42"]
    shared_context = data["shared_context"]
    assert shared_context["consumer"] == "portfolio_diagnosis"
    assert shared_context["contexts"][0]["context_type"] == "weekly_major_holders"
    assert shared_context["contexts"][0]["payload"] == {"major_holder_ratio": 0.61}
    assert "portfolio_action" not in data
    assert "recommended_action" not in data
    assert "action" not in shared_context


def test_portfolio_risk_summary_reads_active_user_positions_only(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(User(id=2, google_sub="user-2", email="other@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-risk-owned",
        symbol="2330.TW",
        entry_price=100,
        quantity=10,
        entry_date=date(2026, 6, 1),
    ))
    portfolio_db_session.add(UserPortfolio(
        id=43,
        user_id=2,
        position_group_id="group-risk-other",
        symbol="2317.TW",
        entry_price=50,
        quantity=10,
        entry_date=date(2026, 6, 1),
    ))
    portfolio_db_session.add(UserPortfolio(
        id=44,
        user_id=1,
        position_group_id="group-risk-closed",
        symbol="2454.TW",
        entry_price=80,
        quantity=10,
        entry_date=date(2026, 6, 1),
        is_active=False,
        exit_date=date(2026, 6, 10),
        exit_price=90,
        exit_quantity=10,
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-risk-owned",
        symbol="2330.TW",
        source_portfolio_id=42,
        setup_type="breakout",
        default_stop_rule="fixed_price",
        planned_stop_price=95,
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2330.TW",
        record_date=date.today(),
        technical={"close_price": 120},
        raw_data_is_final=True,
    ))
    portfolio_db_session.add(Phase1AvwapSnapshot(
        symbol="2330.TW",
        data_date=date.today(),
        dataset="TaiwanStockPrice",
        adjustment_mode="unadjusted",
        source_provider="finmind",
        source_granularity="daily",
        is_final=True,
        freshness="fresh",
        missing_reason=None,
        payload={
            "symbol": "2330.TW",
            "anchors": {
                "entry": {
                    "available": True,
                    "anchor_date": "2026-06-01",
                    "anchor_reason": "holding_entry_date",
                    "avwap": 115,
                    "distance_to_avwap_pct": 4.3478,
                    "source_granularity": "daily",
                    "estimated": False,
                }
            },
            "data_quality": {"estimated": False, "rows_used": 12},
        },
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2317.TW",
        record_date=date.today(),
        technical={"close_price": 60},
        raw_data_is_final=True,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/risk-summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio_value"] == 1200
    assert data["total_unrealized_pnl"] == 200
    assert data["total_at_risk"] == 250
    assert [row["symbol"] for row in data["position_risks"]] == ["2330.TW"]
    assert [row["name"] for row in data["position_risks"]] == ["台積電"]
    phase1_state = data["position_risks"][0]["phase1_position_state"]
    assert phase1_state["state"] == "hold"
    assert phase1_state["label"] == "續抱"
    assert phase1_state["display_anchor"]["type"] == "entry"
    assert phase1_state["data_quality"]["blocking"] is False
    assert "recommended_action" not in data
    assert "portfolio_action" not in data
    assert portfolio_db_session.query(PositionEvent).count() == 0


def test_portfolio_risk_summary_ignores_newer_non_final_raw_data(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-final-price",
        symbol="2330.TW",
        entry_price=100,
        quantity=10,
        entry_date=date(2026, 6, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-final-price",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_stop_price=95,
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2330.TW",
        record_date=date.today() - timedelta(days=1),
        technical={"close_price": 120},
        raw_data_is_final=True,
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2330.TW",
        record_date=date.today(),
        technical={"close_price": 80},
        raw_data_is_final=False,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/risk-summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio_value"] == 1200
    assert data["position_risks"][0]["current_price"] == 120
    assert data["position_risks"][0]["market_value"] == 1200
    assert data["total_unrealized_pnl"] == 200


def test_portfolio_risk_summary_reports_data_gap_caveats(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-no-price",
        symbol="2330.TW",
        entry_price=100,
        quantity=10,
        entry_date=date(2026, 6, 1),
    ))
    portfolio_db_session.add(UserPortfolio(
        id=43,
        user_id=1,
        position_group_id="group-zero-quantity",
        symbol="2317.TW",
        entry_price=50,
        quantity=0,
        entry_date=date(2026, 6, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-no-price",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_stop_price=90,
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2317.TW",
        record_date=date(2026, 1, 1),
        technical={"close_price": 60},
        raw_data_is_final=True,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/risk-summary")

    assert resp.status_code == 200
    data = resp.json()
    caveat_counts = {item["code"]: item["count"] for item in data["data_quality"]["caveats"]}
    assert caveat_counts["missing_price"] == 1
    assert caveat_counts["missing_defense_reference"] == 1
    assert caveat_counts["zero_quantity"] == 1
    assert caveat_counts["stale_price"] == 1
    assert data["data_quality"]["status"] == "insufficient"
    assert data["risk_budget_status"]["notes"] == ["部分部位資料不足，風險預算狀態需搭配 data_quality 解讀。"]


def test_decision_context_status_reads_user_backfilled_plan(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-backfilled-plan",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-backfilled-plan",
        symbol="2330.TW",
        source_portfolio_id=42,
        thesis="Breakout follow-through after consolidation.",
        setup_type="breakout",
        planned_holding_period="swing",
        planned_invalidation="Close below MA20 with institutional distribution.",
        source="user_backfilled",
        created_after_entry=True,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/decision-context-status")

    assert resp.status_code == 200
    data = resp.json()["42"]
    assert data["has_operation_plan"] is True
    assert data["operation_plan_status"] == "backfilled"
    assert data["missing_operation_plan"] is False
    assert data["decision_context"] == "present"
    assert data["source"] == "user_backfilled"
    assert data["created_after_entry"] is True
    assert data["planned_invalidation_present"] is True


def test_decision_context_status_reads_event_time_plan_as_present(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-present-plan",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-present-plan",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_holding_period="swing",
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/decision-context-status")

    assert resp.status_code == 200
    data = resp.json()["42"]
    assert data["operation_plan_status"] == "present"
    assert data["decision_context"] == "present"
    assert data["source"] == "user_recorded_at_event_time"
    assert data["created_after_entry"] is False


def test_backfill_lifecycle_plan_saves_user_backfilled_provenance(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-backfill-save",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42/lifecycle-plan/backfill", json={
        "thesis": "Breakout continuation recorded after entry.",
        "setup_type": "breakout",
        "planned_holding_period": "swing",
        "default_stop_rule": "break_ma20",
        "add_entry_condition": "pullback_holds_ma20",
        "planned_invalidation": "Close below MA20 with distribution.",
        "planned_stop_price": 880.0,
        "planned_target_or_scale_out_rule": "Trim near prior resistance.",
        "planned_risk_amount": 5000.0,
        "planned_risk_pct": 1.25,
        "position_sizing_rationale": "Initial probe only.",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio_id"] == 42
    assert data["position_group_id"] == "group-backfill-save"
    assert data["source"] == "user_backfilled"
    assert data["created_after_entry"] is True
    assert data["setup_type"] == "breakout"
    assert data["planned_holding_period"] == "swing"
    assert data["default_stop_rule"] == "break_ma20"
    assert data["add_entry_condition"] == "pullback_holds_ma20"
    assert data["planned_stop_price"] == 880.0
    assert data["planned_risk_amount"] == 5000.0
    assert data["planned_risk_pct"] == 1.25
    plan = portfolio_db_session.execute(select(PositionLifecyclePlan)).scalar_one()
    assert plan.source == "user_backfilled"
    assert plan.created_after_entry is True
    assert plan.source_portfolio_id == 42


def test_backfill_lifecycle_plan_rejects_invalid_fixed_option_without_plan(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-backfill-invalid",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42/lifecycle-plan/backfill", json={
        "setup_type": "price_went_down",
    })

    assert resp.status_code == 422
    assert portfolio_db_session.execute(select(PositionLifecyclePlan)).scalars().all() == []


def test_backfill_lifecycle_plan_does_not_replace_event_time_plan(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-original-plan",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-original-plan",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_holding_period="swing",
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42/lifecycle-plan/backfill", json={
        "planned_holding_period": "long_term",
    })

    assert resp.status_code == 409
    plan = portfolio_db_session.execute(select(PositionLifecyclePlan)).scalar_one()
    assert plan.source == "user_recorded_at_event_time"
    assert plan.created_after_entry is False
    assert plan.planned_holding_period == "swing"


def test_missing_lifecycle_plan_does_not_block_close_or_lifecycle_review(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        portfolio_router_module,
        "build_position_lifecycle_analysis",
        lambda _db, *, user_id, position_group_id: _lifecycle_payload(position_group_id),
    )
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-missing-nonblocking",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    close_resp = portfolio_db_client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 950.0,
        "exit_quantity": 100,
    })
    lifecycle_resp = portfolio_db_client.post("/portfolio/groups/group-missing-nonblocking/lifecycle-review")

    assert close_resp.status_code == 200
    assert lifecycle_resp.status_code == 200
    assert portfolio_db_session.execute(select(PositionLifecyclePlan)).scalars().all() == []


def test_lifecycle_plan_endpoint_exposes_original_add_entry_condition_without_changing_list_shape(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-condition",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-add-condition",
        symbol="2330.TW",
        source_portfolio_id=42,
        add_entry_condition="no_averaging_down",
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.commit()

    list_resp = portfolio_db_client.get("/portfolio")
    plan_resp = portfolio_db_client.get("/portfolio/42/lifecycle-plan")

    assert list_resp.status_code == 200
    assert set(list_resp.json()[0]) == {"id", "symbol", "name", "entry_price", "quantity", "entry_date", "notes"}
    assert plan_resp.status_code == 200
    assert plan_resp.json() == {
        "portfolio_id": 42,
        "position_group_id": "group-add-condition",
        "symbol": "2330.TW",
        "thesis": None,
        "setup_type": None,
        "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": "no_averaging_down",
        "planned_invalidation": None,
        "planned_stop_price": None,
        "planned_target_or_scale_out_rule": None,
        "planned_risk_amount": None,
        "planned_risk_pct": None,
        "position_sizing_rationale": None,
        "source": "user_recorded_at_event_time",
        "created_after_entry": False,
    }


def test_lifecycle_plan_endpoint_returns_null_add_entry_condition_when_plan_missing(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-no-plan",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/42/lifecycle-plan")

    assert resp.status_code == 200
    assert resp.json()["add_entry_condition"] is None


def test_position_group_events_returns_owned_timeline_in_stable_chronological_order(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(User(id=2, google_sub="user-2", email="other@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-events",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(UserPortfolio(
        id=99,
        user_id=2,
        position_group_id="group-events",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add_all([
        PositionEvent(
            id=30,
            user_id=1,
            position_group_id="group-events",
            symbol="2330.TW",
            event_type="manual_adjustment",
            event_date=date(2026, 1, 1),
            price=905.125,
            quantity=5,
            fees=1.5,
            taxes=0.25,
            source_portfolio_id=42,
            note="tie-break",
            reason_category="record_correction",
            reason_code="manual_record_correction",
            plan_adherence="partial",
            confidence_level="medium",
            source="manual_record_correction",
            data_quality_note="manual note",
            created_at=datetime(2026, 1, 1, 9, 0, 0),
            updated_at=datetime(2026, 1, 1, 9, 5, 0),
        ),
        PositionEvent(
            id=10,
            user_id=1,
            position_group_id="group-events",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=100,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 9, 0, 0),
            updated_at=datetime(2026, 1, 1, 9, 0, 0),
        ),
        PositionEvent(
            id=20,
            user_id=1,
            position_group_id="group-events",
            symbol="2330.TW",
            event_type="add_entry",
            event_date=date(2026, 1, 1),
            price=910,
            quantity=20,
            fees=2,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 10, 0, 0),
            updated_at=datetime(2026, 1, 1, 10, 0, 0),
        ),
        PositionEvent(
            id=40,
            user_id=1,
            position_group_id="group-events",
            symbol="2330.TW",
            event_type="partial_exit",
            event_date=date(2026, 1, 2),
            price=950,
            quantity=50,
            fees=10,
            taxes=5,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 2, 9, 0, 0),
            updated_at=datetime(2026, 1, 2, 9, 0, 0),
        ),
        PositionEvent(
            id=50,
            user_id=2,
            position_group_id="group-events",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=999,
            quantity=1,
            fees=0,
            taxes=0,
            source_portfolio_id=99,
            note="other-user-secret",
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 8, 0, 0),
            updated_at=datetime(2026, 1, 1, 8, 0, 0),
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/groups/group-events/events")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"position_group_id", "symbol", "events"}
    assert data["position_group_id"] == "group-events"
    assert data["symbol"] == "2330.TW"
    assert [event["id"] for event in data["events"]] == [10, 30, 20, 40]
    assert "other-user-secret" not in resp.text
    assert set(data["events"][0]) == {
        "id", "position_group_id", "symbol", "event_type", "event_date", "price", "quantity", "fees", "taxes",
        "source_portfolio_id", "note", "reason_category", "reason_code", "plan_adherence", "confidence_level", "source",
        "data_quality_note", "created_at", "updated_at",
    }
    assert data["events"][1] == {
        "id": 30,
        "position_group_id": "group-events",
        "symbol": "2330.TW",
        "event_type": "manual_adjustment",
        "event_date": "2026-01-01",
        "price": 905.125,
        "quantity": 5,
        "fees": 1.5,
        "taxes": 0.25,
        "source_portfolio_id": 42,
        "note": "tie-break",
        "reason_category": "record_correction",
        "reason_code": "manual_record_correction",
        "plan_adherence": "partial",
        "confidence_level": "medium",
        "source": "manual_record_correction",
        "data_quality_note": "manual note",
        "created_at": "2026-01-01T09:00:00",
        "updated_at": "2026-01-01T09:05:00",
    }


def test_position_group_events_forbids_unowned_group_without_leaking_events(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(User(id=2, google_sub="user-2", email="other@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=99,
        user_id=2,
        position_group_id="foreign-group",
        symbol="2454.TW",
        entry_price=800,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(PositionEvent(
        user_id=2,
        position_group_id="foreign-group",
        symbol="2454.TW",
        event_type="initial_entry",
        event_date=date(2026, 1, 1),
        price=800,
        quantity=100,
        fees=0,
        taxes=0,
        source_portfolio_id=99,
        note="do-not-leak",
        source="user_recorded_at_event_time",
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/groups/foreign-group/events")

    assert resp.status_code == 403
    assert "foreign-group" not in resp.text
    assert "2454.TW" not in resp.text
    assert "do-not-leak" not in resp.text


def test_add_portfolio_persists_initial_entry_event_and_response_shape(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
        "notes": "核心持股",
    })

    assert resp.status_code == 201
    assert resp.json()["symbol"] == "2330.TW"
    assert resp.json()["name"] == "台積電"
    assert resp.json()["entry_price"] == 900.0
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.event_type == "initial_entry"
    assert event.source == "user_recorded_at_event_time"
    assert event.symbol == "2330.TW"
    assert event.quantity == 100
    assert float(event.price) == 900.0
    assert float(event.fees) == 0.0
    assert float(event.taxes) == 0.0


def test_add_portfolio_allows_ninth_active_holding(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    for index in range(8):
        portfolio_db_session.add(UserPortfolio(
            user_id=1,
            symbol=f"99{index:02d}.TW",
            entry_price=100 + index,
            quantity=100,
            entry_date=date(2026, 1, 1),
        ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio", json={
        "symbol": "2454.TW",
        "entry_price": 800.0,
        "entry_date": "2026-01-01",
        "quantity": 50,
    })

    assert resp.status_code == 201
    active_rows = portfolio_db_session.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == 1,
            UserPortfolio.is_active == True,
        )
    ).scalars().all()
    assert len(active_rows) == 9


def test_add_portfolio_with_entry_record_persists_event_time_context(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
        "notes": "legacy note",
        "entry_record": {
            "entry_reason": "breakout_confirmation",
            "planned_holding_period": "swing",
            "default_stop_rule": "break_ma20",
            "add_entry_condition": "pullback_holds_ma20",
            "note": "event-time note",
        },
    })

    assert resp.status_code == 201
    assert resp.json()["symbol"] == "2330.TW"
    assert resp.json()["name"] == "台積電"
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.event_type == "initial_entry"
    assert event.reason_code == "breakout_confirmation"
    assert event.reason_category == "technical"
    assert event.source == "user_recorded_at_event_time"
    assert event.note == "event-time note"
    plan = portfolio_db_session.execute(select(PositionLifecyclePlan)).scalar_one()
    assert plan.planned_holding_period == "swing"
    assert plan.default_stop_rule == "break_ma20"
    assert plan.add_entry_condition == "pullback_holds_ma20"
    assert plan.source == "user_recorded_at_event_time"
    assert plan.created_after_entry is False


def test_add_portfolio_without_entry_record_does_not_create_lifecycle_plan_or_intent_defaults(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
        "notes": "legacy note",
    })

    assert resp.status_code == 201
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.note == "legacy note"
    assert event.reason_category is None
    assert event.reason_code is None
    assert portfolio_db_session.execute(select(PositionLifecyclePlan)).scalars().all() == []


def test_add_portfolio_entry_record_not_recorded_preserves_explicit_not_recorded_category(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
        "entry_record": {
            "entry_reason": "not_recorded",
            "note": "User mentioned breakout in free text only.",
        },
    })

    assert resp.status_code == 201
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.reason_category == "not_recorded"
    assert event.reason_code is None


def test_add_portfolio_entry_record_note_does_not_infer_fixed_options(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
        "entry_record": {
            "planned_holding_period": "medium_term",
            "note": "Breakout above prior high, stop below MA20, add on pullback.",
        },
    })

    assert resp.status_code == 201
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.note == "Breakout above prior high, stop below MA20, add on pullback."
    assert event.reason_category is None
    assert event.reason_code is None
    plan = portfolio_db_session.execute(select(PositionLifecyclePlan)).scalar_one()
    assert plan.planned_holding_period == "medium_term"
    assert plan.default_stop_rule is None
    assert plan.add_entry_condition is None


def test_add_portfolio_rejects_invalid_entry_record_fixed_option(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
        "entry_record": {"default_stop_rule": "trailing_stop"},
    })

    assert resp.status_code == 422
    assert portfolio_db_session.execute(select(UserPortfolio)).scalars().all() == []
    assert portfolio_db_session.execute(select(PositionEvent)).scalars().all() == []
    assert portfolio_db_session.execute(select(PositionLifecyclePlan)).scalars().all() == []


def test_close_without_manual_costs_calculates_row_event_costs_and_pnl(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-tax-default",
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
    data = resp.json()
    assert data["exit_fees"] == 135.38
    assert data["exit_taxes"] == 285.0
    assert data["realized_pnl"] == 4579.62
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    assert event.event_type == "full_exit"
    assert float(event.fees) == 135.38
    assert float(event.taxes) == 285.0


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


def _add_raw_rows(
    session: Session,
    *,
    symbol: str = "2330.TW",
    start: date = date(2026, 1, 1),
    closes: list[float] | None = None,
) -> None:
    for offset, close in enumerate(closes or [900, 930, 880, 960, 950]):
        record_date = start.toordinal() + offset
        date_value = date.fromordinal(record_date)
        session.add(StockRawData(
            symbol=symbol,
            record_date=date_value,
            technical={
                "ohlcv": {
                    "open": close,
                    "high": close + 5,
                    "low": close - 5,
                    "close": close,
                    "volume": 1000 + offset,
                    "avg_volume_20": 1000,
                },
                "indicators": {},
                "data_dates": {"ohlcv": date_value.isoformat()},
            },
            raw_data_is_final=True,
        ))
    session.commit()


def _add_snapshot_raw_rows(
    session: Session,
    *,
    symbol: str = "2330.TW",
) -> None:
    for record_date, closes in [
        (date(2026, 1, 1), list(range(1, 65))),
        (date(2026, 1, 11), list(range(1, 65)) + [950]),
    ]:
        session.add(StockRawData(
            symbol=symbol,
            record_date=record_date,
            technical={
                "current_price": closes[-1],
                "recent_closes": closes,
                "recent_highs": [close + 5 for close in closes],
                "recent_lows": [close - 5 for close in closes],
                "recent_volumes": [1000 + offset for offset, _ in enumerate(closes)],
            },
            raw_data_is_final=True,
        ))
    session.commit()


def _add_lifecycle_group(
    session: Session,
    *,
    user_id: int = 1,
    position_group_id: str = "group-life-review",
    symbol: str = "2330.TW",
) -> UserPortfolio:
    item = UserPortfolio(
        id=77,
        user_id=user_id,
        position_group_id=position_group_id,
        symbol=symbol,
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    )
    session.add(item)
    session.add(PositionEvent(
        user_id=user_id,
        position_group_id=position_group_id,
        symbol=symbol,
        event_type="initial_entry",
        event_date=date(2026, 1, 1),
        price=900,
        quantity=100,
        fees=0,
        taxes=0,
        source_portfolio_id=77,
        source="user_recorded_at_event_time",
    ))
    session.commit()
    return item


def _lifecycle_payload(position_group_id: str = "group-life-review") -> tuple[dict, dict]:
    return (
        {
            "position_group_id": position_group_id,
            "symbol": "2330.TW",
            "lifecycle_review": {"classification": {"tier": "constructive"}},
            "data_quality": {"status": "ok"},
        },
        {
            "position_group_id": position_group_id,
            "symbol": "2330.TW",
            "metrics": {"lifecycle": {}},
            "events": [{"event_type": "initial_entry"}],
            "data_quality": {"status": "ok"},
        },
    )


def test_create_position_lifecycle_review_first_post_saves_result_and_evidence_payload(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []

    def fake_builder(db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        calls.append((db, user_id, position_group_id))
        return _lifecycle_payload(position_group_id)

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fake_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)

    resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 200
    data = resp.json()
    assert "portfolio_id" not in data
    assert data["user_id"] == 1
    assert data["position_group_id"] == "group-life-review"
    assert data["symbol"] == "2330.TW"
    assert data["review_version"] == "position-lifecycle-review-v1"
    assert data["llm_summary"] is None
    assert data["review_result"]["lifecycle_review"]["classification"]["tier"] == "constructive"
    assert data["evidence_payload"]["events"] == [{"event_type": "initial_entry"}]
    assert calls == [(portfolio_db_session, 1, "group-life-review")]
    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].review_result == data["review_result"]
    assert reviews[0].evidence_payload == data["evidence_payload"]


def test_position_lifecycle_review_excludes_future_shared_context(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["lifecycle_review"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 2, 1),
        freshness="fresh",
        payload={"major_holder_ratio": 0.72},
        missing_reason=None,
    )
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 200
    data = resp.json()
    shared_context = data["evidence_payload"]["shared_context"]
    event_context = shared_context["events"][0]["shared_context"]
    weekly_context = next(
        context
        for context in event_context["contexts"]
        if context["context_type"] == "weekly_major_holders"
    )
    assert weekly_context["missing_reason"] == "future_context_excluded"
    assert weekly_context["payload"] == {}
    assert weekly_context["source"]["excluded_as_of_date"] == "2026-02-01"
    caveats = data["review_result"]["lifecycle_review"]["classification"]["caveats"]
    assert any("未來資料" in item["text"] for item in caveats)
    assert data["review_result"]["lifecycle_review"]["classification"]["primary_label"] != "future_context_excluded"


def test_position_lifecycle_review_uses_historical_shared_context_before_future_context(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["lifecycle_review"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2025, 12, 31),
        freshness="fresh",
        payload={"major_holder_ratio": 0.57},
        missing_reason=None,
    )
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["lifecycle_review"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 2, 1),
        freshness="fresh",
        payload={"major_holder_ratio": 0.72},
        missing_reason=None,
    )
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 200
    data = resp.json()
    event_context = data["evidence_payload"]["shared_context"]["events"][0]["shared_context"]
    weekly_context = next(
        context
        for context in event_context["contexts"]
        if context["context_type"] == "weekly_major_holders"
    )
    assert weekly_context["as_of_date"] == "2025-12-31"
    assert weekly_context["payload"] == {"major_holder_ratio": 0.57}
    assert weekly_context["missing_reason"] is None


def test_position_lifecycle_review_missing_shared_context_is_nonblocking(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)

    resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 200
    data = resp.json()
    shared_context = data["review_result"]["shared_context"]
    assert shared_context["consumer"] == "lifecycle_review"
    assert shared_context["data_quality"]["blocking"] is False
    assert "context_cache_missing" in shared_context["data_quality"]["missing_reasons"]
    assert data["review_version"] == "position-lifecycle-review-v1"


def test_get_position_lifecycle_review_returns_existing_review(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        portfolio_router_module,
        "build_position_lifecycle_analysis",
        lambda _db, *, user_id, position_group_id: _lifecycle_payload(position_group_id),
    )
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    created = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review").json()

    resp = portfolio_db_client.get("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 200
    assert resp.json() == created


def test_get_position_lifecycle_review_missing_owned_group_returns_404(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)

    resp = portfolio_db_client.get("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 404
    assert portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all() == []


def test_create_position_lifecycle_review_existing_review_skips_recompute_and_duplicate(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_builder(_db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        raise AssertionError("existing lifecycle review must not be recomputed")

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fail_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    event.updated_at = datetime(2026, 1, 1, 9, 0, 0)
    portfolio_db_session.add(PositionLifecycleReview(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v1",
        review_result={"existing": True},
        evidence_payload={"existing": True},
        llm_summary=None,
        created_at=datetime(2026, 1, 1, 10, 0, 0),
        updated_at=datetime(2026, 1, 1, 10, 0, 0),
    ))
    portfolio_db_session.commit()

    first = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")
    second = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["review_result"] == {"existing": True}
    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert len(reviews) == 1


def test_create_position_lifecycle_review_recomputes_stale_existing_review_after_later_event_update(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []

    def fake_builder(_db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        calls.append((user_id, position_group_id))
        return (
            {"rebuilt": "event", "position_group_id": position_group_id},
            {"source": "event", "events": [{"event_type": "full_exit"}]},
        )

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fake_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    event.updated_at = datetime(2026, 1, 1, 10, 0, 0)
    portfolio_db_session.add(PositionLifecycleReview(
        id=7,
        user_id=1,
        position_group_id="group-life-review",
        symbol="OLD.TW",
        review_version="position-lifecycle-review-v1",
        review_result={"existing": True},
        evidence_payload={"existing": True},
        llm_summary="old summary",
        created_at=datetime(2026, 1, 1, 8, 0, 0),
        updated_at=datetime(2026, 1, 1, 9, 0, 0),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 200
    data = resp.json()
    assert calls == [(1, "group-life-review")]
    assert data["id"] == 7
    assert data["symbol"] == "2330.TW"
    assert data["review_result"] == {"rebuilt": "event", "position_group_id": "group-life-review"}
    assert data["evidence_payload"] == {"source": "event", "events": [{"event_type": "full_exit"}]}
    assert data["llm_summary"] is None
    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].id == 7
    assert reviews[0].review_result == data["review_result"]
    assert reviews[0].evidence_payload == data["evidence_payload"]


def test_create_position_lifecycle_review_recomputes_stale_existing_review_after_later_plan_update(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []

    def fake_builder(_db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        calls.append((user_id, position_group_id))
        return (
            {"rebuilt": "plan", "position_group_id": position_group_id},
            {"source": "plan", "plan": {"planned_holding_period": "long_term"}},
        )

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fake_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    event = portfolio_db_session.execute(select(PositionEvent)).scalar_one()
    event.updated_at = datetime(2026, 1, 1, 8, 30, 0)
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        source_portfolio_id=77,
        planned_holding_period="long_term",
        source="user_backfilled",
        created_after_entry=True,
        created_at=datetime(2026, 1, 1, 8, 0, 0),
        updated_at=datetime(2026, 1, 1, 10, 0, 0),
    ))
    portfolio_db_session.add(PositionLifecycleReview(
        id=8,
        user_id=1,
        position_group_id="group-life-review",
        symbol="OLD.TW",
        review_version="position-lifecycle-review-v1",
        review_result={"existing": True},
        evidence_payload={"existing": True},
        llm_summary="old summary",
        created_at=datetime(2026, 1, 1, 8, 0, 0),
        updated_at=datetime(2026, 1, 1, 9, 0, 0),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 200
    data = resp.json()
    assert calls == [(1, "group-life-review")]
    assert data["id"] == 8
    assert data["symbol"] == "2330.TW"
    assert data["review_result"] == {"rebuilt": "plan", "position_group_id": "group-life-review"}
    assert data["evidence_payload"] == {"source": "plan", "plan": {"planned_holding_period": "long_term"}}
    assert data["llm_summary"] is None
    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].id == 8
    assert reviews[0].review_result == data["review_result"]
    assert reviews[0].evidence_payload == data["evidence_payload"]


def test_position_lifecycle_review_forbids_unowned_group_without_building(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_builder(_db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        raise AssertionError("forbidden lifecycle review must not build")

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fail_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(User(id=2, google_sub="user-2", email="other@example.com"))
    _add_lifecycle_group(portfolio_db_session, user_id=2, position_group_id="foreign-life-group", symbol="2454.TW")

    get_resp = portfolio_db_client.get("/portfolio/groups/foreign-life-group/lifecycle-review")
    post_resp = portfolio_db_client.post("/portfolio/groups/foreign-life-group/lifecycle-review")

    assert get_resp.status_code == 403
    assert post_resp.status_code == 403
    assert portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all() == []


def test_create_position_lifecycle_review_builder_failure_rolls_back_without_partial_review(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_builder(_db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        raise RuntimeError("builder failed")

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fail_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)

    with pytest.raises(RuntimeError, match="builder failed"):
        portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all() == []


def test_create_position_lifecycle_review_commit_failure_rolls_back_pending_review(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    rollback_calls = []
    original_rollback = portfolio_db_session.rollback

    def fail_commit() -> None:
        raise RuntimeError("commit failed")

    def spy_rollback() -> None:
        rollback_calls.append(True)
        original_rollback()

    monkeypatch.setattr(
        portfolio_router_module,
        "build_position_lifecycle_analysis",
        lambda _db, *, user_id, position_group_id: _lifecycle_payload(position_group_id),
    )
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    monkeypatch.setattr(portfolio_db_session, "commit", fail_commit)
    monkeypatch.setattr(portfolio_db_session, "rollback", spy_rollback)

    with pytest.raises(RuntimeError, match="commit failed"):
        portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert rollback_calls == [True]
    assert portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all() == []


def test_position_lifecycle_review_unique_owner_group_version_allows_only_one_current_version(
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    portfolio_db_session.add(PositionLifecycleReview(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v1",
        review_result={"first": True},
        evidence_payload={"first": True},
    ))
    portfolio_db_session.commit()
    portfolio_db_session.add(PositionLifecycleReview(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v1",
        review_result={"second": True},
        evidence_payload={"second": True},
    ))

    with pytest.raises(IntegrityError):
        portfolio_db_session.commit()
    portfolio_db_session.rollback()

    portfolio_db_session.add(PositionLifecycleReview(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v2",
        review_result={"second_version": True},
        evidence_payload={"second_version": True},
    ))
    portfolio_db_session.commit()

    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert len(reviews) == 2


def test_position_lifecycle_review_does_not_change_single_trade_review_behavior(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        portfolio_router_module,
        "build_position_lifecycle_analysis",
        lambda _db, *, user_id, position_group_id: _lifecycle_payload(position_group_id),
    )
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = _add_closed_portfolio(portfolio_db_session, position_group_id="group-compatible-review")
    portfolio_db_session.add(PositionEvent(
        user_id=1,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        event_type="initial_entry",
        event_date=item.entry_date,
        price=item.entry_price,
        quantity=item.quantity,
        fees=0,
        taxes=0,
        source_portfolio_id=item.id,
        source="user_recorded_at_event_time",
    ))
    portfolio_db_session.commit()
    _add_raw_rows(portfolio_db_session)

    lifecycle_resp = portfolio_db_client.post("/portfolio/groups/group-compatible-review/lifecycle-review")
    trade_resp = portfolio_db_client.post("/portfolio/42/review")

    assert lifecycle_resp.status_code == 200
    assert trade_resp.status_code == 200
    assert lifecycle_resp.json()["review_version"] == "position-lifecycle-review-v1"
    assert trade_resp.json()["review_version"] == "trade-review-v1"
    assert trade_resp.json()["portfolio_id"] == 42
    assert trade_resp.json()["review_result"]["operation_review"]["scope"] == "current_closed_row_only"
    assert len(portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()) == 1
    assert len(portfolio_db_session.execute(select(TradeReview)).scalars().all()) == 1


def test_create_trade_review_first_post_saves_real_trade_result_and_evidence_payload(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)

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
        "data_quality", "trade_result", "entry_review", "holding_review", "exit_review", "operation_review", "user_readable_conclusion",
    }
    assert data["review_result"]["entry_review"]["classification"] in {
        "breakout_entry", "pullback_entry", "chase_entry", "weak_entry", "range_entry", "insufficient_data",
    }
    assert set(data["review_result"]["entry_review"]) >= {
        "classification", "confidence", "market_regime", "supporting_signals", "conflicting_signals", "caveats",
    }
    assert data["review_result"]["holding_review"]["detected_events"] == data["evidence_payload"]["detected_events"]
    assert data["review_result"]["operation_review"]["scope"] == "current_closed_row_only"
    assert "market_regime" in data["review_result"]["operation_review"]
    assert set(data["review_result"]["user_readable_conclusion"]) == {
        "overall_verdict", "overall_verdict_label", "one_sentence_reason", "evidence", "next_time_rules",
    }
    assert data["review_result"]["user_readable_conclusion"]["overall_verdict"] in {
        "early", "reasonable", "late", "insufficient",
    }
    assert data["review_result"]["trade_result"]["realized_return_pct"] == pytest.approx(5.5556)
    assert data["review_result"]["trade_result"]["entry_date"] == "2026-01-01"
    assert data["review_result"]["trade_result"]["exit_date"] == "2026-01-11"
    assert data["review_result"]["trade_result"]["realized_pnl"] == 5000
    assert data["review_result"]["trade_result"]["max_profit_pct"] == pytest.approx(6.6667)
    assert data["review_result"]["trade_result"]["max_drawdown_pct"] == pytest.approx(-2.2222)
    assert data["review_result"]["trade_result"]["profit_giveback_pct"] == pytest.approx(1.1111)
    assert set(data["evidence_payload"]) == {
        "trade", "position_group_id", "path_metrics", "entry_indicators", "exit_indicators", "detected_events", "data_quality", "source_data",
    }
    assert data["evidence_payload"]["position_group_id"] == "group-review"
    assert data["evidence_payload"]["trade"]["position_group_id"] == "group-review"
    assert data["evidence_payload"]["trade"]["return_pct"] == pytest.approx(5.5556)
    assert data["evidence_payload"]["path_metrics"]["highest_close_during_holding"] == 960
    reviews = portfolio_db_session.execute(select(TradeReview)).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].review_result == data["review_result"]
    assert reviews[0].evidence_payload == data["evidence_payload"]


def test_create_trade_review_accepts_snapshot_raw_data_without_ohlcv_and_persists_once(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_snapshot_raw_rows(portfolio_db_session)

    first = portfolio_db_client.post("/portfolio/42/review")
    second = portfolio_db_client.post("/portfolio/42/review")

    assert first.status_code == 200
    assert second.status_code == 200
    data = first.json()
    assert second.json() == data
    assert data["review_version"] == "trade-review-v1"
    assert data["review_result"]["trade_result"]["entry_indicators"]["ma20"] is not None
    assert data["review_result"]["trade_result"]["exit_indicators"]["ma20"] is not None
    assert data["review_result"]["data_quality"]["status"] == "ok"
    reviews = portfolio_db_session.execute(select(TradeReview)).scalars().all()
    assert len(reviews) == 1


def test_create_trade_review_calls_market_data_ensure_before_first_save(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []

    def spy_ensure(_db: Session, item: UserPortfolio) -> None:
        calls.append(item.id)

    monkeypatch.setattr(portfolio_router_module, "ensure_trade_review_market_data", spy_ensure)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert calls == [42]


def test_create_trade_review_existing_review_skips_market_data_ensure(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_ensure(_db: Session, _item: UserPortfolio) -> None:
        raise AssertionError("existing review must not trigger market data ensure")

    monkeypatch.setattr(portfolio_router_module, "ensure_trade_review_market_data", fail_ensure)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = _add_closed_portfolio(portfolio_db_session)
    portfolio_db_session.add(TradeReview(
        portfolio_id=item.id,
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        review_version="trade-review-v1",
        review_result={"existing": True},
        evidence_payload={"existing": True},
        llm_summary=None,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert resp.json()["review_result"] == {"existing": True}


def test_create_trade_review_second_post_returns_existing_without_duplicate(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)

    first = portfolio_db_client.post("/portfolio/42/review")
    _add_raw_rows(portfolio_db_session)
    second = portfolio_db_client.post("/portfolio/42/review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.json()["review_result"]["trade_result"]["max_profit_pct"] is None
    reviews = portfolio_db_session.execute(select(TradeReview)).scalars().all()
    assert len(reviews) == 1


def test_create_trade_review_partial_close_uses_closed_slice_not_same_group_batch(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-partial-review",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()
    _add_raw_rows(portfolio_db_session)

    close_resp = portfolio_db_client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-05",
        "exit_price": 950.0,
        "exit_quantity": 40,
    })
    assert close_resp.status_code == 200
    closed_id = close_resp.json()["id"]
    portfolio_db_session.add(UserPortfolio(
        id=99,
        user_id=1,
        position_group_id="group-partial-review",
        symbol="2330.TW",
        entry_price=900,
        quantity=60,
        entry_date=date(2026, 1, 1),
        is_active=False,
        exit_date=date(2026, 1, 11),
        exit_price=1000,
        exit_quantity=60,
        realized_pnl=6000,
        realized_return_pct=11.1111,
        holding_days=10,
    ))
    portfolio_db_session.commit()

    review_resp = portfolio_db_client.post(f"/portfolio/{closed_id}/review")

    assert review_resp.status_code == 200
    evidence = review_resp.json()["evidence_payload"]
    assert evidence["trade"]["id"] == closed_id
    assert evidence["trade"]["quantity"] == 40
    assert evidence["trade"]["exit_quantity"] == 40
    assert evidence["trade"]["position_group_id"] == "group-partial-review"
    assert review_resp.json()["review_result"]["operation_review"]["reviewed_portfolio_id"] == closed_id
    assert evidence["path_metrics"]["highest_close_during_holding"] == 960
    rows = portfolio_db_session.execute(
        select(UserPortfolio).where(UserPortfolio.position_group_id == "group-partial-review")
    ).scalars().all()
    assert len(rows) == 3
    assert {row.is_active for row in rows} == {True, False}


def test_partial_close_group_timeline_and_single_trade_review_remain_usable(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-partial-timeline",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()
    _add_raw_rows(portfolio_db_session)

    close_resp = portfolio_db_client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-05",
        "exit_price": 950.0,
        "exit_quantity": 40,
    })
    assert close_resp.status_code == 200
    closed_id = close_resp.json()["id"]

    timeline_resp = portfolio_db_client.get("/portfolio/groups/group-partial-timeline/events")
    review_resp = portfolio_db_client.post(f"/portfolio/{closed_id}/review")

    assert timeline_resp.status_code == 200
    events = timeline_resp.json()["events"]
    assert [event["event_type"] for event in events] == ["partial_exit"]
    assert events[0]["source_portfolio_id"] == closed_id
    assert events[0]["quantity"] == 40
    assert review_resp.status_code == 200
    assert review_resp.json()["portfolio_id"] == closed_id
    assert review_resp.json()["review_result"]["operation_review"]["scope"] == "current_closed_row_only"


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


def test_latest_history_returns_additive_risk_language_fields():
    """latest-history 應優先回傳 additive risk-language 欄位，legacy action 僅保留相容。"""
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
        "action_tag": "Hold",
        "recommended_action": "Exit",
        "indicators": {
            "position_risk_language": {
                "risk_state": "watch",
                "risk_state_label": "需要觀察",
                "discipline_triggers": ["量能失真需等待確認"],
                "risk_control_reference": {"reference_price": 900},
            },
        },
        "final_verdict": None,
        "prev_action_tag": "Hold",
        "prev_confidence": 60.0,
    }

    client = _make_latest_history_client([portfolio], [log_row])
    resp = client.get("/portfolio/latest-history")

    assert resp.status_code == 200
    record = resp.json()["42"]
    assert record["risk_state"] == "watch"
    assert record["risk_state_label"] == "需要觀察"
    assert record["discipline_triggers"] == ["量能失真需等待確認"]
    assert record["risk_control_reference"] == {"reference_price": 900}
    assert record["compatibility_source"] == "position_risk_language"
    assert record["recommended_action"] == "Exit"
