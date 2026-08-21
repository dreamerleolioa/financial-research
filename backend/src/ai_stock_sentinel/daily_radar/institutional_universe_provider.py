from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from time import monotonic
from typing import Any

from curl_cffi import requests as curl_requests

from ai_stock_sentinel.daily_radar.universe import InstitutionalLeaderRow, is_daily_radar_supported_tw_stock_id
from ai_stock_sentinel.data_sources.official_http import official_request_get

TWSE_FUND_RWD_URL_TEMPLATE = "https://www.twse.com.tw/rwd/zh/fund/{report_id}"
TWSE_FOREIGN_BUY_TOP_REPORT = "TWT38U"
TWSE_TRUST_BUY_TOP_REPORT = "TWT44U"

_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, *range(500, 600)})
_RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    curl_requests.exceptions.Timeout,
    curl_requests.exceptions.ConnectionError,
    json.JSONDecodeError,
)
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_TOTAL_TIMEOUT_SECONDS = 45.0
_DEFAULT_RETRY_BACKOFF_SECONDS = 0.25
_MARKET_DAY_COMPLETE_KEY = "_market_day_complete"
_MAX_IN_FLIGHT_DEADLINE_WORKERS = 4
_DEADLINE_WORKER_SLOTS = threading.BoundedSemaphore(_MAX_IN_FLIGHT_DEADLINE_WORKERS)

_REQUIRED_ACTORS = frozenset({"foreign", "trust"})
_ACTOR_ORDER = ("foreign", "trust")
_VOLUME_FIELDS = (
    "Trading_Volume",
    "trading_volume",
    "volume",
    "Volume",
    "capacity",
    "Trading_Volume_Thousand_Share",
)
_NET_VALUE_FIELDS = (
    "net_buy_value",
    "NetBuyValue",
    "net_buy_amount",
    "NetBuyAmount",
)
_BUY_VALUE_FIELDS = (
    "buy_value",
    "BuyValue",
    "buy_amount",
    "BuyAmount",
)
_SELL_VALUE_FIELDS = (
    "sell_value",
    "SellValue",
    "sell_amount",
    "SellAmount",
)
_NET_SHARE_FIELDS = (
    "net_buy",
    "NetBuy",
    "net_buy_volume",
    "NetBuyVolume",
    "buy_sell",
    "BuySell",
)
_BUY_SHARE_FIELDS = ("buy", "Buy", "buy_volume", "BuyVolume")
_SELL_SHARE_FIELDS = ("sell", "Sell", "sell_volume", "SellVolume")

RequestGetter = Callable[..., Any]
Sleeper = Callable[[float], None]
Clock = Callable[[], float]
logger = logging.getLogger(__name__)


class InstitutionalUniverseProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        error_type: str,
        report_id: str,
        query_date: date,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.error_type = error_type
        self.report_id = report_id
        self.query_date = query_date


class InstitutionalUniverseEmptyResponse(ValueError):
    pass


class TwseRwdInstitutionalUniverseProvider:
    name = "TwseRwdInstitutionalUniverseProvider"

    def __init__(
        self,
        *,
        api_token: str = "",
        request_get: RequestGetter | None = None,
        timeout: int = 15,
        recent_market_days: int = 5,
        recent_calendar_window_days: int = 10,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        total_timeout_seconds: float = _DEFAULT_TOTAL_TIMEOUT_SECONDS,
        retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS,
        sleep: Sleeper = time.sleep,
        clock: Clock = monotonic,
    ) -> None:
        self._ignored_api_token = api_token
        self._request_get = request_get
        self._timeout = max(1, timeout)
        self._recent_market_days = max(1, recent_market_days)
        self._recent_calendar_window_days = max(0, recent_calendar_window_days)
        self._max_attempts = max(1, max_attempts)
        self._total_timeout_seconds = max(1.0, total_timeout_seconds)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._sleep = sleep
        self._clock = clock
        self._report_rows_cache: dict[
            tuple[str, date],
            tuple[bool, list[dict[str, Any]]],
        ] = {}
        self._deadlines_by_run_date: dict[date, float] = {}

    def same_day_institutional_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        deadline = self._deadline_for(run_date)
        rows = self._fetch_market_rows(
            start_date=run_date,
            end_date=run_date,
            deadline=deadline,
            max_market_days=1,
        )
        return _rank_same_day(rows, market=market, actor_limit=limit)

    def recent_accumulation_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        start_date = run_date - timedelta(days=self._recent_calendar_window_days)
        deadline = self._deadline_for(run_date)
        rows = self._fetch_market_rows(
            start_date=start_date,
            end_date=run_date,
            deadline=deadline,
            max_market_days=self._recent_market_days,
        )
        ranked = _rank_recent_accumulation(
            rows,
            market=market,
            market_days=self._recent_market_days,
        )
        return ranked[:limit]

    def _deadline_for(self, run_date: date) -> float:
        return self._deadlines_by_run_date.setdefault(
            run_date,
            self._clock() + self._total_timeout_seconds,
        )

    def _fetch_market_rows(
        self,
        *,
        start_date: date,
        end_date: date,
        deadline: float,
        max_market_days: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        completed_market_days = 0
        for query_date in reversed(_date_range(start_date, end_date)):
            if query_date.weekday() >= 5:
                continue
            date_rows: list[dict[str, Any]] = []
            try:
                foreign_ok, trust_ok, date_rows = self._fetch_date_rows(
                    query_date=query_date,
                    deadline=deadline,
                    require_available=query_date == end_date,
                )
            except InstitutionalUniverseProviderError as exc:
                if query_date == end_date:
                    self._evict_report_date(query_date)
                    raise
                _log_historical_date_skipped(exc)
                break
            if not (foreign_ok and trust_ok):
                if query_date == end_date:
                    raise InstitutionalUniverseProviderError(
                        "institutional_universe_current_date_unavailable",
                        error_type="InstitutionalUniverseEmptyResponse",
                        report_id=(
                            TWSE_FOREIGN_BUY_TOP_REPORT
                            if not foreign_ok
                            else TWSE_TRUST_BUY_TOP_REPORT
                        ),
                        query_date=query_date,
                    )
                continue
            rows.extend(date_rows)
            rows.append(_market_day_complete_marker(query_date))
            completed_market_days += 1
            if completed_market_days >= max_market_days:
                break
        return rows

    def _fetch_date_rows(
        self,
        *,
        query_date: date,
        deadline: float,
        require_available: bool,
    ) -> tuple[bool, bool, list[dict[str, Any]]]:
        for attempt in range(1, self._max_attempts + 1):
            foreign_ok, foreign_rows = self._fetch_report_rows(
                TWSE_FOREIGN_BUY_TOP_REPORT,
                query_date,
                actor="foreign",
                deadline=deadline,
                require_available=require_available,
            )
            trust_ok, trust_rows = self._fetch_report_rows(
                TWSE_TRUST_BUY_TOP_REPORT,
                query_date,
                actor="trust",
                deadline=deadline,
                require_available=require_available,
            )
            date_rows = [*foreign_rows, *trust_rows]
            if not require_available or date_rows:
                return foreign_ok, trust_ok, date_rows

            self._evict_report_date(query_date)
            _log_current_date_empty(
                query_date=query_date,
                attempt=attempt,
                retrying=attempt < self._max_attempts,
            )
            if attempt >= self._max_attempts:
                break
            self._sleep_before_retry(attempt=attempt, deadline=deadline)

        raise InstitutionalUniverseProviderError(
            "institutional_universe_current_date_unavailable",
            error_type="InstitutionalUniverseEmptyResponse",
            report_id=TWSE_FOREIGN_BUY_TOP_REPORT,
            query_date=query_date,
        )

    def _evict_report_date(self, query_date: date) -> None:
        self._report_rows_cache.pop((TWSE_FOREIGN_BUY_TOP_REPORT, query_date), None)
        self._report_rows_cache.pop((TWSE_TRUST_BUY_TOP_REPORT, query_date), None)

    def _sleep_before_retry(self, *, attempt: int, deadline: float) -> None:
        delay = min(
            self._retry_backoff_seconds * (2 ** (attempt - 1)),
            max(0.0, deadline - self._clock()),
        )
        if delay > 0:
            self._sleep(delay)

    def _fetch_report_rows(
        self,
        report_id: str,
        query_date: date,
        *,
        actor: str,
        deadline: float,
        require_available: bool,
    ) -> tuple[bool, list[dict[str, Any]]]:
        cache_key = (report_id, query_date)
        cached = self._report_rows_cache.get(cache_key)
        if cached is not None:
            status_ok, cached_rows = cached
            if require_available and not status_ok:
                raise InstitutionalUniverseProviderError(
                    "institutional_universe_current_date_unavailable",
                    error_type="InstitutionalUniverseEmptyResponse",
                    report_id=report_id,
                    query_date=query_date,
                )
            return status_ok, list(cached_rows)
        params = {"response": "json", "date": query_date.strftime("%Y%m%d")}
        request_get = self._request_get or _import_requests_get()
        for attempt in range(1, self._max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise InstitutionalUniverseProviderError(
                    "institutional_universe_total_timeout",
                    error_type="TimeoutError",
                    report_id=report_id,
                    query_date=query_date,
                )
            response: Any = None
            try:
                request_kwargs: dict[str, Any] = {
                    "params": params,
                    "timeout": min(float(self._timeout), remaining),
                }
                if request_get is official_request_get:
                    request_kwargs["max_attempts"] = 1
                succeeded, response, result = _request_payload_with_deadline(
                    request_get,
                    _twse_report_url(report_id),
                    request_kwargs=request_kwargs,
                    deadline=deadline,
                    clock=self._clock,
                )
                if not succeeded:
                    if isinstance(result, Exception):
                        raise result
                    raise RuntimeError("institutional universe request terminated unexpectedly")
                payload = result
                if not isinstance(payload, Mapping):
                    raise ValueError("TWSE institutional universe payload is not an object")
                status = str(payload.get("stat") or "").strip()
                if status != "OK":
                    if _is_no_data_status(status):
                        if require_available:
                            raise InstitutionalUniverseEmptyResponse(
                                "TWSE institutional universe current date is unavailable"
                            )
                        self._report_rows_cache[cache_key] = (False, [])
                        return False, []
                    raise ValueError("TWSE institutional universe status is unknown")
                data = payload.get("data", [])
                if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
                    raise ValueError("TWSE institutional universe data is not a sequence")
                normalized = [
                    _normalize_twse_row(row, query_date=query_date, actor=actor)
                    for row in data
                    if _is_twse_row(row)
                ]
                self._report_rows_cache[cache_key] = (True, normalized)
                return True, list(normalized)
            except Exception as exc:
                retryable = _is_retryable_request_failure(exc, response=response)
                _log_request_failure(
                    report_id=report_id,
                    query_date=query_date,
                    attempt=attempt,
                    error=exc,
                    response=response,
                    retrying=retryable and attempt < self._max_attempts,
                )
                if not retryable or attempt >= self._max_attempts:
                    raise InstitutionalUniverseProviderError(
                        (
                            "institutional_universe_current_date_unavailable"
                            if isinstance(exc, InstitutionalUniverseEmptyResponse)
                            else "institutional_universe_request_failed"
                        ),
                        error_type=exc.__class__.__name__,
                        report_id=report_id,
                        query_date=query_date,
                    ) from exc
                self._sleep_before_retry(attempt=attempt, deadline=deadline)
        raise RuntimeError("institutional universe retry loop exhausted")


class FinMindMarketInstitutionalUniverseProvider(TwseRwdInstitutionalUniverseProvider):
    name = "TwseRwdInstitutionalUniverseProvider"


def _import_requests_get() -> RequestGetter:
    return official_request_get


def _request_payload_with_deadline(
    request_get: RequestGetter,
    url: str,
    *,
    request_kwargs: Mapping[str, Any],
    deadline: float,
    clock: Clock,
) -> tuple[bool, Any, Any]:
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("institutional universe request deadline exhausted")
    if not _DEADLINE_WORKER_SLOTS.acquire(timeout=remaining):
        raise TimeoutError("institutional universe request worker capacity exhausted")
    remaining = deadline - clock()
    if remaining <= 0:
        _DEADLINE_WORKER_SLOTS.release()
        raise TimeoutError("institutional universe request deadline exhausted")
    bounded_request_kwargs = dict(request_kwargs)
    bounded_request_kwargs["timeout"] = min(
        float(bounded_request_kwargs.get("timeout", remaining)),
        remaining,
    )

    result_queue: queue.Queue[tuple[bool, Any, Any]] = queue.Queue(maxsize=1)

    def run_request() -> None:
        response: Any = None
        try:
            response = request_get(url, **bounded_request_kwargs)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else response
            result_queue.put((True, response, payload))
        except BaseException as exc:
            result_queue.put((False, response, exc))
        finally:
            _DEADLINE_WORKER_SLOTS.release()

    worker = threading.Thread(
        target=run_request,
        name="twse-institutional-universe-request",
        daemon=True,
    )
    try:
        worker.start()
    except BaseException:
        _DEADLINE_WORKER_SLOTS.release()
        raise
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("institutional universe request deadline exhausted")
    try:
        outcome = result_queue.get(timeout=remaining)
    except queue.Empty as exc:
        raise TimeoutError("institutional universe request exceeded total deadline") from exc
    if clock() >= deadline:
        raise TimeoutError("institutional universe request exceeded total deadline")
    return outcome


def _is_retryable_request_failure(exc: Exception, *, response: Any) -> bool:
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        if status_code >= 400:
            return status_code in _RETRYABLE_HTTP_STATUS_CODES
    return isinstance(exc, _RETRYABLE_EXCEPTIONS) or isinstance(exc, ValueError)


def _is_no_data_status(status: str) -> bool:
    normalized = status.lower()
    return any(
        marker in normalized
        for marker in ("沒有符合條件", "查無資料", "no data")
    )


def _log_request_failure(
    *,
    report_id: str,
    query_date: date,
    attempt: int,
    error: Exception,
    response: Any,
    retrying: bool,
) -> None:
    status_code = getattr(response, "status_code", None)
    logger.warning(
        json.dumps(
            {
                "event": "institutional_universe_request_retry" if retrying else "institutional_universe_request_failed",
                "provider": "twse_rwd",
                "report_id": report_id,
                "query_date": query_date.isoformat(),
                "attempt": attempt,
                "error_type": error.__class__.__name__,
                "status_code": status_code if isinstance(status_code, int) else None,
            },
            sort_keys=True,
        )
    )


def _log_historical_date_skipped(error: InstitutionalUniverseProviderError) -> None:
    logger.warning(
        json.dumps(
            {
                "event": "institutional_universe_historical_date_skipped",
                "provider": "twse_rwd",
                "report_id": error.report_id,
                "query_date": error.query_date.isoformat(),
                "error_type": error.error_type,
            },
            sort_keys=True,
        )
    )


def _log_current_date_empty(*, query_date: date, attempt: int, retrying: bool) -> None:
    logger.warning(
        json.dumps(
            {
                "event": (
                    "institutional_universe_empty_retry"
                    if retrying
                    else "institutional_universe_empty_failed"
                ),
                "provider": "twse_rwd",
                "query_date": query_date.isoformat(),
                "attempt": attempt,
                "error_type": "InstitutionalUniverseEmptyResponse",
            },
            sort_keys=True,
        )
    )


def _twse_report_url(report_id: str) -> str:
    return TWSE_FUND_RWD_URL_TEMPLATE.format(report_id=report_id)


def _date_range(start_date: date, end_date: date) -> Sequence[date]:
    if end_date < start_date:
        return []
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _market_day_complete_marker(query_date: date) -> dict[str, Any]:
    return {
        "date": query_date.isoformat(),
        _MARKET_DAY_COMPLETE_KEY: True,
    }


def _is_twse_row(row: Any) -> bool:
    return isinstance(row, Sequence) and not isinstance(row, (str, bytes))


def _normalize_twse_row(row: Sequence[Any], *, query_date: date, actor: str) -> dict[str, Any]:
    first_data_index = _twse_first_data_index(row)
    stock_id = _twse_cell(row, first_data_index).strip()
    if actor == "foreign":
        buy_index, sell_index, net_index = first_data_index + 8, first_data_index + 9, first_data_index + 10
        actor_name = "Foreign_Investors"
    else:
        buy_index, sell_index, net_index = first_data_index + 2, first_data_index + 3, first_data_index + 4
        actor_name = "Investment_Trust"
    return {
        "date": query_date.isoformat(),
        "stock_id": stock_id,
        "name": actor_name,
        "buy": _twse_cell(row, buy_index),
        "sell": _twse_cell(row, sell_index),
        "net_buy": _twse_cell(row, net_index),
    }


def _twse_cell(row: Sequence[Any], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index]).strip()


def _twse_first_data_index(row: Sequence[Any]) -> int:
    first_cell = _twse_cell(row, 0)
    return 0 if first_cell and first_cell[0].isdigit() else 1


def _rank_same_day(
    rows: Sequence[Mapping[str, Any]],
    *,
    market: str,
    actor_limit: int = 50,
) -> list[InstitutionalLeaderRow]:
    if actor_limit <= 0:
        return []

    combined_limit = actor_limit
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    volumes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    source_dates: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in rows:
        actor = _actor_key(row)
        if actor not in _REQUIRED_ACTORS:
            continue
        stock_id = _stock_id(row)
        if not stock_id or not is_daily_radar_supported_tw_stock_id(stock_id):
            continue
        totals[actor][stock_id] += _net_amount(row)
        volume = _volume(row)
        if volume is not None:
            volumes[actor][stock_id] = max(volumes[actor][stock_id], volume)
        row_date = str(row.get("date", ""))
        if row_date:
            source_dates[actor][stock_id].add(row_date)

    actor_scored: list[tuple[str, str, float, float, float, tuple[str, ...]]] = []
    for actor in _ACTOR_ORDER:
        scored = []
        for stock_id, net_amount in totals.get(actor, {}).items():
            if net_amount <= 0:
                continue
            concentration = net_amount / volumes[actor][stock_id] if volumes[actor][stock_id] > 0 else 0.0
            score = net_amount + concentration
            scored.append(
                (
                    actor,
                    stock_id,
                    net_amount,
                    concentration,
                    score,
                    tuple(sorted(source_dates[actor][stock_id])),
                )
            )
        actor_scored.extend(sorted(scored, key=lambda item: (-item[2], -item[3], item[1])))

    best_by_stock: dict[str, tuple[str, str, float, float, float, tuple[str, ...]]] = {}
    for item in actor_scored:
        stock_id = item[1]
        existing = best_by_stock.get(stock_id)
        if existing is None or _same_day_sort_key(item) < _same_day_sort_key(existing):
            best_by_stock[stock_id] = item

    ranked = sorted(best_by_stock.values(), key=_same_day_sort_key)[:combined_limit]
    return [
        InstitutionalLeaderRow(
            symbol=_format_symbol(stock_id, market),
            rank=index,
            score=score,
            actor=actor,
            net_buy=net_amount,
            concentration=concentration,
            source_dates=dates,
            flow_state="same_day_net_buy",
            bucket_hints=("same_day_institutional",),
        )
        for index, (actor, stock_id, net_amount, concentration, score, dates) in enumerate(ranked, start=1)
    ]


def _same_day_sort_key(item: tuple[str, str, float, float, float, tuple[str, ...]]) -> tuple[float, float, int, str]:
    actor, stock_id, net_amount, concentration, _, _ = item
    return (-net_amount, -concentration, _actor_sort_index(actor), stock_id)


def _actor_sort_index(actor: str) -> int:
    try:
        return _ACTOR_ORDER.index(actor)
    except ValueError:
        return len(_ACTOR_ORDER)


def _rank_recent_accumulation(
    rows: Sequence[Mapping[str, Any]],
    *,
    market: str,
    market_days: int,
) -> list[InstitutionalLeaderRow]:
    latest_dates = sorted({str(row.get("date", "")) for row in rows if row.get("date")}, reverse=True)[:market_days]
    latest_date_set = set(latest_dates)
    if not latest_date_set:
        return []

    daily_net: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    daily_volume: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        row_date = str(row.get("date", ""))
        if row_date not in latest_date_set:
            continue
        actor = _actor_key(row)
        if actor not in _REQUIRED_ACTORS:
            continue
        stock_id = _stock_id(row)
        if not stock_id or not is_daily_radar_supported_tw_stock_id(stock_id):
            continue
        daily_net[stock_id][row_date] += _net_amount(row)
        volume = _volume(row)
        if volume is not None:
            daily_volume[stock_id][row_date] = max(daily_volume[stock_id].get(row_date, 0.0), volume)

    scored: list[tuple[str, tuple[float, float, float], float, int, float | None, tuple[str, ...]]] = []
    for stock_id, nets_by_date in daily_net.items():
        source_dates = tuple(sorted(nets_by_date))
        ordered_nets = [nets_by_date.get(row_date, 0.0) for row_date in sorted(latest_date_set)]
        cumulative_net = sum(ordered_nets)
        if cumulative_net <= 0:
            continue
        max_consecutive = _max_consecutive_positive_days(ordered_nets)
        if max_consecutive <= 0:
            continue
        volume_total = sum(daily_volume.get(stock_id, {}).values())
        if volume_total > 0:
            concentration = cumulative_net / volume_total
            secondary_score = concentration
            score = max_consecutive * 1_000_000.0 + concentration
        else:
            concentration = None
            secondary_score = cumulative_net
            score = max_consecutive * 1_000_000.0 + cumulative_net
        scored.append(
            (
                stock_id,
                (float(max_consecutive), secondary_score, cumulative_net),
                score,
                max_consecutive,
                concentration,
                source_dates,
            )
        )

    scored.sort(key=lambda item: (-item[1][0], -item[1][1], -item[1][2], item[0]))
    return [
        InstitutionalLeaderRow(
            symbol=_format_symbol(stock_id, market),
            rank=index,
            score=score,
            actor="institutional",
            cumulative_net_buy=metrics[2],
            concentration=concentration,
            consecutive_buy_days=max_consecutive,
            source_dates=source_dates,
            flow_state="consistent_accumulation" if max_consecutive >= 2 else "weak_confirmation",
            bucket_hints=("recent_accumulation",),
        )
        for index, (stock_id, metrics, score, max_consecutive, concentration, source_dates) in enumerate(
            scored,
            start=1,
        )
    ]


def _actor_key(row: Mapping[str, Any]) -> str | None:
    actor_name = str(row.get("name") or row.get("investor") or row.get("institutional_investor") or "")
    if "外資" in actor_name or "Foreign" in actor_name:
        return "foreign"
    if "投信" in actor_name or "Investment_Trust" in actor_name or "Investment Trust" in actor_name:
        return "trust"
    if "自營" in actor_name or "Dealer" in actor_name:
        return "dealer"
    return None


def _stock_id(row: Mapping[str, Any]) -> str:
    return str(row.get("stock_id") or row.get("StockID") or row.get("stock_no") or "").strip()


def _net_amount(row: Mapping[str, Any]) -> float:
    net = _first_float(row, _NET_VALUE_FIELDS)
    if net is not None:
        return net

    buy_value = _first_float(row, _BUY_VALUE_FIELDS)
    sell_value = _first_float(row, _SELL_VALUE_FIELDS)
    if buy_value is not None or sell_value is not None:
        return (buy_value or 0.0) - (sell_value or 0.0)

    net = _first_float(row, _NET_SHARE_FIELDS)
    if net is not None:
        return net

    buy = _first_float(row, _BUY_SHARE_FIELDS) or 0.0
    sell = _first_float(row, _SELL_SHARE_FIELDS) or 0.0
    return buy - sell


def _volume(row: Mapping[str, Any]) -> float | None:
    return _first_float(row, _VOLUME_FIELDS)


def _first_float(row: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
    return None


def _max_consecutive_positive_days(values: Sequence[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _format_symbol(stock_id: str, market: str) -> str:
    if "." in stock_id:
        return stock_id
    if market.upper() == "TW":
        return f"{stock_id}.TW"
    return stock_id


__all__ = [
    "FinMindMarketInstitutionalUniverseProvider",
    "InstitutionalUniverseProviderError",
    "TWSE_FOREIGN_BUY_TOP_REPORT",
    "TWSE_FUND_RWD_URL_TEMPLATE",
    "TWSE_TRUST_BUY_TOP_REPORT",
    "TwseRwdInstitutionalUniverseProvider",
]
