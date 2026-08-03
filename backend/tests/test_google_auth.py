from __future__ import annotations

import pytest

from ai_stock_sentinel.auth import google_verifier
from ai_stock_sentinel.auth.google_verifier import exchange_google_auth_code, validate_google_redirect_uri


def test_google_redirect_uri_uses_explicit_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URIS",
        "https://example.github.io/financial-research/login/callback,http://localhost:5173/login/callback",
    )

    validate_google_redirect_uri("https://example.github.io/financial-research/login/callback")

    with pytest.raises(ValueError, match="redirect_uri is not allowed"):
        validate_google_redirect_uri("https://example.github.io/other/login/callback")


def test_google_redirect_uri_falls_back_to_configured_cors_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URIS", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://example.github.io,http://localhost:5173")

    validate_google_redirect_uri("https://example.github.io/financial-research/login/callback")


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://attacker.example/login/callback",
        "https://example.github.io/financial-research/not-login-callback",
        "https://example.github.io/financial-research/login/callback?next=evil",
        "https://example.github.io/../login/callback",
        "javascript:alert(1)",
    ],
)
def test_google_redirect_uri_rejects_untrusted_or_malformed_values(
    monkeypatch: pytest.MonkeyPatch,
    redirect_uri: str,
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URIS", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://example.github.io")

    with pytest.raises(ValueError, match="Invalid Google redirect_uri|redirect_uri is not allowed"):
        validate_google_redirect_uri(redirect_uri)


def test_google_code_exchange_rejects_redirect_uri_before_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URIS", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://example.github.io")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Google token endpoint must not be called")

    monkeypatch.setattr(google_verifier.httpx, "post", fail_if_called)

    with pytest.raises(ValueError, match="redirect_uri is not allowed"):
        exchange_google_auth_code("attacker-code", "https://attacker.example/login/callback")
