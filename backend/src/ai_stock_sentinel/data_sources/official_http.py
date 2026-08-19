from __future__ import annotations

from typing import Any

from curl_cffi import requests as curl_requests


def official_request_get(url: str, **kwargs: Any) -> Any:
    """Call official market endpoints through libcurl with TLS verification enabled."""

    return curl_requests.get(url, **kwargs)


__all__ = ["official_request_get"]
