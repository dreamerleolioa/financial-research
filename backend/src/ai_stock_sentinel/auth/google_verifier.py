from __future__ import annotations

from dataclasses import dataclass

import os

import google.auth.transport.requests
from google.oauth2 import id_token as google_id_token


@dataclass
class GoogleUserInfo:
    sub: str
    email: str
    name: str | None
    picture: str | None


def verify_google_id_token(token: str) -> GoogleUserInfo:
    """Verify a Google id_token and return user info.

    Raises ValueError if the token is invalid.
    """
    request = google.auth.transport.requests.Request()
    try:
        audience = os.environ.get("GOOGLE_CLIENT_ID")
        idinfo = google_id_token.verify_oauth2_token(token, request, audience=audience)
    except Exception as exc:
        raise ValueError(f"Invalid Google id_token: {exc}") from exc

    return GoogleUserInfo(
        sub=idinfo["sub"],
        email=idinfo["email"],
        name=idinfo.get("name"),
        picture=idinfo.get("picture"),
    )
