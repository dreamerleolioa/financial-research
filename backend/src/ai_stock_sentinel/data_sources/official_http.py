from __future__ import annotations

import time
from typing import Any

from curl_cffi import requests as curl_requests


_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 0.25
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, *range(500, 600)})
_RETRYABLE_EXCEPTIONS = (
    curl_requests.exceptions.Timeout,
    curl_requests.exceptions.ConnectionError,
)


def official_request_get(url: str, **kwargs: Any) -> Any:
    """Call official market endpoints with bounded retries and TLS verification enabled."""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = curl_requests.get(url, **kwargs)
        except _RETRYABLE_EXCEPTIONS:
            if attempt >= _MAX_ATTEMPTS:
                raise
            _sleep_before_retry(attempt)
            continue

        if _is_retryable_response(response) and attempt < _MAX_ATTEMPTS:
            _sleep_before_retry(attempt)
            continue
        return response

    raise RuntimeError("official request retry loop exhausted")


def _is_retryable_response(response: Any) -> bool:
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, bool):
        return False
    try:
        return int(status_code) in _RETRYABLE_STATUS_CODES
    except (TypeError, ValueError):
        return False


def _sleep_before_retry(attempt: int) -> None:
    time.sleep(_BACKOFF_SECONDS * (2 ** (attempt - 1)))


__all__ = ["official_request_get"]
