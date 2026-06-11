"""Shared FinMind HTTP client with process-wide quota and response caching."""
from __future__ import annotations

import copy
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ai_stock_sentinel.data_sources.finmind_token import get_token_manager

logger = logging.getLogger(__name__)

FINMIND_DATA_API = "https://api.finmindtrade.com/api/v4/data"
DEFAULT_TOKEN_REQUESTS_PER_HOUR = 600
DEFAULT_ANONYMOUS_REQUESTS_PER_HOUR = 300
DEFAULT_RESPONSE_CACHE_TTL_SECONDS = 3600


@dataclass
class FinMindClientError(Exception):
    code: str
    message: str
    dataset: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


class FinMindHourlyRequestLedger:
    """Thread-safe per-hour request ledger keyed by token identity."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._usage: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    def reserve(self, *, identity: str, limit: int, amount: int = 1) -> int:
        if amount <= 0:
            return self.remaining(identity=identity, limit=limit)
        bucket = self._current_bucket()
        with self._lock:
            key = (identity, bucket)
            used = self._usage.get(key, 0)
            if used + amount > limit:
                return -1
            self._usage[key] = used + amount
            return limit - self._usage[key]

    def remaining(self, *, identity: str, limit: int) -> int:
        bucket = self._current_bucket()
        with self._lock:
            return max(limit - self._usage.get((identity, bucket), 0), 0)

    def reset(self) -> None:
        with self._lock:
            self._usage.clear()

    def _current_bucket(self) -> int:
        return int(self._clock() // 3600)


class FinMindResponseCache:
    """Small in-process cache for identical FinMind data requests."""

    def __init__(self, *, ttl_seconds: int = DEFAULT_RESPONSE_CACHE_TTL_SECONDS, clock: Callable[[], float] | None = None) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.time
        self._items: dict[tuple[tuple[str, str], ...], tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[tuple[str, str], ...]) -> list[dict[str, Any]] | None:
        now = self._clock()
        with self._lock:
            cached = self._items.get(key)
            if cached is None:
                return None
            expires_at, data = cached
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            return copy.deepcopy(data)

    def set(self, key: tuple[tuple[str, str], ...], data: list[dict[str, Any]]) -> None:
        expires_at = self._clock() + self._ttl_seconds
        with self._lock:
            self._items[key] = (expires_at, copy.deepcopy(data))

    def reset(self) -> None:
        with self._lock:
            self._items.clear()


_DEFAULT_LEDGER = FinMindHourlyRequestLedger()
_DEFAULT_CACHE = FinMindResponseCache()


class FinMindClient:
    """Central access point for FinMind data requests.

    The client intentionally owns token injection, response parsing, per-hour
    admission control, and short-lived response caching so individual providers
    cannot accidentally multiply FinMind traffic independently.
    """

    def __init__(
        self,
        *,
        api_token: str = "",
        request_get: Callable[..., Any] | None = None,
        ledger: FinMindHourlyRequestLedger | None = None,
        cache: FinMindResponseCache | None = None,
        token_request_limit: int | None = None,
        anonymous_request_limit: int | None = None,
    ) -> None:
        self._static_token = api_token
        self._request_get = request_get
        self._ledger = ledger or _DEFAULT_LEDGER
        self._cache = cache or _DEFAULT_CACHE
        self._token_request_limit = token_request_limit or _int_env(
            "FINMIND_REQUESTS_PER_HOUR",
            DEFAULT_TOKEN_REQUESTS_PER_HOUR,
        )
        self._anonymous_request_limit = anonymous_request_limit or _int_env(
            "FINMIND_ANONYMOUS_REQUESTS_PER_HOUR",
            DEFAULT_ANONYMOUS_REQUESTS_PER_HOUR,
        )

    @property
    def uses_static_token(self) -> bool:
        return bool(self._static_token)

    def fetch_data(
        self,
        *,
        dataset: str,
        data_id: str,
        start_date: str,
        end_date: str,
        timeout: int = 15,
    ) -> list[dict[str, Any]]:
        token = self._token()
        params: dict[str, Any] = {
            "dataset": dataset,
            "data_id": data_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        if token:
            params["token"] = token

        cache_key = _cache_key(params)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[FinMindClient] cache hit dataset=%s data_id=%s", dataset, data_id)
            return cached

        request_get = self._request_get or _default_request_get(dataset)
        identity = _token_identity(token)
        limit = self._token_request_limit if token else self._anonymous_request_limit
        remaining = self._ledger.reserve(identity=identity, limit=limit)
        if remaining < 0:
            raise FinMindClientError(
                code="quota_exceeded",
                message=f"FinMind hourly request budget exhausted before dataset={dataset}",
                dataset=dataset,
                status_code=429,
            )

        try:
            response = request_get(FINMIND_DATA_API, params=params, timeout=timeout)
        except Exception as exc:
            raise FinMindClientError(
                code="request_error",
                message=f"FinMind request failed for dataset={dataset}: {exc}",
                dataset=dataset,
            ) from exc

        status_code = getattr(response, "status_code", None)
        if status_code == 402:
            raise FinMindClientError(
                code="quota_or_token_error",
                message=f"FinMind returned HTTP 402 for dataset={dataset}",
                dataset=dataset,
                status_code=402,
            )

        try:
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise FinMindClientError(
                code="response_error",
                message=f"FinMind response could not be parsed for dataset={dataset}: {exc}",
                dataset=dataset,
                status_code=status_code,
            ) from exc

        if body.get("status") != 200:
            message = str(body.get("msg") or "unknown error")
            raise FinMindClientError(
                code="api_error",
                message=message,
                dataset=dataset,
                status_code=status_code,
            )

        data = body.get("data")
        rows = list(data) if isinstance(data, list) else []
        self._cache.set(cache_key, rows)
        logger.debug(
            "[FinMindClient] fetched dataset=%s data_id=%s remaining_hourly_budget=%s",
            dataset,
            data_id,
            remaining,
        )
        return rows

    def _token(self) -> str:
        return self._static_token or get_token_manager().token


def reset_default_finmind_client_state() -> None:
    """Reset process-wide client state for tests and one-off maintenance."""
    _DEFAULT_LEDGER.reset()
    _DEFAULT_CACHE.reset()


def _default_request_get(dataset: str) -> Callable[..., Any]:
    try:
        import requests
    except ImportError as exc:
        raise FinMindClientError(
            code="missing_dependency",
            message="requests package is not installed",
            dataset=dataset,
        ) from exc
    return requests.get


def _cache_key(params: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in params.items() if key != "token"))


def _token_identity(token: str) -> str:
    if not token:
        return "anonymous"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"token:{digest}"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default
    return value if value > 0 else default


__all__ = [
    "DEFAULT_ANONYMOUS_REQUESTS_PER_HOUR",
    "DEFAULT_RESPONSE_CACHE_TTL_SECONDS",
    "DEFAULT_TOKEN_REQUESTS_PER_HOUR",
    "FINMIND_DATA_API",
    "FinMindClient",
    "FinMindClientError",
    "FinMindHourlyRequestLedger",
    "FinMindResponseCache",
    "reset_default_finmind_client_state",
]
