from __future__ import annotations

from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, status

from ai_stock_sentinel.config import load_settings


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def require_daily_radar_internal_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    configured_token = load_settings().daily_radar_internal_token
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Daily Radar internal token is not configured",
        )

    candidate_tokens = [
        token
        for token in (_extract_bearer_token(authorization), x_internal_token)
        if token is not None
    ]
    if not candidate_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Daily Radar internal token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if any(compare_digest(token, configured_token) for token in candidate_tokens):
        return None

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid Daily Radar internal token",
    )


__all__ = ["require_daily_radar_internal_auth"]
