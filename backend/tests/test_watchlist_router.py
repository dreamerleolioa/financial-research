from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import UserWatchlist
from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.user_models.user import User
from ai_stock_sentinel.watchlist import router as watchlist_router_module


@pytest.fixture()
def watchlist_db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            UserWatchlist.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture()
def watchlist_client(watchlist_db_session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(watchlist_router_module, "check_symbol_exists", lambda _symbol: True)
    monkeypatch.setattr(watchlist_router_module, "resolve_symbol_name", lambda symbol: "台積電" if symbol == "2330.TW" else None)
    api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    api.app.dependency_overrides[get_db] = lambda: watchlist_db_session
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(get_current_user, None)
        api.app.dependency_overrides.pop(get_db, None)


def test_create_and_list_watchlist_item(
    watchlist_client: TestClient,
    watchlist_db_session: Session,
):
    watchlist_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    watchlist_db_session.commit()

    created = watchlist_client.post("/watchlist", json={
        "symbol": "2330.tw",
        "notes": "等待拉回",
    })

    assert created.status_code == 201
    assert created.json()["symbol"] == "2330.TW"
    assert created.json()["name"] == "台積電"

    listed = watchlist_client.get("/watchlist")
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_create_watchlist_item_is_idempotent_and_updates_notes(
    watchlist_client: TestClient,
    watchlist_db_session: Session,
):
    watchlist_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    watchlist_db_session.commit()

    first = watchlist_client.post("/watchlist", json={
        "symbol": "2330.TW",
        "notes": "第一版",
    })
    second = watchlist_client.post("/watchlist", json={
        "symbol": "2330.tw",
        "notes": "更新觀察原因",
    })

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["notes"] == "更新觀察原因"
    rows = watchlist_db_session.execute(select(UserWatchlist)).scalars().all()
    assert len(rows) == 1


def test_watchlist_items_are_user_scoped(
    watchlist_client: TestClient,
    watchlist_db_session: Session,
):
    watchlist_db_session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    watchlist_db_session.add(User(id=2, google_sub="user-2", email="other@example.com"))
    watchlist_db_session.add(UserWatchlist(id=1, user_id=1, symbol="2330.TW", notes=None))
    watchlist_db_session.add(UserWatchlist(id=2, user_id=2, symbol="2454.TW", notes=None))
    watchlist_db_session.commit()

    listed = watchlist_client.get("/watchlist")
    assert listed.status_code == 200
    assert [row["symbol"] for row in listed.json()] == ["2330.TW"]

    foreign_delete = watchlist_client.delete("/watchlist/2")
    assert foreign_delete.status_code == 404

    own_delete = watchlist_client.delete("/watchlist/1")
    assert own_delete.status_code == 204
    remaining = watchlist_db_session.execute(select(UserWatchlist)).scalars().all()
    assert [row.symbol for row in remaining] == ["2454.TW"]
