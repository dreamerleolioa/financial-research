from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

from ai_stock_sentinel.data_sources.official_http import official_request_get


TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_MI_INDEX_DATASET = "MI_INDEX"
TWSE_NO_DATA_MARKERS = ("沒有符合條件的資料", "查無資料")

RequestGetter = Callable[..., Any]
MarketSessionStatus = Literal["open", "closed"]


class MarketSessionProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class MarketSessionResult:
    status: MarketSessionStatus
    run_date: date
    market: str
    provider: str
    dataset: str


class MarketSessionProvider(Protocol):
    def resolve(self, *, run_date: date, market: str) -> MarketSessionResult: ...


class TwseMarketSessionProvider:
    def __init__(self, *, request_get: RequestGetter | None = None, timeout: int = 15) -> None:
        self._request_get = request_get
        self._timeout = timeout

    def resolve(self, *, run_date: date, market: str) -> MarketSessionResult:
        if market.upper() != "TW":
            raise MarketSessionProviderError("unsupported_market_session_market")
        request_get = self._request_get or official_request_get
        try:
            response = request_get(
                TWSE_MI_INDEX_URL,
                params={
                    "response": "json",
                    "date": run_date.strftime("%Y%m%d"),
                    "type": "ALLBUT0999",
                },
                timeout=self._timeout,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else response
        except Exception as exc:
            raise MarketSessionProviderError("twse_market_session_request_failed") from exc
        return _market_session_result(payload, run_date=run_date, market=market.upper())


def _market_session_result(payload: object, *, run_date: date, market: str) -> MarketSessionResult:
    if not isinstance(payload, Mapping):
        raise MarketSessionProviderError("twse_market_session_payload_invalid")
    status = str(payload.get("stat") or "").strip()
    if status == "OK":
        response_date = str(payload.get("date") or "").strip()
        if response_date != run_date.strftime("%Y%m%d"):
            raise MarketSessionProviderError("twse_market_session_date_mismatch")
        tables = payload.get("tables")
        if not _has_market_rows(tables):
            raise MarketSessionProviderError("twse_market_session_rows_missing")
        resolved_status: MarketSessionStatus = "open"
    elif any(marker in status for marker in TWSE_NO_DATA_MARKERS):
        resolved_status = "closed"
    else:
        raise MarketSessionProviderError("twse_market_session_status_unknown")
    return MarketSessionResult(
        status=resolved_status,
        run_date=run_date,
        market=market,
        provider="twse",
        dataset=TWSE_MI_INDEX_DATASET,
    )


def _has_market_rows(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return False
    for table in value:
        if not isinstance(table, Mapping):
            continue
        rows = table.get("data")
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) and rows:
            return True
    return False


__all__ = [
    "MarketSessionProvider",
    "MarketSessionProviderError",
    "MarketSessionResult",
    "TWSE_MI_INDEX_DATASET",
    "TWSE_MI_INDEX_URL",
    "TWSE_NO_DATA_MARKERS",
    "TwseMarketSessionProvider",
]


def require_open_session(provider, *, run_date, market):
    from fastapi import HTTPException
    try:
        result = provider.resolve(run_date=run_date, market=market)
    except MarketSessionProviderError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
    if result.run_date != run_date or result.market != market or result.status != "open":
        raise HTTPException(status_code=409, detail={"code": "non_trading_signal_date", "run_date": str(run_date)})
    return {"status": "open", "run_date": str(run_date), "market": market,
            "provider": result.provider, "dataset": result.dataset}


def require_prepared_open_session(prepared):
    from fastapi import HTTPException
    from ai_stock_sentinel.daily_radar.trading_calendar import SESSION_STEP
    proof = (prepared.step_statuses or {}).get(SESSION_STEP) or {}
    if (proof.get("status") != "open" or proof.get("run_date") != str(prepared.run_date)
            or proof.get("market") != prepared.market or proof.get("provider") != "twse"
            or proof.get("dataset") not in ("MI_INDEX", "MI_5MINS_HIST")):
        raise HTTPException(status_code=409, detail={"code": "market_session_evidence_missing_or_invalid",
                                                    "run_date": str(prepared.run_date)})
    if proof.get("dataset") == "MI_5MINS_HIST":
        from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar, CALENDAR_STEP
        try:
            calendar = TradingCalendar.parse(prepared.step_statuses[CALENDAR_STEP])
            if prepared.run_date not in calendar.sessions:
                raise ValueError("closed")
        except (KeyError, ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=409, detail={"code": "market_session_evidence_missing_or_invalid",
                                                    "run_date": str(prepared.run_date)})
