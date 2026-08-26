# backend/tests/test_portfolio_router.py
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread
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
from ai_stock_sentinel.analysis.review_sources import attach_source_fingerprint, market_snapshot_payload
from ai_stock_sentinel.portfolio.application import get_risk_summary as portfolio_risk_summary_app
from ai_stock_sentinel.portfolio.application import refresh_prices as refresh_prices_module
from ai_stock_sentinel.portfolio.application.refresh_prices import _fetch_quotes, _quote_payload
from ai_stock_sentinel.portfolio import router as portfolio_router_module
from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.daily_radar.repository import upsert_shared_background_context
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarRun,
    Phase1AvwapSnapshot,
    PositionEvent,
    PositionLifecyclePlan,
    PositionLifecycleReview,
    SharedBackgroundContext,
    StockRawData,
    TradeReview,
    UserPortfolio,
    UserWatchlist,
)
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.phase1_avwap.provider import DEFAULT_PHASE1_DATASET, TWSE_STOCK_DAY_DATASET
from ai_stock_sentinel.models import StockSnapshot
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


def test_add_portfolio_rejects_non_taiwan_symbol(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        portfolio_router_module,
        "check_symbol_exists",
        lambda _symbol: pytest.fail("unsupported market must fail before provider validation"),
    )
    client = _make_client()

    resp = client.post("/portfolio", json={
        "symbol": "AAPL",
        "entry_price": 200.0,
        "entry_date": "2026-01-01",
        "quantity": 10,
    })

    assert resp.status_code == 422
    assert "目前僅支援台灣上市" in resp.json()["detail"][0]["msg"]


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


def test_add_portfolio_rejects_non_finite_entry_price(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
    client = _make_client()

    resp = client.post(
        "/portfolio",
        content=(
            '{"symbol":"2330.TW","entry_price":1e309,'
            '"entry_date":"2026-01-01","quantity":100}'
        ),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 422


def test_add_portfolio_rejects_quantity_above_postgresql_integer_max():
    client = _make_client()

    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 2_147_483_648,
    })

    assert resp.status_code == 422


@pytest.mark.parametrize("entry_price", [0.001, 900.001, 100_000_000])
def test_add_portfolio_rejects_entry_price_outside_postgresql_numeric_range(
    entry_price,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(portfolio_router_module, "check_symbol_exists", lambda _symbol: True)
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


def test_update_portfolio_backfills_and_updates_initial_entry_event(
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
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "initial_entry")
    ).scalar_one()
    assert event.event_type == "initial_entry"
    assert event.source == "manual_record_correction"
    assert event.data_quality_note == (
        "Entry price, quantity, or date was manually corrected after initial recording."
    )
    assert event.event_date == date(2026, 2, 1)
    assert float(event.price) == 950.0
    assert event.quantity == 200


def test_update_portfolio_marks_event_time_entry_as_manual_correction(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-update-recorded-event",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    )
    portfolio_db_session.add(item)
    portfolio_db_session.flush()
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
        reason_category="fundamental",
        reason_code="value_revaluation",
        source_portfolio_id=item.id,
        source="user_recorded_at_event_time",
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42", json={
        "entry_price": 950.0,
        "quantity": 120,
        "entry_date": "2026-01-02",
        "notes": "corrected facts",
    })

    assert resp.status_code == 200
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "initial_entry")
    ).scalar_one()
    assert event.source == "manual_record_correction"
    assert event.data_quality_note == (
        "Entry price, quantity, or date was manually corrected after initial recording."
    )
    assert event.reason_category == "fundamental"
    assert event.reason_code == "value_revaluation"


def test_update_portfolio_rejects_closed_row_economic_mutation(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-update-closed",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
        is_active=False,
        exit_date=date(2026, 1, 10),
        exit_price=950,
        exit_quantity=100,
        realized_pnl=5000,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42", json={
        "entry_price": 950.0,
        "quantity": 200,
        "entry_date": "2026-02-01",
        "notes": "must use explicit correction flow",
    })

    assert resp.status_code == 409
    row = portfolio_db_session.get(UserPortfolio, 42)
    assert float(row.entry_price) == 900.0
    assert row.quantity == 100
    assert float(row.realized_pnl) == 5000.0


def test_update_portfolio_rejects_economic_mutation_after_lifecycle_started(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-update-started",
        symbol="2330.TW",
        entry_price=900,
        quantity=150,
        entry_date=date(2026, 1, 1),
    )
    portfolio_db_session.add(item)
    portfolio_db_session.flush()
    portfolio_db_session.add_all([
        PositionEvent(
            user_id=1,
            position_group_id=item.position_group_id,
            symbol=item.symbol,
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=100,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ),
        PositionEvent(
            user_id=1,
            position_group_id=item.position_group_id,
            symbol=item.symbol,
            event_type="add_entry",
            event_date=date(2026, 1, 5),
            price=950,
            quantity=50,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42", json={
        "entry_price": 920.0,
        "quantity": 160,
        "entry_date": "2026-01-02",
        "notes": "unsafe rewrite",
    })

    assert resp.status_code == 409


def test_update_portfolio_rejects_unsafe_legacy_group_backfill(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add_all([
        UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-update-legacy-split",
            symbol="2330.TW",
            entry_price=900,
            quantity=60,
            entry_date=date(2026, 1, 1),
            is_active=True,
        ),
        UserPortfolio(
            id=43,
            user_id=1,
            position_group_id="group-update-legacy-split",
            symbol="2330.TW",
            entry_price=900,
            quantity=40,
            entry_date=date(2026, 1, 1),
            is_active=False,
            exit_date=date(2026, 1, 5),
            exit_price=950,
            exit_quantity=40,
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42", json={
        "entry_price": 920.0,
        "quantity": 70,
        "entry_date": "2026-01-02",
        "notes": "unsafe correction",
    })

    assert resp.status_code == 409
    assert resp.json()["detail"] == "舊部位群組缺少事件帳本且已有分批紀錄，無法安全自動補帳"
    assert portfolio_db_session.execute(select(PositionEvent)).scalars().all() == []
    row = portfolio_db_session.get(UserPortfolio, 42)
    assert float(row.entry_price) == 900.0
    assert row.quantity == 60
    assert row.entry_date == date(2026, 1, 1)


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
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "add_entry")
    ).scalar_one()
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


def test_add_entry_endpoint_rejects_aggregate_quantity_over_postgresql_integer_max(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-entry-overflow",
        symbol="2330.TW",
        entry_price=900,
        quantity=1,
        entry_date=date(2026, 1, 1),
    )
    portfolio_db_session.add(item)
    portfolio_db_session.flush()
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

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-10",
        "price": 1000.0,
        "quantity": 2_147_483_647,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
    })

    assert resp.status_code == 422
    assert resp.json()["detail"] == "加碼後持有股數超過系統上限"
    assert portfolio_db_session.get(UserPortfolio, 42).quantity == 1
    assert len(portfolio_db_session.execute(select(PositionEvent)).scalars().all()) == 1


def test_add_entry_endpoint_rejects_computed_fee_over_postgresql_numeric_range(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-entry-money-overflow",
        symbol="2330.TW",
        entry_price=900,
        quantity=1,
        entry_date=date(2026, 1, 1),
    )
    portfolio_db_session.add(item)
    portfolio_db_session.flush()
    portfolio_db_session.add(PositionEvent(
        user_id=1,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        event_type="initial_entry",
        event_date=item.entry_date,
        price=item.entry_price,
        quantity=1,
        fees=0,
        taxes=0,
        source_portfolio_id=item.id,
        source="user_recorded_at_event_time",
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-10",
        "price": 900,
        "quantity": 2_147_483_646,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
    })

    assert resp.status_code == 422
    assert resp.json()["detail"] == "加碼金額超過系統可儲存範圍"
    portfolio_db_session.expire_all()
    assert portfolio_db_session.get(UserPortfolio, 42).quantity == 1
    assert len(portfolio_db_session.execute(select(PositionEvent)).scalars().all()) == 1


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
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "add_entry")
    ).scalar_one()
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


def test_add_entry_endpoint_rejects_event_before_latest_ledger_event(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-add-entry-order",
        symbol="2330.TW",
        entry_price=900,
        quantity=150,
        entry_date=date(2026, 1, 1),
    )
    portfolio_db_session.add(item)
    portfolio_db_session.flush()
    portfolio_db_session.add_all([
        PositionEvent(
            user_id=1,
            position_group_id=item.position_group_id,
            symbol=item.symbol,
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=100,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ),
        PositionEvent(
            user_id=1,
            position_group_id=item.position_group_id,
            symbol=item.symbol,
            event_type="add_entry",
            event_date=date(2026, 1, 10),
            price=950,
            quantity=50,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-05",
        "price": 920.0,
        "quantity": 10,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
    })

    assert resp.status_code == 422
    assert resp.json()["detail"] == "事件日期不可早於目前帳本的最新事件"
    assert len(portfolio_db_session.execute(select(PositionEvent)).scalars().all()) == 2


def test_add_entry_endpoint_rejects_unsafe_legacy_group_backfill(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add_all([
        UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-legacy-split",
            symbol="2330.TW",
            entry_price=900,
            quantity=60,
            entry_date=date(2026, 1, 1),
            is_active=True,
        ),
        UserPortfolio(
            id=43,
            user_id=1,
            position_group_id="group-legacy-split",
            symbol="2330.TW",
            entry_price=900,
            quantity=40,
            entry_date=date(2026, 1, 1),
            is_active=False,
            exit_date=date(2026, 1, 5),
            exit_price=950,
            exit_quantity=40,
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-01-10",
        "price": 920.0,
        "quantity": 10,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
    })

    assert resp.status_code == 409
    assert resp.json()["detail"] == "舊部位群組缺少事件帳本且已有分批紀錄，無法安全自動補帳"
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


def test_update_portfolio_rejects_non_finite_entry_price():
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.put(
        "/portfolio/42",
        content=(
            '{"entry_price":1e309,"quantity":200,'
            '"entry_date":"2026-02-01","notes":"invalid"}'
        ),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 422


def test_update_portfolio_rejects_quantity_above_postgresql_integer_max():
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.put("/portfolio/42", json={
        "entry_price": 950.0,
        "quantity": 2_147_483_648,
        "entry_date": "2026-02-01",
    })

    assert resp.status_code == 422


@pytest.mark.parametrize("entry_price", [0.001, 900.001, 100_000_000])
def test_update_portfolio_rejects_entry_price_outside_postgresql_numeric_range(entry_price):
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.put("/portfolio/42", json={
        "entry_price": entry_price,
        "quantity": 100,
        "entry_date": "2026-02-01",
    })

    assert resp.status_code == 422


@pytest.mark.parametrize("path", [
    "/portfolio/42/close",
    "/portfolio/42/add-entry",
])
@pytest.mark.parametrize("invalid_price", [900.001, 100_000_000])
def test_lifecycle_mutations_reject_price_outside_postgresql_numeric_range(path, invalid_price):
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)
    payload = {
        "event_date": "2026-02-01",
        "price": invalid_price,
        "quantity": 1,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
    }
    if path.endswith("/close"):
        payload = {
            "exit_date": "2026-02-01",
            "exit_price": invalid_price,
            "exit_quantity": 1,
        }

    resp = client.post(path, json=payload)

    assert resp.status_code == 422


def test_close_portfolio_rejects_quantity_above_postgresql_integer_max():
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.post("/portfolio/42/close", json={
        "exit_date": "2026-02-01",
        "exit_price": 950.0,
        "exit_quantity": 2_147_483_648,
    })

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["type"] == "less_than_equal"


def test_add_entry_endpoint_rejects_quantity_above_postgresql_integer_max():
    item = _make_portfolio_item(user_id=1)
    client = _make_client_with_item(item, user_id=1)

    resp = client.post("/portfolio/42/add-entry", json={
        "event_date": "2026-02-01",
        "price": 950.0,
        "quantity": 2_147_483_648,
        "reason_code": "planned_scale_in",
        "plan_adherence": "yes",
        "confidence_level": "high",
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


def test_close_portfolio_rejects_computed_costs_over_postgresql_numeric_range(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-close-money-overflow",
        symbol="2330.TW",
        entry_price=900,
        quantity=2_147_483_647,
        entry_date=date(2026, 1, 1),
    )
    portfolio_db_session.add(item)
    portfolio_db_session.flush()
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

    resp = portfolio_db_client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-11",
        "exit_price": 900,
        "exit_quantity": 2_147_483_647,
    })

    assert resp.status_code == 422
    assert resp.json()["detail"] == "結案金額超過系統可儲存範圍"
    portfolio_db_session.expire_all()
    assert portfolio_db_session.get(UserPortfolio, 42).is_active is True
    assert len(portfolio_db_session.execute(select(PositionEvent)).scalars().all()) == 1


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


def test_close_portfolio_rejects_exit_before_latest_ledger_event(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-close-order",
        symbol="2330.TW",
        entry_price=900,
        quantity=150,
        entry_date=date(2026, 1, 1),
    )
    portfolio_db_session.add(item)
    portfolio_db_session.flush()
    portfolio_db_session.add_all([
        PositionEvent(
            user_id=1,
            position_group_id=item.position_group_id,
            symbol=item.symbol,
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=100,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ),
        PositionEvent(
            user_id=1,
            position_group_id=item.position_group_id,
            symbol=item.symbol,
            event_type="add_entry",
            event_date=date(2026, 1, 10),
            price=950,
            quantity=50,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/close", json={
        "exit_date": "2026-01-05",
        "exit_price": 940.0,
        "exit_quantity": 150,
    })

    assert resp.status_code == 422
    assert resp.json()["detail"] == "事件日期不可早於目前帳本的最新事件"
    assert portfolio_db_session.get(UserPortfolio, 42).is_active is True


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
            UserWatchlist.__table__,
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
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
        api.app.dependency_overrides.pop(portfolio_router_module.get_portfolio_quote_fetcher, None)


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
        "reason_code": "planned_scale_out",
        "plan_adherence": "yes",
        "confidence_level": "high",
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
    assert [event.event_type for event in events] == ["initial_entry", "partial_exit"]
    assert events[0].source == "user_backfilled"
    assert events[0].quantity == 100
    assert events[1].source == "user_recorded_at_event_time"
    assert events[1].source_portfolio_id == closed.id
    assert events[1].quantity == 40
    assert float(events[1].fees) == 10.0
    assert float(events[1].taxes) == 5.0
    assert events[1].reason_category == "plan_execution"
    assert events[1].reason_code == "planned_scale_out"
    assert events[1].plan_adherence == "yes"
    assert events[1].confidence_level == "high"


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
        "reason_code": "stop_loss",
        "plan_adherence": "no",
        "confidence_level": "medium",
    })

    assert resp.status_code == 200
    assert resp.json()["position_group_id"] == "group-full-close"
    row = portfolio_db_session.get(UserPortfolio, 42)
    assert row.position_group_id == "group-full-close"
    events = portfolio_db_session.execute(select(PositionEvent)).scalars().all()
    assert [event.event_type for event in events] == ["initial_entry", "full_exit"]
    assert events[0].source == "user_backfilled"
    assert events[1].source_portfolio_id == 42
    assert events[1].reason_category == "risk_control"
    assert events[1].reason_code == "stop_loss"
    assert events[1].plan_adherence == "no"
    assert events[1].confidence_level == "medium"


@pytest.mark.parametrize(
    ("decision_context", "expected_context"),
    [
        ({}, (None, None, None, None)),
        (
            {
                "reason_code": "not_recorded",
                "plan_adherence": "not_recorded",
                "confidence_level": "not_recorded",
            },
            ("not_recorded", None, "not_recorded", "not_recorded"),
        ),
    ],
)
def test_close_portfolio_distinguishes_legacy_omission_from_explicit_not_recorded(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    decision_context: dict[str, str],
    expected_context: tuple[str | None, str | None, str | None, str | None],
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-close-context",
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
        **decision_context,
    })

    assert resp.status_code == 200
    exit_event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "full_exit")
    ).scalar_one()
    assert (
        exit_event.reason_category,
        exit_event.reason_code,
        exit_event.plan_adherence,
        exit_event.confidence_level,
    ) == expected_context


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("reason_code", "price_went_down"),
        ("plan_adherence", "mostly"),
        ("confidence_level", "certain"),
    ],
)
def test_close_portfolio_rejects_invalid_decision_context_without_event(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    field: str,
    invalid_value: str,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-invalid-close-context",
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
        field: invalid_value,
    })

    assert resp.status_code == 422
    assert portfolio_db_session.get(UserPortfolio, 42).is_active is True
    assert portfolio_db_session.execute(select(PositionEvent)).scalars().all() == []


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
    monkeypatch: pytest.MonkeyPatch,
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    summary_date = date(2026, 6, 20)
    monkeypatch.setattr(portfolio_risk_summary_app, "today_taipei", lambda: summary_date)
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
        record_date=summary_date,
        technical={"close_price": 120},
        raw_data_is_final=True,
    ))
    portfolio_db_session.add(Phase1AvwapSnapshot(
        symbol="2330.TW",
        data_date=summary_date,
        dataset=DEFAULT_PHASE1_DATASET,
        adjustment_mode="unadjusted",
        source_provider="twse",
        source_granularity="daily",
        is_final=True,
        freshness="fresh",
        missing_reason=None,
        payload={
            "symbol": "2330.TW",
            "source": {
                "provider": "twse",
                "dataset": TWSE_STOCK_DAY_DATASET,
                "adjustment_mode": "unadjusted",
            },
            "ohlcv": {"close": 120},
            "bars": [
                {
                    "date": "2026-06-01",
                    "open": 115,
                    "high": 116,
                    "low": 114,
                    "close": 115,
                    "volume": 100,
                    "amount": 11500,
                    "estimated_amount": False,
                },
                {
                    "date": summary_date.isoformat(),
                    "open": 120,
                    "high": 121,
                    "low": 119,
                    "close": 120,
                    "volume": 100,
                    "amount": 11500,
                    "estimated_amount": False,
                },
            ],
            "anchors": {
                "swing_low_60d": {
                    "available": True,
                    "anchor_date": "2026-06-01",
                    "anchor_reason": "swing_low_60d",
                    "avwap": 115,
                    "distance_to_avwap_pct": 4.3478,
                    "source_granularity": "daily",
                    "estimated": False,
                },
            },
            "data_quality": {"estimated": False, "rows_used": 12},
        },
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2317.TW",
        record_date=summary_date,
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


def test_portfolio_risk_summary_projects_weekly_major_holders_with_previous_delta(
    monkeypatch: pytest.MonkeyPatch,
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    summary_date = date(2026, 6, 20)
    monkeypatch.setattr(portfolio_risk_summary_app, "today_taipei", lambda: summary_date)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-weekly-holder",
        symbol="2330.TW",
        entry_price=100,
        quantity=10,
        entry_date=date(2026, 6, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-weekly-holder",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_stop_price=95,
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2330.TW",
        record_date=summary_date,
        technical={"close_price": 120},
        raw_data_is_final=True,
    ))
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["portfolio_diagnosis"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 5, 30),
        freshness="fresh",
        payload={
            "thousand_lot_holder_ratio": 35.0,
            "large_holder_400_lot_plus_ratio": 49.0,
            "retail_100_lot_or_less_ratio": 41.0,
        },
        missing_reason=None,
    )
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["portfolio_diagnosis"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 6, 6),
        freshness="fresh",
        payload={
            "thousand_lot_holder_ratio": 36.68,
            "large_holder_400_lot_plus_ratio": 50.7,
            "retail_100_lot_or_less_ratio": 39.59,
        },
        missing_reason=None,
    )
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["portfolio_diagnosis"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 6, 13),
        freshness="fresh",
        payload={
            "thousand_lot_holder_ratio": 38.2,
            "large_holder_400_lot_plus_ratio": 51.58,
            "retail_100_lot_or_less_ratio": 38.49,
        },
        missing_reason=None,
    )
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["portfolio_diagnosis"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=date(2026, 6, 27),
        freshness="fresh",
        payload={
            "thousand_lot_holder_ratio": 99.0,
            "large_holder_400_lot_plus_ratio": 99.0,
            "retail_100_lot_or_less_ratio": 1.0,
        },
        missing_reason=None,
    )
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/risk-summary")

    assert resp.status_code == 200
    position = resp.json()["position_risks"][0]
    assert position["weekly_major_holders"] == {
        "status": "fresh",
        "as_of_date": "2026-06-13",
        "previous_as_of_date": "2026-06-06",
        "thousand_lot_holder_ratio": 38.2,
        "thousand_lot_holder_ratio_delta_pp": 1.52,
        "large_holder_400_lot_plus_ratio": 51.58,
        "large_holder_400_lot_plus_ratio_delta_pp": 0.88,
        "retail_100_lot_or_less_ratio": 38.49,
        "retail_100_lot_or_less_ratio_delta_pp": -1.1,
        "previous_thousand_lot_holder_ratio_delta_pp": 1.68,
        "consecutive_thousand_lot_holder_ratio_increase_count": 2,
    }
    assert position["chip_stability_context"]["source"] == "tdcc_weekly_major_holders"
    assert position["chip_stability_context"]["state"] == "stable"
    assert position["chip_stability_context"]["trend"] == "strengthening"
    assert position["chip_stability_context"]["summary"] == "千張大戶持股比例連續增加，籌碼愈加穩定。"
    assert all("score" not in key for key in position["chip_stability_context"])
    assert position["risk_state"] == "elevated"


def test_portfolio_risk_summary_omits_missing_weekly_major_holders_context(
    monkeypatch: pytest.MonkeyPatch,
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    summary_date = date(2026, 6, 20)
    monkeypatch.setattr(portfolio_risk_summary_app, "today_taipei", lambda: summary_date)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-weekly-holder-missing",
        symbol="2330.TW",
        entry_price=100,
        quantity=10,
        entry_date=date(2026, 6, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-weekly-holder-missing",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_stop_price=95,
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.add(StockRawData(
        symbol="2330.TW",
        record_date=summary_date,
        technical={"close_price": 120},
        raw_data_is_final=True,
    ))
    upsert_shared_background_context(
        portfolio_db_session,
        symbol="2330.TW",
        context_type="weekly_major_holders",
        applicable_consumers=["portfolio_diagnosis"],
        source={"domain": "background_context", "provider": "fixture"},
        as_of_date=None,
        freshness="missing",
        payload={},
        missing_reason="tdcc_symbol_not_found",
        replay_key="background_context:2330.TW:weekly_major_holders:2026-06-20:missing:tdcc_symbol_not_found",
    )
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/risk-summary")

    assert resp.status_code == 200
    position = resp.json()["position_risks"][0]
    assert "weekly_major_holders" not in position
    assert "chip_stability_context" not in position
    assert position["risk_state"] == "elevated"


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


def test_refresh_portfolio_prices_updates_selected_quotes_without_persisting_raw_data(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add_all([
        UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-refresh-success",
            symbol="2330.TW",
            entry_price=100,
            quantity=10,
            entry_date=date(2026, 6, 1),
        ),
        UserPortfolio(
            id=43,
            user_id=1,
            position_group_id="group-refresh-fallback",
            symbol="2317.TW",
            entry_price=50,
            quantity=20,
            entry_date=date(2026, 6, 1),
        ),
        PositionLifecyclePlan(
            user_id=1,
            position_group_id="group-refresh-success",
            symbol="2330.TW",
            source_portfolio_id=42,
            planned_stop_price=95,
            source="user_recorded_at_event_time",
            created_after_entry=False,
        ),
        PositionLifecyclePlan(
            user_id=1,
            position_group_id="group-refresh-fallback",
            symbol="2317.TW",
            source_portfolio_id=43,
            planned_stop_price=45,
            source="user_recorded_at_event_time",
            created_after_entry=False,
        ),
        StockRawData(
            symbol="2330.TW",
            record_date=date.today(),
            technical={"close_price": 120},
            raw_data_is_final=True,
        ),
        StockRawData(
            symbol="2317.TW",
            record_date=date.today(),
            technical={"close_price": 60},
            raw_data_is_final=True,
        ),
    ])
    portfolio_db_session.commit()

    def fetch_quote(symbol: str) -> StockSnapshot:
        if symbol == "2317.TW":
            raise TimeoutError("provider timeout")
        return StockSnapshot(
            symbol=symbol,
            currency="TWD",
            current_price=130,
            previous_close=120,
            day_open=121,
            day_high=132,
            day_low=119,
            volume=1000,
            recent_closes=[120, 130],
            recent_volume_dates=[date.today().isoformat()],
            fetched_at="2026-07-31T10:30:00+08:00",
        )

    api.app.dependency_overrides[portfolio_router_module.get_portfolio_quote_fetcher] = lambda: fetch_quote
    resp = portfolio_db_client.post(
        "/portfolio/risk-summary/refresh-prices",
        json={"portfolio_ids": None},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["price_refresh"]["status"] == "partial"
    assert data["price_refresh"]["refreshed_symbols"] == ["2330.TW"]
    assert data["price_refresh"]["failed_symbols"] == ["2317.TW"]
    risks = {item["symbol"]: item for item in data["position_risks"]}
    assert risks["2330.TW"]["current_price"] == 130
    assert risks["2330.TW"]["price_context"]["refresh_status"] == "refreshed"
    assert risks["2317.TW"]["current_price"] == 60
    assert risks["2317.TW"]["price_context"]["refresh_status"] == "failed"

    persisted = {
        row.symbol: row
        for row in portfolio_db_session.execute(select(StockRawData)).scalars().all()
    }
    assert persisted["2330.TW"].technical["close_price"] == 120
    assert persisted["2317.TW"].technical["close_price"] == 60
    assert len(persisted) == 2


def test_refresh_portfolio_prices_rejects_unowned_or_closed_target(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post(
        "/portfolio/risk-summary/refresh-prices",
        json={"portfolio_ids": [999]},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "持股不存在或已結案"


def test_refresh_portfolio_prices_rejects_more_than_500_targets(
    portfolio_db_client: TestClient,
):
    resp = portfolio_db_client.post(
        "/portfolio/risk-summary/refresh-prices",
        json={"portfolio_ids": list(range(501))},
    )

    assert resp.status_code == 422


def test_price_refresh_deadline_returns_without_waiting_for_provider():
    from threading import Event
    import time

    provider_release = Event()

    def slow_fetcher(symbol: str) -> StockSnapshot:
        provider_release.wait(timeout=1)
        return StockSnapshot(
            symbol=symbol,
            currency="TWD",
            current_price=100,
            previous_close=99,
            day_open=99,
            day_high=101,
            day_low=98,
            volume=100,
            recent_closes=[99, 100],
            fetched_at="2026-07-31T10:00:00+08:00",
        )

    started_at = time.monotonic()
    try:
        quotes = _fetch_quotes(
            ["2330.TW"],
            quote_fetcher=slow_fetcher,
            response_deadline=0,
        )
    finally:
        provider_release.set()

    assert time.monotonic() - started_at < 0.5
    assert quotes["2330.TW"]["error_code"] == "TimeoutError"


def test_price_refresh_reports_capacity_exhaustion_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch,
):
    from threading import BoundedSemaphore

    exhausted_capacity = BoundedSemaphore(1)
    exhausted_capacity.acquire()
    monkeypatch.setattr(refresh_prices_module, "_PRICE_REFRESH_CAPACITY", exhausted_capacity)

    quotes = _fetch_quotes(
        ["2330.TW"],
        quote_fetcher=lambda _symbol: pytest.fail("provider must not run without capacity"),
        response_deadline=0.1,
    )

    assert quotes["2330.TW"]["error_code"] == "ProviderCapacityExhausted"


def test_price_refresh_processes_more_symbols_than_the_worker_count():
    symbols = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "6488.TWO"]

    def fetcher(symbol: str) -> StockSnapshot:
        return StockSnapshot(
            symbol=symbol,
            currency="TWD",
            current_price=100,
            previous_close=99,
            day_open=99,
            day_high=101,
            day_low=98,
            volume=100,
            recent_closes=[99, 100],
            fetched_at="2026-07-31T10:00:00+08:00",
        )

    quotes = _fetch_quotes(
        symbols,
        quote_fetcher=fetcher,
    )

    assert set(quotes) == set(symbols)
    assert all(quote["status"] == "refreshed" for quote in quotes.values())


def test_price_refresh_uses_taiwan_exchange_session_at_quote_observation_time():
    snapshot = StockSnapshot(
        symbol="6488.TWO",
        currency="TWD",
        current_price=701,
        previous_close=695,
        day_open=698,
        day_high=703,
        day_low=696,
        volume=1000,
        recent_closes=[695, 701],
        recent_volume_dates=["2026-07-31"],
        fetched_at="2026-07-31T02:00:00+00:00",
        exchange="TWO",
        exchange_timezone="Asia/Taipei",
        regular_market_open="2026-07-31T09:00:00+08:00",
        regular_market_close="2026-07-31T13:30:00+08:00",
    )

    quote = _quote_payload(snapshot)

    assert quote["market_session"] == "intraday"
    assert quote["is_final"] is False

    snapshot.fetched_at = "2026-07-31T06:00:00+00:00"
    closed_quote = _quote_payload(snapshot)

    assert closed_quote["market_session"] == "closed"
    assert closed_quote["is_final"] is True


def test_price_refresh_keeps_live_session_when_daily_volume_date_is_previous_day():
    snapshot = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=1200,
        previous_close=1180,
        day_open=1190,
        day_high=1210,
        day_low=1185,
        volume=1000,
        recent_closes=[1180],
        recent_volume_dates=["2026-07-30"],
        fetched_at="2026-07-31T02:00:00+00:00",
        exchange="TAI",
        exchange_timezone="Asia/Taipei",
        regular_market_open="2026-07-31T09:00:00+08:00",
        regular_market_close="2026-07-31T13:30:00+08:00",
    )

    quote = _quote_payload(snapshot)

    assert quote["data_date"] == "2026-07-30"
    assert quote["market_session"] == "intraday"
    assert quote["is_final"] is False


@pytest.mark.parametrize("fetched_at", ["invalid", "2026-07-31T10:00:00"])
def test_price_refresh_reports_unknown_when_quote_observation_time_is_unusable(
    fetched_at: str,
):
    snapshot = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=1200,
        previous_close=1180,
        day_open=1190,
        day_high=1210,
        day_low=1185,
        volume=1000,
        recent_closes=[1180, 1200],
        fetched_at=fetched_at,
        exchange="TAI",
        exchange_timezone="Asia/Taipei",
        regular_market_open="2026-07-31T09:00:00+08:00",
        regular_market_close="2026-07-31T13:30:00+08:00",
    )

    quote = _quote_payload(snapshot)

    assert quote["market_session"] == "unknown"
    assert quote["is_final"] is None


def test_price_refresh_reports_unknown_for_unmapped_exchange():
    snapshot = StockSnapshot(
        symbol="UNKNOWN",
        currency="USD",
        current_price=10,
        previous_close=9,
        day_open=9,
        day_high=10,
        day_low=9,
        volume=100,
        recent_closes=[9, 10],
        recent_volume_dates=["2026-07-31"],
        fetched_at="2026-07-31T15:00:00+00:00",
        exchange="UNKNOWN",
        exchange_timezone="America/New_York",
    )

    quote = _quote_payload(snapshot)

    assert quote["market_session"] == "unknown"
    assert quote["is_final"] is None


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


def test_update_lifecycle_plan_marks_changed_event_time_plan_as_retrospective(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-edit-plan",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-edit-plan",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_holding_period="swing",
        default_stop_rule="break_ma20",
        planned_stop_price=880,
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42/lifecycle-plan", json={
        "planned_holding_period": "medium_term",
        "default_stop_rule": "fixed_price",
        "planned_invalidation": "Close below fixed defense price.",
        "planned_stop_price": 860.0,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "user_backfilled"
    assert data["created_after_entry"] is True
    assert data["planned_holding_period"] == "medium_term"
    assert data["default_stop_rule"] == "fixed_price"
    assert data["planned_stop_price"] == 860.0
    plan = portfolio_db_session.execute(select(PositionLifecyclePlan)).scalar_one()
    assert plan.source == "user_backfilled"
    assert plan.created_after_entry is True
    assert float(plan.planned_stop_price) == 860.0


def test_update_lifecycle_plan_preserves_event_time_provenance_when_values_are_unchanged(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-unchanged-plan",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.add(PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-unchanged-plan",
        symbol="2330.TW",
        source_portfolio_id=42,
        planned_holding_period="swing",
        default_stop_rule="break_ma20",
        planned_stop_price=880,
        source="user_recorded_at_event_time",
        created_after_entry=False,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42/lifecycle-plan", json={
        "planned_holding_period": "swing",
        "default_stop_rule": "break_ma20",
        "planned_stop_price": 880.0,
    })

    assert resp.status_code == 200
    assert resp.json()["source"] == "user_recorded_at_event_time"
    assert resp.json()["created_after_entry"] is False


def test_update_lifecycle_plan_creates_backfilled_plan_when_missing(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-edit-plan-missing",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.put("/portfolio/42/lifecycle-plan", json={
        "default_stop_rule": "fixed_price",
        "planned_stop_price": 870.0,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "user_backfilled"
    assert data["created_after_entry"] is True
    assert data["default_stop_rule"] == "fixed_price"
    assert data["planned_stop_price"] == 870.0
    plan = portfolio_db_session.execute(select(PositionLifecyclePlan)).scalar_one()
    assert plan.source == "user_backfilled"
    assert plan.created_after_entry is True


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
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "initial_entry")
    ).scalar_one()
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
            "planned_stop_price": 880.0,
            "add_entry_condition": "pullback_holds_ma20",
            "note": "event-time note",
        },
    })

    assert resp.status_code == 201
    assert resp.json()["symbol"] == "2330.TW"
    assert resp.json()["name"] == "台積電"
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "initial_entry")
    ).scalar_one()
    assert event.event_type == "initial_entry"
    assert event.reason_code == "breakout_confirmation"
    assert event.reason_category == "technical"
    assert event.source == "user_recorded_at_event_time"
    assert event.note == "event-time note"
    plan = portfolio_db_session.execute(select(PositionLifecyclePlan)).scalar_one()
    assert plan.planned_holding_period == "swing"
    assert plan.default_stop_rule == "break_ma20"
    assert float(plan.planned_stop_price) == 880.0
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


def test_add_portfolio_rejects_invalid_entry_record_planned_stop_price(
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
            "default_stop_rule": "break_ma20",
            "planned_stop_price": 0,
        },
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
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "full_exit")
    ).scalar_one()
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
                "data_dates": {"ohlcv": record_date.isoformat()},
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
        is_active=False,
        exit_date=date(2026, 1, 11),
        exit_price=950,
        exit_quantity=100,
        realized_pnl=5000,
        realized_return_pct=5.5556,
        holding_days=10,
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
    session.add(PositionEvent(
        user_id=user_id,
        position_group_id=position_group_id,
        symbol=symbol,
        event_type="full_exit",
        event_date=date(2026, 1, 11),
        price=950,
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
    assert data["review_version"] == "position-lifecycle-review-v3"
    assert data["llm_summary"] is None
    assert data["review_result"]["lifecycle_review"]["classification"]["tier"] == "constructive"
    assert data["evidence_payload"]["events"] == [{"event_type": "initial_entry"}]
    assert calls == [(portfolio_db_session, 1, "group-life-review")]
    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].review_result == data["review_result"]
    assert reviews[0].evidence_payload == data["evidence_payload"]


def test_position_lifecycle_review_rejects_active_or_open_ledger_group(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_builder(*_args, **_kwargs):
        raise AssertionError("open lifecycle must be rejected before review building")

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fail_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add(UserPortfolio(
        id=88,
        user_id=1,
        position_group_id="group-open-review",
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=date(2026, 1, 1),
        is_active=True,
    ))
    portfolio_db_session.add(PositionEvent(
        user_id=1,
        position_group_id="group-open-review",
        symbol="2330.TW",
        event_type="initial_entry",
        event_date=date(2026, 1, 1),
        price=900,
        quantity=100,
        fees=0,
        taxes=0,
        source_portfolio_id=88,
        source="user_recorded_at_event_time",
    ))
    portfolio_db_session.commit()

    post_resp = portfolio_db_client.post("/portfolio/groups/group-open-review/lifecycle-review")
    get_resp = portfolio_db_client.get("/portfolio/groups/group-open-review/lifecycle-review")

    assert post_resp.status_code == 409
    assert get_resp.status_code == 409
    assert post_resp.json()["detail"]["code"] == "position_lifecycle_not_closed"
    assert portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all() == []


def test_position_lifecycle_review_rebuilds_when_market_snapshot_changes(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)

    first = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")
    portfolio_db_session.add(StockRawData(
        symbol="2330.TW",
        record_date=date(2026, 1, 5),
        technical={"ohlcv": {"open": 910, "high": 915, "low": 905, "close": 912, "volume": 1000}},
        raw_data_is_final=True,
    ))
    portfolio_db_session.commit()
    second = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["evidence_payload"]["source_fingerprint"] != first.json()["evidence_payload"]["source_fingerprint"]
    assert second.json()["evidence_payload"]["market_snapshot"]["quality"]["row_count"] == 1


def test_position_lifecycle_review_keeps_better_snapshot_when_market_rows_regress(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    portfolio_db_session.add(StockRawData(
        symbol="2330.TW",
        record_date=date(2026, 1, 5),
        technical={"ohlcv": {"open": 910, "high": 915, "low": 905, "close": 912, "volume": 1000}},
        raw_data_is_final=True,
    ))
    portfolio_db_session.commit()
    first = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    row = portfolio_db_session.execute(select(StockRawData)).scalar_one()
    portfolio_db_session.delete(row)
    portfolio_db_session.commit()
    second = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.json()["evidence_payload"]["market_snapshot"]["quality"]["row_count"] == 1


def test_position_lifecycle_review_preserves_unknown_newer_version(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_builder(*_args, **_kwargs):
        raise AssertionError("unknown newer lifecycle review must remain read-only")

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", fail_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    portfolio_db_session.add(PositionLifecycleReview(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v3",
        review_result={"current": True},
        evidence_payload={"current": True},
        llm_summary=None,
    ))
    portfolio_db_session.add(PositionLifecycleReview(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v4",
        review_result={"future": True},
        evidence_payload={"future": True},
        llm_summary="future summary",
    ))
    portfolio_db_session.commit()

    post_resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")
    get_resp = portfolio_db_client.get("/portfolio/groups/group-life-review/lifecycle-review")

    assert post_resp.status_code == 200
    assert get_resp.status_code == 200
    assert post_resp.json()["review_version"] == "position-lifecycle-review-v4"
    assert get_resp.json()["review_version"] == "position-lifecycle-review-v4"
    assert len(portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()) == 2


def test_trade_review_regression_guard_prefers_normal_provider_over_larger_fallback():
    existing = SimpleNamespace(evidence_payload={
        "trade": {"id": 42},
        "market_snapshot": {
            "provider": "yfinance",
            "quality": {"row_count": 80, "missing_reason": None},
        },
    })
    fallback = {
        "trade": {"id": 42},
        "market_snapshot": {
            "provider": "stock_raw_data_read_only_fallback",
            "quality": {"row_count": 200, "missing_reason": "provider_fetch_failed_or_empty"},
        },
    }

    assert portfolio_router_module._trade_review_snapshot_regressed(existing, fallback) is True


def test_trade_review_regression_guard_rejects_severe_row_loss_when_provider_recovers():
    existing = SimpleNamespace(evidence_payload={
        "trade": {"id": 42},
        "market_snapshot": {
            "provider": "stock_raw_data_read_only_fallback",
            "quality": {
                "coverage_version": "market-coverage-v1",
                "coverage_basis": "dated_bars",
                "trading_bar_count": 80,
                "row_count": 1,
                "missing_reason": "provider_fetch_failed_or_empty",
            },
        },
    })
    tiny_provider_snapshot = {
        "trade": {"id": 42},
        "market_snapshot": {
            "provider": "yfinance",
            "quality": {
                "coverage_version": "market-coverage-v1",
                "coverage_basis": "dated_bars",
                "trading_bar_count": 1,
                "row_count": 1,
                "missing_reason": None,
            },
        },
    }

    assert portfolio_router_module._trade_review_snapshot_regressed(existing, tiny_provider_snapshot) is True


@pytest.mark.parametrize(("provider_rows", "expected"), [(72, False), (71, True)])
def test_trade_review_provider_upgrade_requires_ninety_percent_coverage(
    provider_rows: int,
    expected: bool,
):
    existing = SimpleNamespace(evidence_payload={
        "trade": {"id": 42},
        "market_snapshot": {
            "quality": {
                "coverage_version": "market-coverage-v1",
                "coverage_basis": "estimated_trailing_series",
                "trading_bar_count": 80,
                "row_count": 1,
                "missing_reason": "provider_fetch_failed_or_empty",
            },
        },
    })
    provider_snapshot = {
        "trade": {"id": 42},
        "market_snapshot": {
            "quality": {
                "coverage_version": "market-coverage-v1",
                "coverage_basis": "dated_bars",
                "trading_bar_count": provider_rows,
                "row_count": provider_rows,
                "missing_reason": None,
            },
        },
    }

    assert portfolio_router_module._trade_review_snapshot_regressed(existing, provider_snapshot) is expected


def test_trade_review_coverage_does_not_compare_outer_rows_across_providers():
    fallback = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "dated_bars",
            "trading_bar_count": 70,
            "row_count": 80,
            "missing_reason": "provider_fetch_failed_or_empty",
        },
    }
    provider = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "dated_bars",
            "trading_bar_count": 70,
            "row_count": 70,
            "missing_reason": None,
        },
    }

    assert portfolio_router_module._market_snapshot_regressed(fallback, provider) is False


def test_trade_review_provider_upgrade_preserves_dated_coverage_bounds():
    fallback = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "dated_bars",
            "trading_bar_count": 80,
            "date_start": "2026-01-01",
            "date_end": "2026-04-30",
            "missing_reason": "provider_fetch_failed_or_empty",
        },
    }
    shifted_provider = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "dated_bars",
            "trading_bar_count": 80,
            "date_start": "2026-01-02",
            "date_end": "2026-04-30",
            "missing_reason": None,
        },
    }

    assert portfolio_router_module._market_snapshot_regressed(fallback, shifted_provider) is True


def test_trade_review_singleflight_rejects_duplicate_without_blocking_worker():
    key = (1, 42)
    first_entered = Event()
    release_first = Event()
    second_started = Event()
    second_finished = Event()
    acquisitions = []

    def first_request() -> None:
        with portfolio_router_module._trade_review_refresh_singleflight(key) as acquired:
            acquisitions.append(acquired)
            first_entered.set()
            assert release_first.wait(timeout=1)

    def second_request() -> None:
        assert first_entered.wait(timeout=1)
        second_started.set()
        with portfolio_router_module._trade_review_refresh_singleflight(key) as acquired:
            acquisitions.append(acquired)
        second_finished.set()

    first = Thread(target=first_request)
    second = Thread(target=second_request)
    first.start()
    second.start()
    assert second_started.wait(timeout=1)
    assert second_finished.wait(timeout=1)
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert acquisitions == [True, False]
    assert key not in portfolio_router_module._TRADE_REVIEW_REFRESH_SLOTS


def test_create_trade_review_returns_conflict_when_refresh_is_in_progress(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)

    with portfolio_router_module._trade_review_refresh_singleflight((1, 42)) as acquired:
        assert acquired is True
        response = portfolio_db_client.post(
            "/portfolio/42/review",
            headers={"Origin": "http://localhost:5173"},
        )

    assert response.status_code == 409
    assert response.headers["retry-after"] == "1"
    assert response.headers["access-control-expose-headers"] == "Retry-After"


def test_trade_review_regression_guard_allows_material_fallback_recovery():
    tiny_provider = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "dated_bars",
            "trading_bar_count": 1,
            "covered_dates": ["2026-01-01"],
            "holding_covered_dates": ["2026-01-01"],
            "date_start": "2026-01-01",
            "date_end": "2026-01-01",
            "missing_reason": None,
        },
    }
    rich_fallback = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "estimated_trailing_series",
            "trading_bar_count": 80,
            "covered_dates": [],
            "holding_covered_dates": [],
            "date_start": None,
            "date_end": None,
            "missing_reason": "provider_fetch_failed_or_empty",
        },
    }

    assert portfolio_router_module._market_snapshot_regressed(tiny_provider, rich_fallback) is False


def test_trade_review_regression_guard_rejects_internal_date_gap():
    existing_dates = [date(2026, 1, 1) + timedelta(days=offset * 2) for offset in range(40)]
    candidate_dates = list(existing_dates)
    candidate_dates[20] += timedelta(days=1)

    def snapshot(dates: list[date]) -> dict:
        return {
            "quality": {
                "coverage_version": "market-coverage-v1",
                "coverage_basis": "dated_bars",
                "trading_bar_count": len(dates),
                "covered_dates": [value.isoformat() for value in dates],
                "date_start": dates[0].isoformat(),
                "date_end": dates[-1].isoformat(),
                "missing_reason": None,
            },
        }

    assert portfolio_router_module._market_snapshot_regressed(
        snapshot(existing_dates),
        snapshot(candidate_dates),
    ) is True


def test_create_trade_review_same_content_refresh_advances_fetched_at(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)
    now = datetime.now(timezone.utc)
    observed_times = [now - timedelta(hours=7), now]

    def stable_market_snapshot(db: Session, target: portfolio_router_module.TradeReviewMarketTarget):
        rows = db.execute(
            select(StockRawData)
            .where(StockRawData.symbol == target.symbol)
            .order_by(StockRawData.record_date.asc())
        ).scalars().all()
        fetched_at = observed_times.pop(0)
        return SimpleNamespace(
            rows=rows,
            evidence=market_snapshot_payload(
                rows,
                provider="yfinance",
                fetched_at=fetched_at,
                coverage_start=target.entry_date - timedelta(days=120),
                coverage_end=target.exit_date,
            ),
        )

    monkeypatch.setattr(
        portfolio_router_module,
        "ensure_trade_review_market_data",
        stable_market_snapshot,
    )

    first = portfolio_db_client.post("/portfolio/42/review")
    second = portfolio_db_client.post("/portfolio/42/review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert observed_times == []
    assert first.json()["evidence_payload"]["source_fingerprint"] == second.json()["evidence_payload"]["source_fingerprint"]
    assert first.json()["evidence_payload"]["market_snapshot"]["fetched_at"] != second.json()["evidence_payload"]["market_snapshot"]["fetched_at"]
    assert second.json()["evidence_payload"]["market_snapshot"]["fetched_at"] == now.isoformat()


def _trade_review_item() -> UserPortfolio:
    return UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-review",
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


@pytest.mark.parametrize(
    ("missing_reason", "age", "expected"),
    [
        (None, timedelta(hours=5), True),
        (None, timedelta(hours=7), False),
        (None, timedelta(minutes=-1), False),
        ("provider_fetch_failed_or_empty", timedelta(minutes=4), True),
        ("provider_fetch_failed_or_empty", timedelta(minutes=6), False),
        ("provider_coverage_insufficient", timedelta(hours=23), True),
        ("provider_coverage_insufficient", timedelta(hours=25), False),
    ],
)
def test_trade_review_cache_uses_distinct_success_and_failure_ttls(
    missing_reason: str | None,
    age: timedelta,
    expected: bool,
):
    now = datetime(2026, 8, 4, 8, tzinfo=timezone.utc)
    item = _trade_review_item()
    review = SimpleNamespace(
        review_version="trade-review-v3",
        evidence_payload={
            "trade": portfolio_router_module.trade_review_source_payload(item),
            "market_snapshot": {
                "fetched_at": (now - age).isoformat(),
                "quality": {"row_count": 80, "missing_reason": missing_reason},
            },
        },
    )

    assert portfolio_router_module._trade_review_cache_reusable(review, item, now=now) is expected


def test_trade_review_fresh_fallback_does_not_supersede_better_provider_snapshot():
    now = datetime.now(timezone.utc)
    item = _trade_review_item()
    quality = {
        "coverage_version": "market-coverage-v1",
        "coverage_basis": "dated_bars",
        "trading_bar_count": 80,
        "row_count": 80,
    }
    existing = SimpleNamespace(
        review_version="trade-review-v3",
        evidence_payload={
            "trade": portfolio_router_module.trade_review_source_payload(item),
            "market_snapshot": {
                "fetched_at": now.isoformat(),
                "quality": {**quality, "missing_reason": "provider_fetch_failed_or_empty"},
            },
        },
    )
    provider_snapshot = SimpleNamespace(evidence={
        "fetched_at": (now - timedelta(seconds=1)).isoformat(),
        "quality": {**quality, "missing_reason": None},
    })

    assert portfolio_router_module._trade_review_refresh_superseded(
        existing,
        item,
        provider_snapshot,
    ) is False


def test_trade_review_newer_saved_snapshot_supersedes_older_equal_quality_fetch():
    now = datetime.now(timezone.utc)
    item = _trade_review_item()
    quality = {
        "coverage_version": "market-coverage-v1",
        "coverage_basis": "dated_bars",
        "trading_bar_count": 80,
        "row_count": 80,
        "missing_reason": None,
    }
    existing = SimpleNamespace(
        review_version="trade-review-v3",
        evidence_payload={
            "trade": portfolio_router_module.trade_review_source_payload(item),
            "market_snapshot": {"fetched_at": now.isoformat(), "quality": quality},
        },
    )
    older_snapshot = SimpleNamespace(evidence={
        "fetched_at": (now - timedelta(seconds=1)).isoformat(),
        "quality": quality,
    })

    assert portfolio_router_module._trade_review_refresh_superseded(
        existing,
        item,
        older_snapshot,
    ) is True


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
    assert data["review_version"] == "position-lifecycle-review-v3"


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


@pytest.mark.parametrize("saved_version", ["position-lifecycle-review-v1", "position-lifecycle-review-v2"])
def test_get_position_lifecycle_review_can_read_saved_version_until_post_upgrades_to_v3(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    saved_version: str,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    portfolio_db_session.add(PositionLifecycleReview(
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version=saved_version,
        review_result={"legacy": True},
        evidence_payload={"legacy": True},
    ))
    portfolio_db_session.commit()

    get_resp = portfolio_db_client.get("/portfolio/groups/group-life-review/lifecycle-review")
    post_resp = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert get_resp.status_code == 200
    assert get_resp.json()["review_version"] == saved_version
    assert get_resp.json()["review_result"] == {"legacy": True}
    assert post_resp.status_code == 200
    assert post_resp.json()["review_version"] == "position-lifecycle-review-v3"
    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert {review.review_version for review in reviews} == {saved_version, "position-lifecycle-review-v3"}


def test_get_position_lifecycle_review_missing_owned_group_returns_404(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)

    resp = portfolio_db_client.get("/portfolio/groups/group-life-review/lifecycle-review")

    assert resp.status_code == 404
    assert portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all() == []


def test_create_position_lifecycle_review_same_fingerprint_does_not_duplicate(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []

    def stable_builder(_db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        calls.append((user_id, position_group_id))
        return _lifecycle_payload(position_group_id)

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", stable_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)

    first = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")
    second = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert calls == [(1, "group-life-review"), (1, "group-life-review")]
    assert first.json()["review_result"] == _lifecycle_payload()[0]
    reviews = portfolio_db_session.execute(select(PositionLifecycleReview)).scalars().all()
    assert len(reviews) == 1


def test_create_position_lifecycle_review_refreshes_same_source_after_ruleset_upgrade(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def stable_builder(_db: Session, *, user_id: int, position_group_id: str) -> tuple[dict, dict]:
        return _lifecycle_payload(position_group_id)

    monkeypatch.setattr(portfolio_router_module, "build_position_lifecycle_analysis", stable_builder)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_lifecycle_group(portfolio_db_session)
    _, legacy_evidence = _lifecycle_payload()
    legacy_fingerprint = attach_source_fingerprint(
        legacy_evidence,
        ruleset_version="position-lifecycle-ruleset-v3.1",
    )
    portfolio_db_session.add(PositionLifecycleReview(
        id=9,
        user_id=1,
        position_group_id="group-life-review",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v3",
        review_result={"legacy_copy": True},
        evidence_payload=legacy_evidence,
    ))
    portfolio_db_session.commit()

    response = portfolio_db_client.post("/portfolio/groups/group-life-review/lifecycle-review")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 9
    assert data["review_version"] == "position-lifecycle-review-v3"
    assert data["review_result"] == _lifecycle_payload()[0]
    assert data["evidence_payload"]["ruleset_version"] == "position-lifecycle-ruleset-v3.2"
    assert data["evidence_payload"]["source_fingerprint"] != legacy_fingerprint
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
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "initial_entry")
    ).scalar_one()
    event.updated_at = datetime(2026, 1, 1, 10, 0, 0)
    portfolio_db_session.add(PositionLifecycleReview(
        id=7,
        user_id=1,
        position_group_id="group-life-review",
        symbol="OLD.TW",
        review_version="position-lifecycle-review-v3",
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
    assert data["evidence_payload"]["source"] == "event"
    assert data["evidence_payload"]["events"] == [{"event_type": "full_exit"}]
    assert data["evidence_payload"]["ruleset_version"] == "position-lifecycle-ruleset-v3.2"
    assert len(data["evidence_payload"]["source_fingerprint"]) == 64
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
    event = portfolio_db_session.execute(
        select(PositionEvent).where(PositionEvent.event_type == "initial_entry")
    ).scalar_one()
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
        review_version="position-lifecycle-review-v3",
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
    assert data["evidence_payload"]["source"] == "plan"
    assert data["evidence_payload"]["plan"] == {"planned_holding_period": "long_term"}
    assert data["evidence_payload"]["ruleset_version"] == "position-lifecycle-ruleset-v3.2"
    assert len(data["evidence_payload"]["source_fingerprint"]) == 64
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
    portfolio_db_session.add(PositionEvent(
        user_id=1,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        event_type="full_exit",
        event_date=item.exit_date,
        price=item.exit_price,
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
    assert lifecycle_resp.json()["review_version"] == "position-lifecycle-review-v3"
    assert trade_resp.json()["review_version"] == "trade-review-v3"
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
    assert data["review_version"] == "trade-review-v3"
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
        "early", "reasonable", "late", "unclassified", "insufficient",
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
        "market_snapshot", "ruleset_version", "source_fingerprint",
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
    assert data["review_version"] == "trade-review-v3"
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

    def spy_ensure(_db: Session, item: portfolio_router_module.TradeReviewMarketTarget) -> None:
        calls.append((item.symbol, item.entry_date, item.exit_date))

    monkeypatch.setattr(portfolio_router_module, "ensure_trade_review_market_data", spy_ensure)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert calls == [("2330.TW", date(2026, 1, 1), date(2026, 1, 11))]


def test_create_trade_review_releases_db_transaction_before_provider_fetch(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    transaction_states = []

    def spy_ensure(db: Session, _item: UserPortfolio):
        transaction_states.append(db.in_transaction())
        return None

    monkeypatch.setattr(portfolio_router_module, "ensure_trade_review_market_data", spy_ensure)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert transaction_states == [False]


def test_create_trade_review_rechecks_freshness_after_provider_fetch(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = _add_closed_portfolio(portfolio_db_session)
    trade_payload = portfolio_router_module.trade_review_source_payload(item)
    review = TradeReview(
        portfolio_id=item.id,
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        review_version="trade-review-v3",
        review_result={"generation": "stale"},
        evidence_payload={
            "trade": trade_payload,
            "market_snapshot": {
                "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),
                "quality": {"missing_reason": None},
            },
        },
        llm_summary=None,
    )
    portfolio_db_session.add(review)
    portfolio_db_session.commit()

    def concurrent_refresh(db: Session, _target: portfolio_router_module.TradeReviewMarketTarget):
        saved = db.execute(select(TradeReview).where(TradeReview.portfolio_id == item.id)).scalar_one()
        saved.review_result = {"generation": "fresh-from-concurrent-request"}
        saved.evidence_payload = {
            "trade": trade_payload,
            "market_snapshot": {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "quality": {"missing_reason": None},
            },
        }
        db.commit()
        return None

    monkeypatch.setattr(portfolio_router_module, "ensure_trade_review_market_data", concurrent_refresh)

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert resp.json()["review_result"] == {"generation": "fresh-from-concurrent-request"}
    portfolio_db_session.expire_all()
    assert portfolio_db_session.execute(select(TradeReview)).scalar_one().review_result == {
        "generation": "fresh-from-concurrent-request",
    }


def test_create_trade_review_upgrades_v2_review_using_current_sources(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = _add_closed_portfolio(portfolio_db_session)
    portfolio_db_session.add(TradeReview(
        portfolio_id=item.id,
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        review_version="trade-review-v2",
        review_result={"existing": True},
        evidence_payload={"existing": True},
        llm_summary=None,
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert resp.json()["review_version"] == "trade-review-v3"
    assert resp.json()["review_result"] != {"existing": True}
    assert resp.json()["evidence_payload"]["ruleset_version"] == "trade-review-v3"


def test_create_trade_review_rebuilds_legacy_review_version_in_place(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = _add_closed_portfolio(portfolio_db_session)
    portfolio_db_session.add(TradeReview(
        portfolio_id=item.id,
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        review_version="trade-review-v1",
        review_result={"legacy": True},
        evidence_payload={"legacy": True},
        llm_summary="legacy summary",
    ))
    portfolio_db_session.commit()
    _add_raw_rows(portfolio_db_session)

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert resp.json()["review_version"] == "trade-review-v3"
    assert resp.json()["review_result"] != {"legacy": True}
    assert resp.json()["llm_summary"] is None
    reviews = portfolio_db_session.execute(select(TradeReview)).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].review_version == "trade-review-v3"


def test_create_trade_review_preserves_unknown_newer_review_version(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_ensure(_db: Session, _item: UserPortfolio) -> None:
        raise AssertionError("unknown newer review must not trigger a destructive rebuild")

    monkeypatch.setattr(portfolio_router_module, "ensure_trade_review_market_data", fail_ensure)
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    item = _add_closed_portfolio(portfolio_db_session)
    portfolio_db_session.add(TradeReview(
        portfolio_id=item.id,
        user_id=item.user_id,
        position_group_id=item.position_group_id,
        symbol=item.symbol,
        review_version="trade-review-v4",
        review_result={"future": True},
        evidence_payload={"future": True},
        llm_summary="future summary",
    ))
    portfolio_db_session.commit()

    resp = portfolio_db_client.post("/portfolio/42/review")

    assert resp.status_code == 200
    assert resp.json()["review_version"] == "trade-review-v4"
    assert resp.json()["review_result"] == {"future": True}
    assert resp.json()["llm_summary"] == "future summary"
    review = portfolio_db_session.execute(select(TradeReview)).scalar_one()
    assert review.review_version == "trade-review-v4"
    assert review.evidence_payload == {"future": True}


def test_create_trade_review_second_post_returns_existing_without_duplicate(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)

    first = portfolio_db_client.post("/portfolio/42/review")
    second = portfolio_db_client.post("/portfolio/42/review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.json()["review_result"]["trade_result"]["max_profit_pct"] == pytest.approx(6.6667)
    reviews = portfolio_db_session.execute(select(TradeReview)).scalars().all()
    assert len(reviews) == 1


def test_create_trade_review_rebuilds_when_review_market_snapshot_changes(
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
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["evidence_payload"]["source_fingerprint"] != first.json()["evidence_payload"]["source_fingerprint"]
    assert len(portfolio_db_session.execute(select(TradeReview)).scalars().all()) == 1


def test_create_trade_review_keeps_better_snapshot_when_refresh_regresses(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    _add_closed_portfolio(portfolio_db_session)
    _add_raw_rows(portfolio_db_session)
    first = portfolio_db_client.post("/portfolio/42/review")

    for row in portfolio_db_session.execute(select(StockRawData)).scalars().all():
        portfolio_db_session.delete(row)
    portfolio_db_session.commit()
    second = portfolio_db_client.post("/portfolio/42/review")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.json()["evidence_payload"]["market_snapshot"]["quality"]["row_count"] == 5


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
    assert [event["event_type"] for event in events] == ["initial_entry", "partial_exit"]
    assert events[0]["source_portfolio_id"] == 42
    assert events[0]["quantity"] == 100
    assert events[1]["source_portfolio_id"] == closed_id
    assert events[1]["quantity"] == 40
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


def test_list_closed_lifecycles_returns_complete_group_with_chronological_exit_labels(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add_all([
        UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-complete-lifecycle",
            symbol="2330.TW",
            entry_price=910,
            quantity=40,
            entry_date=date(2026, 1, 1),
            is_active=False,
            exit_date=date(2026, 1, 8),
            exit_price=960,
            exit_quantity=40,
            realized_pnl=2000,
            realized_return_pct=5.49,
            holding_days=7,
        ),
        UserPortfolio(
            id=43,
            user_id=1,
            position_group_id="group-complete-lifecycle",
            symbol="2330.TW",
            entry_price=910,
            quantity=60,
            entry_date=date(2026, 1, 1),
            is_active=False,
            exit_date=date(2026, 1, 12),
            exit_price=940,
            exit_quantity=60,
            realized_pnl=1800,
            realized_return_pct=3.30,
            holding_days=11,
        ),
    ])
    portfolio_db_session.add_all([
        PositionEvent(
            id=101,
            user_id=1,
            position_group_id="group-complete-lifecycle",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=80,
            fees=0,
            taxes=0,
            source_portfolio_id=43,
            source="user_recorded_at_event_time",
        ),
        PositionEvent(
            id=102,
            user_id=1,
            position_group_id="group-complete-lifecycle",
            symbol="2330.TW",
            event_type="add_entry",
            event_date=date(2026, 1, 4),
            price=950,
            quantity=20,
            fees=0,
            taxes=0,
            source_portfolio_id=43,
            reason_category="plan_execution",
            reason_code="planned_scale_in",
            plan_adherence="yes",
            confidence_level="high",
            source="user_recorded_at_event_time",
        ),
        PositionEvent(
            id=103,
            user_id=1,
            position_group_id="group-complete-lifecycle",
            symbol="2330.TW",
            event_type="partial_exit",
            event_date=date(2026, 1, 8),
            price=960,
            quantity=40,
            fees=20,
            taxes=30,
            source_portfolio_id=42,
            reason_category="risk_control",
            reason_code="profit_protection",
            plan_adherence="yes",
            confidence_level="high",
            source="user_recorded_at_event_time",
        ),
        PositionEvent(
            id=104,
            user_id=1,
            position_group_id="group-complete-lifecycle",
            symbol="2330.TW",
            event_type="full_exit",
            event_date=date(2026, 1, 12),
            price=940,
            quantity=60,
            fees=20,
            taxes=30,
            source_portfolio_id=43,
            reason_category="technical",
            reason_code="support_broken",
            plan_adherence="partial",
            confidence_level="medium",
            source="user_recorded_at_event_time",
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/closed-lifecycles")

    assert resp.status_code == 200
    assert len(resp.json()) == 1
    lifecycle = resp.json()[0]
    assert lifecycle["lifecycle_start_date"] == "2026-01-01"
    assert lifecycle["lifecycle_end_date"] == "2026-01-12"
    assert lifecycle["initial_entry_price"] == 900.0
    assert lifecycle["entry_event_count"] == 2
    assert lifecycle["add_entry_count"] == 1
    assert lifecycle["exit_event_count"] == 2
    assert lifecycle["total_closed_quantity"] == 100
    assert lifecycle["total_realized_pnl"] == 3800.0
    assert [batch["display_label"] for batch in lifecycle["exit_batches"]] == ["第 1 次減碼", "最終出清"]
    assert lifecycle["exit_batches"][0]["reason_code"] == "profit_protection"
    assert lifecycle["exit_batches"][1]["plan_adherence"] == "partial"


def test_list_closed_lifecycles_excludes_group_that_still_has_an_active_position(
    portfolio_db_client: TestClient,
    portfolio_db_session: Session,
):
    portfolio_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    portfolio_db_session.add_all([
        UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-still-open",
            symbol="2330.TW",
            entry_price=900,
            quantity=60,
            entry_date=date(2026, 1, 1),
            is_active=True,
        ),
        UserPortfolio(
            id=43,
            user_id=1,
            position_group_id="group-still-open",
            symbol="2330.TW",
            entry_price=900,
            quantity=40,
            entry_date=date(2026, 1, 1),
            is_active=False,
            exit_date=date(2026, 1, 8),
            exit_price=960,
            exit_quantity=40,
            realized_pnl=2400,
            realized_return_pct=6.67,
            holding_days=7,
        ),
    ])
    portfolio_db_session.add_all([
        PositionEvent(
            user_id=1,
            position_group_id="group-still-open",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=100,
            fees=0,
            taxes=0,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ),
        PositionEvent(
            user_id=1,
            position_group_id="group-still-open",
            symbol="2330.TW",
            event_type="partial_exit",
            event_date=date(2026, 1, 8),
            price=960,
            quantity=40,
            fees=0,
            taxes=0,
            source_portfolio_id=43,
            source="user_recorded_at_event_time",
        ),
    ])
    portfolio_db_session.commit()

    resp = portfolio_db_client.get("/portfolio/closed-lifecycles")

    assert resp.status_code == 200
    assert resp.json() == []


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
