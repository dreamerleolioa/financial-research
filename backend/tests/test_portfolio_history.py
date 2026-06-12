# backend/tests/test_portfolio_history.py
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.db.models import DailyAnalysisLog, UserPortfolio
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.user_models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


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


def test_history_prefers_position_risk_language_snapshot():
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2330.TW"

    mock_record = MagicMock()
    mock_record.record_date = date(2026, 3, 10)
    mock_record.signal_confidence = 72.5
    mock_record.action_tag = "Hold"
    mock_record.recommended_action = "Exit"
    mock_record.indicators = {
        "position_risk_language": {
            "risk_state": "elevated",
            "risk_state_label": "風險狀態升高",
            "discipline_triggers": ["收盤跌破風險控制參考價"],
            "risk_control_reference": {"reference_price": 900},
        },
    }
    mock_record.final_verdict = "中性"
    mock_record.prev_action_tag = None
    mock_record.prev_confidence = None

    client = _make_client(portfolio=mock_portfolio, records=[mock_record], total=1)
    resp = client.get("/portfolio/1/history")

    assert resp.status_code == 200
    record = resp.json()["records"][0]
    assert record["risk_state"] == "elevated"
    assert record["risk_state_label"] == "風險狀態升高"
    assert record["discipline_triggers"] == ["收盤跌破風險控制參考價"]
    assert record["risk_control_reference"] == {"reference_price": 900}
    assert record["compatibility_source"] == "position_risk_language"
    assert record["recommended_action"] == "Exit"


def test_history_falls_back_to_legacy_action_for_old_rows():
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2330.TW"

    mock_record = MagicMock()
    mock_record.record_date = date(2026, 3, 10)
    mock_record.signal_confidence = 72.5
    mock_record.action_tag = "Exit"
    mock_record.recommended_action = "Exit"
    mock_record.indicators = {}
    mock_record.final_verdict = "已觸發防守條件"
    mock_record.prev_action_tag = None
    mock_record.prev_confidence = None

    client = _make_client(portfolio=mock_portfolio, records=[mock_record], total=1)
    resp = client.get("/portfolio/1/history")

    assert resp.status_code == 200
    record = resp.json()["records"][0]
    assert record["risk_state"] == "critical"
    assert record["risk_state_label"] == "防守條件已觸發"
    assert record["compatibility_source"] == "legacy_recommended_action"


def test_history_returns_records_for_closed_portfolio():
    from datetime import date
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2330.TW"
    mock_portfolio.is_active = False

    mock_record = MagicMock()
    mock_record.record_date = date(2026, 3, 10)
    mock_record.signal_confidence = 72.5
    mock_record.action_tag = "Exit"
    mock_record.recommended_action = "出場"
    mock_record.indicators = {}
    mock_record.final_verdict = "已觸發出場條件"
    mock_record.prev_action_tag = "Hold"
    mock_record.prev_confidence = 68.0

    client = _make_client(portfolio=mock_portfolio, records=[mock_record], total=1)
    resp = client.get("/portfolio/1/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "2330.TW"
    assert data["records"][0]["action_tag"] == "Exit"


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


@pytest.fixture()
def portfolio_history_db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[User.__table__, UserPortfolio.__table__, DailyAnalysisLog.__table__],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture()
def portfolio_history_client(portfolio_history_db_session: Session) -> TestClient:
    api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    api.app.dependency_overrides[get_db] = lambda: portfolio_history_db_session
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(get_current_user, None)
        api.app.dependency_overrides.pop(get_db, None)


def _persist_user(session: Session) -> None:
    session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    session.flush()


def _persist_portfolio(
    session: Session,
    *,
    portfolio_id: int,
    entry_date: date,
    exit_date: date | None,
    is_active: bool,
) -> UserPortfolio:
    portfolio = UserPortfolio(
        id=portfolio_id,
        user_id=1,
        symbol="2330.TW",
        entry_price=900,
        quantity=100,
        entry_date=entry_date,
        exit_date=exit_date,
        is_active=is_active,
    )
    session.add(portfolio)
    session.flush()
    return portfolio


def _persist_log(
    session: Session,
    record_date: date,
    action_tag: str,
    *,
    indicators: dict | None = None,
    recommended_action: str | None = None,
) -> None:
    session.add(DailyAnalysisLog(
        user_id=1,
        symbol="2330.TW",
        record_date=record_date,
        signal_confidence=70,
        action_tag=action_tag,
        recommended_action=recommended_action if recommended_action is not None else action_tag,
        indicators=indicators or {},
        final_verdict=action_tag,
    ))


def test_history_scopes_closed_portfolio_to_own_holding_window(
    portfolio_history_client: TestClient,
    portfolio_history_db_session: Session,
):
    _persist_user(portfolio_history_db_session)
    closed = _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=101,
        entry_date=date(2026, 1, 1),
        exit_date=date(2026, 1, 31),
        is_active=False,
    )
    _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=102,
        entry_date=date(2026, 3, 1),
        exit_date=None,
        is_active=True,
    )
    _persist_log(portfolio_history_db_session, date(2025, 12, 31), "BeforeEntry")
    _persist_log(portfolio_history_db_session, date(2026, 1, 15), "ClosedWindow")
    _persist_log(portfolio_history_db_session, date(2026, 2, 1), "AfterExit")
    _persist_log(portfolio_history_db_session, date(2026, 3, 10), "NewEntry")
    portfolio_history_db_session.commit()

    response = portfolio_history_client.get(f"/portfolio/{closed.id}/history")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert [record["action_tag"] for record in data["records"]] == ["ClosedWindow"]


def test_history_scopes_active_reentry_to_post_entry_logs(
    portfolio_history_client: TestClient,
    portfolio_history_db_session: Session,
):
    _persist_user(portfolio_history_db_session)
    _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=201,
        entry_date=date(2026, 1, 1),
        exit_date=date(2026, 1, 31),
        is_active=False,
    )
    active = _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=202,
        entry_date=date(2026, 3, 1),
        exit_date=None,
        is_active=True,
    )
    _persist_log(portfolio_history_db_session, date(2026, 1, 15), "ClosedWindow")
    _persist_log(portfolio_history_db_session, date(2026, 2, 20), "BetweenHoldings")
    _persist_log(portfolio_history_db_session, date(2026, 3, 10), "ActiveWindow")
    portfolio_history_db_session.commit()

    response = portfolio_history_client.get(f"/portfolio/{active.id}/history")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert [record["action_tag"] for record in data["records"]] == ["ActiveWindow"]


def test_latest_history_returns_null_for_reentry_with_only_stale_logs(
    portfolio_history_client: TestClient,
    portfolio_history_db_session: Session,
):
    _persist_user(portfolio_history_db_session)
    _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=301,
        entry_date=date(2026, 1, 1),
        exit_date=date(2026, 1, 31),
        is_active=False,
    )
    active = _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=302,
        entry_date=date(2026, 3, 1),
        exit_date=None,
        is_active=True,
    )
    _persist_log(portfolio_history_db_session, date(2026, 1, 15), "ClosedWindow")
    _persist_log(portfolio_history_db_session, date(2026, 2, 20), "BetweenHoldings")
    portfolio_history_db_session.commit()

    response = portfolio_history_client.get("/portfolio/latest-history")

    assert response.status_code == 200
    assert response.json() == {str(active.id): None}


def test_latest_history_returns_post_entry_log_for_reentry(
    portfolio_history_client: TestClient,
    portfolio_history_db_session: Session,
):
    _persist_user(portfolio_history_db_session)
    _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=401,
        entry_date=date(2026, 1, 1),
        exit_date=date(2026, 1, 31),
        is_active=False,
    )
    active = _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=402,
        entry_date=date(2026, 3, 1),
        exit_date=None,
        is_active=True,
    )
    _persist_log(portfolio_history_db_session, date(2026, 1, 15), "ClosedWindow")
    _persist_log(portfolio_history_db_session, date(2026, 3, 10), "ActiveWindow")
    portfolio_history_db_session.commit()

    response = portfolio_history_client.get("/portfolio/latest-history")

    assert response.status_code == 200
    data = response.json()
    assert data[str(active.id)]["record_date"] == "2026-03-10"
    assert data[str(active.id)]["action_tag"] == "ActiveWindow"


def test_latest_history_returns_risk_language_from_real_mapping_row(
    portfolio_history_client: TestClient,
    portfolio_history_db_session: Session,
):
    _persist_user(portfolio_history_db_session)
    active = _persist_portfolio(
        portfolio_history_db_session,
        portfolio_id=501,
        entry_date=date(2026, 3, 1),
        exit_date=None,
        is_active=True,
    )
    _persist_log(
        portfolio_history_db_session,
        date(2026, 3, 10),
        "Hold",
        recommended_action="Exit",
        indicators={
            "position_risk_language": {
                "risk_state": "elevated",
                "risk_state_label": "風險狀態升高",
                "discipline_triggers": ["收盤跌破風險控制參考價"],
                "risk_control_reference": {"reference_price": 900},
            },
        },
    )
    portfolio_history_db_session.commit()

    response = portfolio_history_client.get("/portfolio/latest-history")

    assert response.status_code == 200
    record = response.json()[str(active.id)]
    assert record["risk_state"] == "elevated"
    assert record["risk_state_label"] == "風險狀態升高"
    assert record["discipline_triggers"] == ["收盤跌破風險控制參考價"]
    assert record["risk_control_reference"] == {"reference_price": 900}
    assert record["compatibility_source"] == "position_risk_language"
    assert record["recommended_action"] == "Exit"
