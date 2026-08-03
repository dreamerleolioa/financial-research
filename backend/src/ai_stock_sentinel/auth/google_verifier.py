from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import google.auth.transport.requests
from google.oauth2 import id_token as google_id_token
import httpx


DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:5174"


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
    validate_google_redirect_uri(redirect_uri)
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


def validate_google_redirect_uri(redirect_uri: str) -> None:
    """Allow only the app callback on an explicitly trusted URI or CORS origin."""
    if not redirect_uri or len(redirect_uri) > 2048:
        raise ValueError("Invalid Google redirect_uri")

    explicit_uris = {
        uri.strip()
        for uri in os.environ.get("GOOGLE_OAUTH_REDIRECT_URIS", "").split(",")
        if uri.strip()
    }
    if explicit_uris:
        if redirect_uri not in explicit_uris:
            raise ValueError("Google redirect_uri is not allowed")
        return

    parsed = urlsplit(redirect_uri)
    path_segments = parsed.path.split("/")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or ".." in path_segments
        or not parsed.path.endswith("/login/callback")
    ):
        raise ValueError("Invalid Google redirect_uri")

    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    allowed_origins = {
        configured_origin.strip().rstrip("/").lower()
        for configured_origin in os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if configured_origin.strip()
    }
    if origin not in allowed_origins:
        raise ValueError("Google redirect_uri is not allowed")
