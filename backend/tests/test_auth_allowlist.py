from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.auth import router as auth_router
from ai_stock_sentinel.auth.allowlist import AllowedGoogleAccount
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.auth.google_verifier import GoogleUserInfo
from ai_stock_sentinel.auth.jwt_handler import create_access_token
from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.user_models.user import User


@pytest.fixture
def auth_app(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-allowlist-signing-key-not-a-production-secret")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, AllowedGoogleAccount.__table__])
    app = FastAPI()
    app.include_router(auth_router.router)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database

    @app.get("/protected")
    def protected(user=Depends(get_current_user)):
        return {"user_id": user.id}

    info = GoogleUserInfo(sub="owner-sub", email="owner@gmail.com", name="Owner", picture=None)
    monkeypatch.setattr(auth_router, "verify_google_id_token", lambda _: info)
    monkeypatch.setattr(auth_router, "exchange_google_auth_code", lambda *_: info)
    yield TestClient(app), engine, info
    engine.dispose()


LOGIN_CASES = [("/auth/google", {"id_token": "opaque-test-token"}),
               ("/auth/google/code", {"code": "opaque-code", "redirect_uri": "https://example.com/login/callback"})]


@pytest.mark.parametrize("path,body", LOGIN_CASES)
def test_unapproved_login_is_rejected_before_creating_user_or_jwt(auth_app, path, body, caplog):
    client, engine, _ = auth_app
    with caplog.at_level(logging.WARNING):
        response = client.post(path, json=body)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_NOT_ALLOWLISTED"
    assert "access_token" not in response.json()
    with Session(engine) as db:
        assert db.scalar(select(User)) is None
    assert "auth_access_denied" in caplog.text
    assert "owner@gmail.com" not in caplog.text
    assert "owner-sub" not in caplog.text
    assert "opaque-test-token" not in caplog.text


@pytest.mark.parametrize("path,body", LOGIN_CASES)
def test_allowlisted_google_subject_can_login(auth_app, path, body):
    client, engine, info = auth_app
    with Session(engine) as db:
        db.add(AllowedGoogleAccount(google_sub=info.sub, email=info.email))
        db.commit()
    response = client.post(path, json=body)
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_removing_and_readding_membership_affects_existing_jwt_on_next_request(auth_app):
    client, engine, info = auth_app
    with Session(engine) as db:
        user = User(google_sub=info.sub, email=info.email)
        db.add(user)
        db.flush()
        token = create_access_token(user.id, user.email)
        db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).status_code == 403
    with Session(engine) as db:
        db.add(AllowedGoogleAccount(google_sub=info.sub, email=info.email))
        db.commit()
    assert client.get("/auth/me", headers=headers).status_code == 200
    with Session(engine) as db:
        db.execute(delete(AllowedGoogleAccount))
        db.commit()
    assert client.get("/protected", headers=headers).status_code == 403
    assert client.get("/auth/me", headers=headers).status_code == 403


def test_matching_email_cannot_impersonate_an_allowlisted_subject(auth_app):
    client, engine, info = auth_app
    with Session(engine) as db:
        db.add(AllowedGoogleAccount(google_sub="different-sub", email=info.email))
        db.commit()
    assert client.post("/auth/google", json={"id_token": "token"}).status_code == 403


def test_existing_unapproved_user_is_not_updated_during_login(auth_app):
    client, engine, info = auth_app
    with Session(engine) as db:
        db.add(User(google_sub=info.sub, email=info.email, name="Unchanged"))
        db.commit()
    assert client.post("/auth/google", json={"id_token": "token"}).status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(User)).name == "Unchanged"


def test_inactive_user_cannot_get_new_jwt_even_if_allowlisted(auth_app):
    client, engine, info = auth_app
    with Session(engine) as db:
        db.add(User(google_sub=info.sub, email=info.email, is_active=False))
        db.add(AllowedGoogleAccount(google_sub=info.sub, email=info.email))
        db.commit()
    assert client.post("/auth/google", json={"id_token": "token"}).status_code == 401


def test_allowlist_database_failure_does_not_allow_login(auth_app):
    client, engine, _ = auth_app
    AllowedGoogleAccount.__table__.drop(engine)
    response = client.post("/auth/google", json={"id_token": "token"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ACCESS_CHECK_UNAVAILABLE"


@pytest.mark.parametrize("path,body", LOGIN_CASES)
def test_membership_does_not_bypass_google_verification(auth_app, monkeypatch, path, body):
    client, engine, info = auth_app
    with Session(engine) as db:
        db.add(AllowedGoogleAccount(google_sub=info.sub, email=info.email))
        db.commit()

    def invalid(*_args):
        raise ValueError("invalid Google identity")

    monkeypatch.setattr(auth_router, "verify_google_id_token", invalid)
    monkeypatch.setattr(auth_router, "exchange_google_auth_code", invalid)
    assert client.post(path, json=body).status_code == 401
