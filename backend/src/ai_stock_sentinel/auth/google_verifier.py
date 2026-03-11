from __future__ import annotations

from dataclasses import dataclass

import os

import google.auth.transport.requests
from google.oauth2 import id_token as google_id_token
import httpx


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


def exchange_google_auth_code(code: str, redirect_uri: str) -> GoogleUserInfo:
    """Exchange a Google authorization code for user info.

    Raises ValueError if the exchange fails.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set")

    # Exchange auth code for tokens
    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if token_resp.status_code != 200:
        raise ValueError(f"Token exchange failed: {token_resp.text}")

    token_data = token_resp.json()
    id_token_str = token_data.get("id_token")
    if not id_token_str:
        raise ValueError("No id_token in token response")

    # Verify the id_token we received
    return verify_google_id_token(id_token_str)
