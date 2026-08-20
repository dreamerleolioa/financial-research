from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, timedelta
from time import monotonic
from typing import Any, Protocol

from curl_cffi import requests as curl_requests

from ai_stock_sentinel.data_sources.official_http import official_request_get


TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"
TWSE_T86_FALLBACK_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_3I_URL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
_MAX_REQUEST_TIMEOUT_SECONDS = 10
_RETRYABLE_TRANSPORT_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    curl_requests.exceptions.Timeout,
    curl_requests.exceptions.ConnectionError,
)
_RETRYABLE_INSTITUTIONAL_EXCEPTIONS = _RETRYABLE_TRANSPORT_EXCEPTIONS + (
    json.JSONDecodeError,
)
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, *range(500, 600)})

RequestGetter = Callable[..., Any]
logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class InstitutionalEvidenceResult:
    payloads_by_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)


class InstitutionalEvidenceProvider(Protocol):
    def fetch(
        self,
        symbols: Sequence[str],
        *,
        run_date: date,
    ) -> InstitutionalEvidenceResult:
        ...


class OfficialInstitutionalEvidenceProvider:
    """Build neutral, date-scoped institutional evidence from official batch reports."""

    def __init__(
        self,
        *,
        request_get: RequestGetter | None = None,
        timeout: int = 20,
        recent_market_days: int = 5,
        calendar_window_days: int = 10,
        max_workers: int = 6,
        total_timeout: int = 60,
    ) -> None:
        self._request_get = request_get
        self._timeout = min(max(1, timeout), _MAX_REQUEST_TIMEOUT_SECONDS)
        self._recent_market_days = max(1, recent_market_days)
        self._calendar_window_days = max(0, calendar_window_days)
        self._max_workers = max(1, max_workers)
        self._total_timeout = max(1, total_timeout)

    def fetch(
        self,
        symbols: Sequence[str],
        *,
        run_date: date,
    ) -> InstitutionalEvidenceResult:
        requested = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
        daily_by_symbol: dict[str, dict[date, dict[str, float]]] = {}
        errors: list[dict[str, Any]] = []
        markets = [
            market
            for market, suffix in (("TWSE", ".TW"), ("TPEX", ".TWO"))
            if any(symbol.endswith(suffix) for symbol in requested)
        ]
        if not markets:
            return InstitutionalEvidenceResult()
        required_market_days = min(
            self._recent_market_days,
            self._calendar_window_days + 1,
        )
        market_days: dict[str, set[date]] = {market: set() for market in markets}
        historical_errors: dict[str, list[dict[str, Any]]] = {
            market: [] for market in markets
        }
        deadline = monotonic() + self._total_timeout
        worker_count = min(self._max_workers, len(markets))
        executor = ThreadPoolExecutor(max_workers=worker_count)
        deadline_exhausted = False
        try:
            for offset in range(self._calendar_window_days + 1):
                active_markets = [
                    market
                    for market in markets
                    if len(market_days[market]) < required_market_days
                ]
                if not active_markets:
                    break
                query_date = run_date - timedelta(days=offset)
                for batch_start in range(0, len(active_markets), worker_count):
                    batch_markets = active_markets[
                        batch_start : batch_start + worker_count
                    ]
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        for market in active_markets[batch_start:]:
                            errors.append(_timeout_error(market, query_date))
                        deadline_exhausted = True
                        break
                    request_timeout = min(float(self._timeout), remaining)
                    futures: dict[Future[dict[str, dict[str, float]]], str] = {
                        executor.submit(
                            self._fetch_market_date,
                            market,
                            query_date,
                            request_timeout,
                        ): market
                        for market in batch_markets
                    }
                    done, pending = wait(futures, timeout=remaining)
                    for future in pending:
                        future.cancel()
                        errors.append(_timeout_error(futures[future], query_date))
                    for future in done:
                        market = futures[future]
                        try:
                            rows = future.result()
                        except Exception as exc:
                            error = {
                                "market": market,
                                "query_date": query_date.isoformat(),
                                "error_type": exc.__class__.__name__,
                            }
                            if query_date == run_date or not isinstance(
                                exc,
                                _RETRYABLE_TRANSPORT_EXCEPTIONS,
                            ):
                                errors.append(error)
                            else:
                                historical_errors[market].append(error)
                            continue
                        if rows:
                            market_days[market].add(query_date)
                        for symbol, values in rows.items():
                            if symbol in requested:
                                daily_by_symbol.setdefault(symbol, {})[
                                    query_date
                                ] = values
                    if pending:
                        for market in active_markets[batch_start + worker_count :]:
                            errors.append(_timeout_error(market, query_date))
                        deadline_exhausted = True
                        break
                if deadline_exhausted:
                    break
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        for market in markets:
            if len(market_days[market]) >= required_market_days:
                continue
            errors.extend(historical_errors[market])
            errors.append(
                {
                    "market": market,
                    "error_type": "institutional_evidence_lookback_incomplete",
                    "market_day_count": len(market_days[market]),
                    "required_market_day_count": required_market_days,
                }
            )

        active_dates_by_market = {
            market: sorted(dates, reverse=True)[:required_market_days]
            for market, dates in market_days.items()
        }
        payloads = {
            symbol: _build_payload(
                symbol,
                rows,
                run_date=run_date,
                active_dates=active_dates_by_market[_symbol_market(symbol)],
            )
            for symbol, rows in daily_by_symbol.items()
            if rows
        }
        return InstitutionalEvidenceResult(payloads_by_symbol=payloads, errors=errors)

    def _fetch_market_date(
        self,
        market: str,
        query_date: date,
        timeout: float,
    ) -> dict[str, dict[str, float]]:
        return (
            self._fetch_twse(query_date, timeout=timeout)
            if market == "TWSE"
            else self._fetch_tpex(query_date, timeout=timeout)
        )

    def _fetch_twse(
        self,
        query_date: date,
        *,
        timeout: float,
    ) -> dict[str, dict[str, float]]:
        payload = self._get_json(
            TWSE_T86_URL,
            fallback_url=TWSE_T86_FALLBACK_URL,
            market="TWSE",
            query_date=query_date,
            params={
                "response": "json",
                "date": query_date.strftime("%Y%m%d"),
                "selectType": "ALLBUT0999",
            },
            timeout=timeout,
        )
        if not isinstance(payload, Mapping) or payload.get("stat") != "OK":
            return {}
        rows = payload.get("data")
        if not _is_rows(rows):
            return {}
        result: dict[str, dict[str, float]] = {}
        for row in rows:
            if not _is_row(row) or len(row) <= 18:
                continue
            stock_id = str(row[0]).strip()
            if len(stock_id) != 4 or not stock_id.isdigit():
                continue
            result[f"{stock_id}.TW"] = {
                "foreign": _number(row[4]),
                "trust": _number(row[10]),
                "dealer": _number(row[11]),
                "total": _number(row[18]),
            }
        return result

    def _fetch_tpex(
        self,
        query_date: date,
        *,
        timeout: float,
    ) -> dict[str, dict[str, float]]:
        roc_date = f"{query_date.year - 1911}/{query_date.month:02d}/{query_date.day:02d}"
        payload = self._get_json(
            TPEX_3I_URL,
            market="TPEX",
            query_date=query_date,
            params={"l": "zh-tw", "o": "json", "d": roc_date},
            timeout=timeout,
        )
        if not isinstance(payload, Mapping) or str(payload.get("stat") or "").lower() != "ok":
            return {}
        tables = payload.get("tables")
        if not _is_rows(tables) or not tables or not isinstance(tables[0], Mapping):
            return {}
        rows = tables[0].get("data")
        if not _is_rows(rows):
            return {}
        result: dict[str, dict[str, float]] = {}
        for row in rows:
            if not _is_row(row) or len(row) <= 23:
                continue
            stock_id = str(row[0]).strip()
            if len(stock_id) != 4 or not stock_id.isdigit():
                continue
            result[f"{stock_id}.TWO"] = {
                "foreign": _number(row[4]),
                "trust": _number(row[13]),
                "dealer": _number(row[22]),
                "total": _number(row[23]),
            }
        return result

    def _get_json(
        self,
        url: str,
        *,
        market: str,
        query_date: date,
        params: dict[str, str],
        timeout: float,
        fallback_url: str | None = None,
    ) -> Any:
        request_get = self._request_get or _import_requests_get()
        attempt_urls = (url, fallback_url or url)
        attempts = len(attempt_urls)
        attempt_timeout = timeout / attempts
        for attempt, attempt_url in enumerate(attempt_urls, start=1):
            request_kwargs: dict[str, Any] = {
                "params": params,
                "timeout": attempt_timeout,
            }
            if request_get is official_request_get:
                request_kwargs["max_attempts"] = 1
            response: Any = None
            started_at = time.perf_counter()
            try:
                response = request_get(attempt_url, **request_kwargs)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                payload = response.json() if hasattr(response, "json") else response
            except Exception as exc:
                retryable = isinstance(
                    exc,
                    _RETRYABLE_INSTITUTIONAL_EXCEPTIONS,
                ) or _is_retryable_http_response(response)
                event = (
                    "institutional_evidence_request_retry"
                    if retryable and attempt < attempts
                    else "institutional_evidence_request_failed"
                )
                _log_request_diagnostic(
                    event,
                    market=market,
                    query_date=query_date,
                    endpoint_url=attempt_url,
                    attempt=attempt,
                    max_attempts=attempts,
                    started_at=started_at,
                    response=response,
                    error=exc,
                )
                if retryable and attempt < attempts:
                    continue
                raise
            if attempt > 1:
                _log_request_diagnostic(
                    "institutional_evidence_request_recovered",
                    market=market,
                    query_date=query_date,
                    endpoint_url=attempt_url,
                    attempt=attempt,
                    max_attempts=attempts,
                    started_at=started_at,
                    response=response,
                )
            return payload
        raise RuntimeError("official request retry loop exhausted")


def _is_retryable_http_response(response: Any) -> bool:
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, bool):
        return False
    try:
        return int(status_code) in _RETRYABLE_HTTP_STATUS_CODES
    except (TypeError, ValueError):
        return False


def _log_request_diagnostic(
    event: str,
    *,
    market: str,
    query_date: date,
    endpoint_url: str,
    attempt: int,
    max_attempts: int,
    started_at: float,
    response: Any,
    error: Exception | None = None,
) -> None:
    diagnostic: dict[str, Any] = {
        "event": event,
        "provider": "official_twse_tpex",
        "market": market,
        "dataset": "institutional_flow",
        "query_date": query_date.isoformat(),
        "endpoint_url": endpoint_url,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "elapsed_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
    }
    status_code, content_type, response_bytes = _response_metadata(response)
    if status_code is not None:
        diagnostic["http_status"] = status_code
    if content_type:
        diagnostic["content_type"] = content_type
    if response_bytes is not None:
        diagnostic["response_bytes"] = response_bytes
    if error is not None:
        diagnostic["error_type"] = error.__class__.__name__
    logger.warning(json.dumps(diagnostic, sort_keys=True))


def _response_metadata(response: Any) -> tuple[int | None, str | None, int | None]:
    status_code: int | None = None
    raw_status = getattr(response, "status_code", None)
    if not isinstance(raw_status, bool):
        try:
            status_code = int(raw_status)
        except (TypeError, ValueError):
            pass
    headers = getattr(response, "headers", None)
    header_get = getattr(headers, "get", None)
    content_type = str(header_get("content-type") or "").strip() if header_get else ""
    content = getattr(response, "content", None)
    response_bytes = len(content) if isinstance(content, (bytes, bytearray)) else None
    return status_code, content_type or None, response_bytes


def _timeout_error(market: str, query_date: date) -> dict[str, Any]:
    return {
        "market": market,
        "query_date": query_date.isoformat(),
        "error_type": "institutional_evidence_total_timeout",
    }


def _symbol_market(symbol: str) -> str:
    return "TPEX" if symbol.endswith(".TWO") else "TWSE"


def _build_payload(
    symbol: str,
    rows: Mapping[date, Mapping[str, float]],
    *,
    run_date: date,
    active_dates: Sequence[date],
) -> dict[str, Any]:
    latest_date = max(rows)
    latest = rows[latest_date]
    ordered_dates = [item for item in sorted(active_dates, reverse=True) if item in rows]
    totals = [float(rows[item].get("total", 0.0)) for item in ordered_dates]
    consecutive_positive = _consecutive(totals, positive=True)
    consecutive_negative = _consecutive(totals, positive=False)
    actor, actor_net = max(
        ((key, float(latest.get(key, 0.0))) for key in ("foreign", "trust", "dealer")),
        key=lambda item: abs(item[1]),
    )
    flat: dict[str, Any] = {
        "foreign_net_shares": float(latest.get("foreign", 0.0)),
        "investment_trust_net_shares": float(latest.get("trust", 0.0)),
        "dealer_net_shares": float(latest.get("dealer", 0.0)),
        "three_party_net_shares": float(latest.get("total", 0.0)),
        "recent_actor": "three_party",
        "cumulative_net_buy": sum(totals),
        "consecutive_buy_days": consecutive_positive,
        "consecutive_positive_days": consecutive_positive,
        "consecutive_negative_days": consecutive_negative,
        "recent_source_dates": [item.isoformat() for item in sorted(ordered_dates)],
        "flow_state": _flow_state(consecutive_positive, consecutive_negative, float(latest.get("total", 0.0))),
        "source_provider": "official_twse_tpex",
        "source_symbol": symbol,
        "data_dates": {"institutional_flow": latest_date.isoformat()},
        "requested_run_date": run_date.isoformat(),
    }
    if latest_date == run_date:
        flat.update(
            {
                "same_day_actor": actor,
                "same_day_net_buy": actor_net,
                "same_day_source_dates": [latest_date.isoformat()],
            }
        )
    return flat | {"institutional_flow": dict(flat)}


def _consecutive(values: Sequence[float], *, positive: bool) -> int:
    count = 0
    for value in values:
        if (value > 0) if positive else (value < 0):
            count += 1
            continue
        break
    return count


def _flow_state(positive_days: int, negative_days: int, latest_total: float) -> str:
    if positive_days >= 2:
        return "consistent_accumulation"
    if negative_days >= 2:
        return "consistent_distribution"
    if latest_total > 0:
        return "same_day_net_buy"
    if latest_total < 0:
        return "same_day_net_sell"
    return "neutral"


def _number(value: Any) -> float:
    text = str(value or "0").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _is_rows(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_row(value: Any) -> bool:
    return _is_rows(value)


def _import_requests_get() -> RequestGetter:
    return official_request_get


__all__ = [
    "InstitutionalEvidenceProvider",
    "InstitutionalEvidenceResult",
    "OfficialInstitutionalEvidenceProvider",
    "TPEX_3I_URL",
    "TWSE_T86_URL",
]
