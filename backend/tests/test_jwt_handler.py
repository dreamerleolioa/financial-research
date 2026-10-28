from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from ai_stock_sentinel.auth.jwt_handler import create_access_token, decode_access_token


@pytest.fixture
def signing_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    secret = "test-jwt-signing-key-not-a-production-secret"
    monkeypatch.setenv("JWT_SECRET", secret)
    return secret


def test_access_token_round_trip_preserves_identity(signing_secret: str) -> None:
    token = create_access_token(42, "owner@example.com")

    payload = decode_access_token(token)

    assert payload["sub"] == "42"
    assert payload["email"] == "owner@example.com"
    assert payload["exp"] > datetime.now(timezone.utc).timestamp()


def test_expired_access_token_is_rejected(signing_secret: str) -> None:
    token = jwt.encode(
        {"sub": "42", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        signing_secret,
        algorithm="HS256",
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_access_token_signed_with_another_key_is_rejected(signing_secret: str) -> None:
    token = jwt.encode({"sub": "42"}, "another-signing-key-not-a-production-secret", algorithm="HS256")

    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token)


def test_unsigned_access_token_is_rejected(signing_secret: str) -> None:
    token = jwt.encode({"sub": "42"}, None, algorithm="none")

    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_access_token(token)


def test_deeply_nested_header_is_rejected_as_invalid_token(signing_secret: str) -> None:
    header = base64.urlsafe_b64encode(b"[" * 200_000).rstrip(b"=").decode("ascii")

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(f"{header}.e30.c2ln")
