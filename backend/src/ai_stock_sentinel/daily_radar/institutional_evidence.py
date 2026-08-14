from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol


TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_3I_URL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"

RequestGetter = Callable[..., Any]


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
        self._timeout = timeout
        self._recent_market_days = recent_market_days
        self._calendar_window_days = calendar_window_days
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
        market_days: set[date] = set()
        tasks = [
            (market, run_date - timedelta(days=offset))
            for offset in range(self._calendar_window_days + 1)
            for market in ("TWSE", "TPEX")
        ]
        executor = ThreadPoolExecutor(max_workers=min(self._max_workers, len(tasks)))
        futures: dict[Future[dict[str, dict[str, float]]], tuple[str, date]] = {
            executor.submit(self._fetch_market_date, market, query_date): (market, query_date)
            for market, query_date in tasks
        }
        done, pending = wait(futures, timeout=self._total_timeout)
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        for future, (market, query_date) in futures.items():
            if future in pending:
                errors.append(
                    {
                        "market": market,
                        "query_date": query_date.isoformat(),
                        "error_type": "institutional_evidence_total_timeout",
                    }
                )
                continue
            try:
                rows = future.result()
            except Exception as exc:
                errors.append(
                    {
                        "market": market,
                        "query_date": query_date.isoformat(),
                        "error_type": exc.__class__.__name__,
                    }
                )
                continue
            if rows:
                market_days.add(query_date)
            for symbol, values in rows.items():
                if symbol in requested:
                    daily_by_symbol.setdefault(symbol, {})[query_date] = values

        active_dates = sorted(market_days, reverse=True)[: self._recent_market_days]
        payloads = {
            symbol: _build_payload(symbol, rows, run_date=run_date, active_dates=active_dates)
            for symbol, rows in daily_by_symbol.items()
            if rows
        }
        return InstitutionalEvidenceResult(payloads_by_symbol=payloads, errors=errors)

    def _fetch_market_date(self, market: str, query_date: date) -> dict[str, dict[str, float]]:
        return self._fetch_twse(query_date) if market == "TWSE" else self._fetch_tpex(query_date)

    def _fetch_twse(self, query_date: date) -> dict[str, dict[str, float]]:
        payload = self._get_json(
            TWSE_T86_URL,
            params={
                "response": "json",
                "date": query_date.strftime("%Y%m%d"),
                "selectType": "ALLBUT0999",
            },
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

    def _fetch_tpex(self, query_date: date) -> dict[str, dict[str, float]]:
        roc_date = f"{query_date.year - 1911}/{query_date.month:02d}/{query_date.day:02d}"
        payload = self._get_json(
            TPEX_3I_URL,
            params={"l": "zh-tw", "o": "json", "d": roc_date},
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

    def _get_json(self, url: str, *, params: dict[str, str]) -> Any:
        request_get = self._request_get or _import_requests_get()
        response = request_get(url, params=params, timeout=self._timeout)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.json() if hasattr(response, "json") else response


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
    import requests

    return requests.get


__all__ = [
    "InstitutionalEvidenceProvider",
    "InstitutionalEvidenceResult",
    "OfficialInstitutionalEvidenceProvider",
    "TPEX_3I_URL",
    "TWSE_T86_URL",
]
