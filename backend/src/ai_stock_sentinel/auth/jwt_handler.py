from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt

_ALGORITHM = "HS256"
_EXPIRE_DAYS = 7


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is not set")
    return secret


def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
