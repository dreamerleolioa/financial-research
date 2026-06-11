from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ai_stock_sentinel.data_sources.finmind_token import get_token_manager
from ai_stock_sentinel.daily_radar.background_context import BACKGROUND_CONTEXT_ALL_CONSUMERS, BackgroundContextPayload


FINMIND_BACKGROUND_CONTEXT_CONSUMERS = BACKGROUND_CONTEXT_ALL_CONSUMERS


_FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
_FULL_MARGIN_DATASET = "TaiwanStockMarginPurchaseShortSale"
_LENDING_DATASET = "TaiwanStockSecuritiesLending"
_SUPPORTED_CONTEXT_TYPES = {"full_margin", "lending"}
_FATAL_DATASET_ERROR_CODES = {
    "missing_dependency",
    "finmind_quota_or_token_error",
    "finmind_request_error",
    "finmind_response_error",
}


@dataclass(frozen=True)
class _FinMindDatasetError(Exception):
    code: str
    message: str
    dataset: str

    def __str__(self) -> str:
        return self.message


class FinMindBackgroundChipContextProvider:
    """Fetch selected-symbol chip context from FinMind for the background cache."""

    provider_name = "finmind_background_chip_context_provider"

    def __init__(
        self,
        *,
        api_token: str = "",
        request_get: Callable[..., Any] | None = None,
        lookback_trading_days: int = 10,
        stale_after_days: int = 5,
    ) -> None:
        self._static_token = api_token
        self._request_get = request_get
        self._lookback_trading_days = lookback_trading_days
        self._stale_after_days = stale_after_days

    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        for symbol in symbols:
            for context_type in context_types:
                if context_type == "full_margin":
                    yield self._full_margin_payload(symbol=symbol, run_date=run_date, market=market)
                elif context_type == "lending":
                    yield self._lending_payload(symbol=symbol, run_date=run_date, market=market)
                else:
                    yield self._missing_payload(
                        symbol=symbol,
                        context_type=context_type,
                        run_date=run_date,
                        market=market,
                        missing_reason=(
                            "provider_deferred"
                            if context_type == "weekly_major_holders"
                            else "unsupported_context_type"
                        ),
                        source_extra={"supported_context_types": sorted(_SUPPORTED_CONTEXT_TYPES)},
                    )

    def _full_margin_payload(self, *, symbol: str, run_date: date, market: str) -> BackgroundContextPayload:
        try:
            rows = self._fetch_rows(symbol=symbol, dataset=_FULL_MARGIN_DATASET, run_date=run_date)
        except _FinMindDatasetError as exc:
            if exc.code in _FATAL_DATASET_ERROR_CODES:
                raise
            return self._missing_payload(
                symbol=symbol,
                context_type="full_margin",
                run_date=run_date,
                market=market,
                missing_reason=exc.code,
                source_extra={"dataset": exc.dataset, "error_message": exc.message},
            )

        recent_rows = _recent_rows(rows, self._lookback_trading_days, run_date=run_date)
        if not recent_rows:
            return self._missing_payload(
                symbol=symbol,
                context_type="full_margin",
                run_date=run_date,
                market=market,
                missing_reason="finmind_no_data",
                source_extra={"dataset": _FULL_MARGIN_DATASET},
            )

        latest_row = recent_rows[-1]
        as_of_date = _row_date(latest_row)
        first_row = recent_rows[0]
        margin_latest = _first_float(latest_row, ["MarginPurchaseTodayBalance", "MarginPurchaseToday"])
        margin_start = _first_float(first_row, ["MarginPurchaseYesterdayBalance", "MarginPurchaseYesterday"])
        short_latest = _first_float(latest_row, ["ShortSaleTodayBalance", "ShortSaleToday"])
        short_start = _first_float(first_row, ["ShortSaleYesterdayBalance", "ShortSaleYesterday"])
        freshness, missing_reason = self._freshness(as_of_date=as_of_date, run_date=run_date)

        return BackgroundContextPayload(
            symbol=symbol,
            context_type="full_margin",
            applicable_consumers=FINMIND_BACKGROUND_CONTEXT_CONSUMERS,
            source=self._source(market=market, dataset=_FULL_MARGIN_DATASET),
            as_of_date=as_of_date,
            freshness=freshness,
            payload={
                "provider": self.provider_name,
                "dataset": _FULL_MARGIN_DATASET,
                "lookback_trading_days": self._lookback_trading_days,
                "row_count": len(recent_rows),
                "unit": "finmind_margin_balance_unit",
                "latest_margin_balance": margin_latest,
                "latest_short_balance": short_latest,
                "margin_balance_delta": _delta(margin_latest, margin_start),
                "margin_balance_delta_pct": _delta_pct(margin_latest, margin_start),
                "short_balance_delta": _delta(short_latest, short_start),
                "short_balance_delta_pct": _delta_pct(short_latest, short_start),
                "data_dates": [row["date"] for row in recent_rows if row.get("date")],
            },
            missing_reason=missing_reason,
            replay_key=_replay_key(symbol, "full_margin", as_of_date),
        )

    def _lending_payload(self, *, symbol: str, run_date: date, market: str) -> BackgroundContextPayload:
        try:
            rows = self._fetch_rows(symbol=symbol, dataset=_LENDING_DATASET, run_date=run_date)
        except _FinMindDatasetError as exc:
            if exc.code in _FATAL_DATASET_ERROR_CODES:
                raise
            return self._missing_payload(
                symbol=symbol,
                context_type="lending",
                run_date=run_date,
                market=market,
                missing_reason=exc.code,
                source_extra={"dataset": exc.dataset, "error_message": exc.message},
            )

        daily_volumes = _daily_lending_volumes(rows, self._lookback_trading_days, run_date=run_date)
        if not daily_volumes:
            return self._missing_payload(
                symbol=symbol,
                context_type="lending",
                run_date=run_date,
                market=market,
                missing_reason="finmind_no_data",
                source_extra={"dataset": _LENDING_DATASET},
            )

        data_dates = [dt for dt, _ in daily_volumes]
        latest_date = data_dates[-1]
        latest_volume = daily_volumes[-1][1]
        first_volume = daily_volumes[0][1]
        period_volume = sum(volume for _, volume in daily_volumes)
        freshness, missing_reason = self._freshness(as_of_date=latest_date, run_date=run_date)

        return BackgroundContextPayload(
            symbol=symbol,
            context_type="lending",
            applicable_consumers=FINMIND_BACKGROUND_CONTEXT_CONSUMERS,
            source=self._source(market=market, dataset=_LENDING_DATASET),
            as_of_date=latest_date,
            freshness=freshness,
            payload={
                "provider": self.provider_name,
                "dataset": _LENDING_DATASET,
                "lookback_trading_days": self._lookback_trading_days,
                "row_count": len(rows),
                "daily_point_count": len(daily_volumes),
                "unit": "finmind_lending_volume_unit",
                "latest_daily_lending_volume": latest_volume,
                "period_lending_volume": period_volume,
                "lending_volume_delta": latest_volume - first_volume if len(daily_volumes) >= 2 else None,
                "data_dates": [dt.isoformat() for dt in data_dates],
            },
            missing_reason=missing_reason,
            replay_key=_replay_key(symbol, "lending", latest_date),
        )

    def _fetch_rows(self, *, symbol: str, dataset: str, run_date: date) -> list[dict[str, Any]]:
        request_get = self._request_get
        if request_get is None:
            try:
                import requests
            except ImportError as exc:
                raise _FinMindDatasetError("missing_dependency", "requests package is not installed", dataset) from exc
            request_get = requests.get

        start_date = run_date - timedelta(days=self._lookback_trading_days * 3 + 7)
        params: dict[str, Any] = {
            "dataset": dataset,
            "data_id": _strip_suffix(symbol),
            "start_date": start_date.isoformat(),
            "end_date": run_date.isoformat(),
        }
        token_manager = None if self._static_token else get_token_manager()
        token = self._static_token or token_manager.token
        response = self._request_dataset(request_get=request_get, params=params, token=token, dataset=dataset)
        if getattr(response, "status_code", None) == 402 and token_manager is not None:
            token_manager.invalidate()
            response = self._request_dataset(
                request_get=request_get,
                params=params,
                token=token_manager.token,
                dataset=dataset,
            )

        if getattr(response, "status_code", None) == 402:
            raise _FinMindDatasetError(
                "finmind_quota_or_token_error",
                f"FinMind returned HTTP 402 for {dataset}",
                dataset,
            )

        try:
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise _FinMindDatasetError(
                "finmind_response_error",
                f"FinMind response could not be parsed for {dataset}: {exc}",
                dataset,
            ) from exc

        if body.get("status") != 200:
            message = str(body.get("msg") or "unknown error")
            code = "finmind_access_required" if "level" in message.lower() else "finmind_api_error"
            raise _FinMindDatasetError(code, message, dataset)

        data = body.get("data")
        return list(data) if isinstance(data, list) else []

    def _request_dataset(
        self,
        *,
        request_get: Callable[..., Any],
        params: Mapping[str, Any],
        token: str,
        dataset: str,
    ) -> Any:
        request_params = dict(params)
        if token:
            request_params["token"] = token
        try:
            return request_get(_FINMIND_API, params=request_params, timeout=15)
        except Exception as exc:
            raise _FinMindDatasetError(
                "finmind_request_error",
                f"FinMind request failed for {dataset}: {exc}",
                dataset,
            ) from exc

    def _freshness(self, *, as_of_date: date | None, run_date: date) -> tuple[str, str | None]:
        if as_of_date is None:
            return "missing", "finmind_no_data"
        age_days = (run_date - as_of_date).days
        if age_days <= self._stale_after_days:
            return "fresh", None
        return "stale", "source_stale"

    def _missing_payload(
        self,
        *,
        symbol: str,
        context_type: str,
        run_date: date,
        market: str,
        missing_reason: str,
        source_extra: Mapping[str, Any] | None = None,
    ) -> BackgroundContextPayload:
        source = self._source(market=market)
        source.update(dict(source_extra or {}))
        return BackgroundContextPayload(
            symbol=symbol,
            context_type=context_type,
            applicable_consumers=FINMIND_BACKGROUND_CONTEXT_CONSUMERS,
            source=source,
            as_of_date=None,
            freshness="missing",
            payload={},
            missing_reason=missing_reason,
            replay_key=f"background_context:{symbol}:{context_type}:{run_date.isoformat()}:missing:{missing_reason}",
        )

    def _source(self, *, market: str, dataset: str | None = None) -> dict[str, Any]:
        source: dict[str, Any] = {
            "domain": "background_context",
            "provider": self.provider_name,
            "market": market,
        }
        if dataset is not None:
            source["dataset"] = dataset
        return source


def _strip_suffix(symbol: str) -> str:
    return symbol.split(".")[0]


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(row: Mapping[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _row_date(row: Mapping[str, Any]) -> date | None:
    value = row.get("date")
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _recent_rows(rows: list[dict[str, Any]], limit: int, *, run_date: date) -> list[dict[str, Any]]:
    valid_rows = [
        row
        for row in rows
        if (row_date := _row_date(row)) is not None and row_date <= run_date
    ]
    valid_rows.sort(key=lambda row: str(row.get("date") or ""))
    return valid_rows[-limit:]


def _daily_lending_volumes(
    rows: list[dict[str, Any]],
    limit: int,
    *,
    run_date: date,
) -> list[tuple[date, float]]:
    daily: defaultdict[date, float] = defaultdict(float)
    for row in rows:
        row_date = _row_date(row)
        if row_date is None or row_date > run_date:
            continue
        volume = _safe_float(row.get("volume"))
        if volume is None:
            continue
        daily[row_date] += volume
    return sorted(daily.items())[-limit:]


def _delta(latest: float | None, start: float | None) -> float | None:
    if latest is None or start is None:
        return None
    return latest - start


def _delta_pct(latest: float | None, start: float | None) -> float | None:
    if latest is None or start in (None, 0):
        return None
    return (latest - start) / start * 100


def _replay_key(symbol: str, context_type: str, as_of_date: date | None) -> str:
    date_part = as_of_date.isoformat() if as_of_date is not None else "missing"
    return f"background_context:{symbol}:{context_type}:{date_part}"


__all__ = [
    "FINMIND_BACKGROUND_CONTEXT_CONSUMERS",
    "FinMindBackgroundChipContextProvider",
]
