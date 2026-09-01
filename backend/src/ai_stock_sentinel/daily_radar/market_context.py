from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Protocol

import yfinance as yf

from ai_stock_sentinel.daily_radar.market_session import (
    TWSE_MI_INDEX_URL,
    TWSE_NO_DATA_MARKERS,
)
from ai_stock_sentinel.daily_radar.raw_data import (
    _build_technical_payload,
    _frame_on_or_before_run_date,
    _symbol_frame,
)
from ai_stock_sentinel.data_sources.official_http import official_request_get


MARKET_INDEX_SYMBOLS: dict[str, tuple[str, str]] = {
    "TW": ("TAIEX", "^TWII"),
    "US": ("SPX", "^GSPC"),
}
TWSE_WEIGHTED_INDEX_NAME = "發行量加權股價指數"
MARKET_INDEX_OHLCV_FIELDS = ("Open", "High", "Low", "Close", "Volume")
MAX_MARKET_VOLATILITY_LAG_DAYS = 14


class MarketIndexContextProvider(Protocol):
    def build(self, *, run_date: date, market: str) -> Mapping[str, Any]: ...


class YFinanceMarketIndexContextProvider:
    def __init__(
        self,
        index_symbols: Mapping[str, tuple[str, str]] | None = None,
        *,
        official_request_getter: Callable[..., Any] | None = None,
        official_timeout: int = 10,
    ) -> None:
        self._index_symbols = dict(index_symbols or MARKET_INDEX_SYMBOLS)
        self._official_request_getter = official_request_getter or official_request_get
        self._official_timeout = official_timeout

    def build(self, *, run_date: date, market: str) -> Mapping[str, Any]:
        index_symbol, yfinance_symbol = self._index_config(market)
        start_date = run_date - timedelta(days=120)
        end_date = run_date + timedelta(days=1)
        attempts = (
            (
                "download",
                lambda: yf.download(
                    yfinance_symbol,
                    start=start_date,
                    end=end_date,
                    interval="1d",
                    threads=False,
                    progress=False,
                ),
            ),
            (
                "ticker_history",
                lambda: yf.Ticker(yfinance_symbol).history(
                    start=start_date,
                    end=end_date,
                    interval="1d",
                    actions=False,
                    auto_adjust=False,
                ),
            ),
        )
        last_context: dict[str, Any] | None = None
        last_payload: Mapping[str, Any] | None = None
        last_payload_fetch_method: str | None = None
        fallback_triggered = False
        for fetch_method, fetch_history in attempts:
            fallback_triggered = fetch_method != "download"
            try:
                history = fetch_history()
                frame = _symbol_frame(history, yfinance_symbol)
                frame = _frame_on_or_before_run_date(frame, run_date=run_date)
                frame = _complete_market_index_frame(frame)
                payload = _build_technical_payload(index_symbol, frame, run_date=run_date)
                context = build_market_context_from_technical_payload(
                    payload,
                    run_date=run_date,
                    index_symbol=index_symbol,
                    yfinance_symbol=yfinance_symbol,
                )
            except Exception:
                continue
            history_gap_detected = (
                market.upper() == "TW"
                and payload is not None
                and _trailing_history_gap_detected(payload, run_date=run_date)
            )
            fallback_payload = payload
            if history_gap_detected:
                historical_frame = _frame_on_or_before_run_date(
                    frame,
                    run_date=run_date - timedelta(days=1),
                )
                fallback_payload = _build_technical_payload(
                    index_symbol,
                    historical_frame,
                    run_date=run_date,
                )
                context = _missing_context(
                    run_date=run_date,
                    index_symbol=index_symbol,
                    yfinance_symbol=yfinance_symbol,
                    freshness="stale",
                    missing_reason="market_index_history_gap",
                    data_date=(
                        _market_index_data_date(fallback_payload)
                        if fallback_payload is not None
                        else None
                    ),
                )
            if fallback_payload is not None and (
                last_payload is None
                or _payload_recency_rank(fallback_payload)
                > _payload_recency_rank(last_payload)
            ):
                last_payload = fallback_payload
                last_payload_fetch_method = fetch_method
            context["provider_trace"] = {
                "provider": "yfinance",
                "fetch_method": fetch_method,
                "fallback_triggered": fallback_triggered,
            }
            if history_gap_detected:
                context["provider_trace"]["history_gap_detected"] = True
            if last_context is None or _context_freshness_rank(context) > _context_freshness_rank(
                last_context
            ):
                last_context = context
            if market_context_refresh_error(context, run_date=run_date) is None:
                return context

        official_error: str | None = None
        if market.upper() == "TW" and last_payload is not None:
            official_snapshots, official_error = self._official_index_snapshots(
                last_payload,
                run_date=run_date,
            )
            if official_snapshots is not None:
                official_payload, official_error = _payload_with_official_closes(
                    last_payload,
                    official_snapshots=official_snapshots,
                    run_date=run_date,
                )
                if official_payload is not None:
                    official_context = build_market_context_from_technical_payload(
                        official_payload,
                        run_date=run_date,
                        index_symbol=index_symbol,
                        yfinance_symbol=yfinance_symbol,
                    )
                    official_context["provider_trace"] = {
                        "provider": "twse",
                        "dataset": "MI_INDEX",
                        "fetch_method": "official_close_with_yfinance_history",
                        "history_provider": "yfinance",
                        "history_fetch_method": last_payload_fetch_method,
                        "official_dates": [
                            str(snapshot["data_date"])
                            for snapshot in official_snapshots
                        ],
                        "fallback_triggered": True,
                    }
                    if market_context_refresh_error(official_context, run_date=run_date) is None:
                        return official_context
                    official_error = "twse_market_index_context_incomplete"

        if last_context is not None:
            last_context["provider_trace"]["fallback_triggered"] = fallback_triggered
            if official_error is not None:
                last_context["provider_trace"].update(
                    {
                        "official_fallback_attempted": True,
                        "official_fallback_error": official_error,
                    }
                )
            return last_context
        context = _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="missing",
            missing_reason="market_index_fetch_failed",
            data_date=None,
        )
        context["provider_trace"] = {
            "provider": "yfinance",
            "fetch_method": None,
            "fallback_triggered": True,
        }
        return context

    def _official_index_snapshots(
        self,
        payload: Mapping[str, Any],
        *,
        run_date: date,
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        history, history_error = _official_history(
            payload,
            run_date=run_date,
        )
        if history is None:
            return None, history_error

        snapshots: list[dict[str, Any]] = []
        history_data_date = date.fromisoformat(history[-1]["date"])
        query_date = history_data_date + timedelta(days=1)
        while query_date <= run_date:
            snapshot, snapshot_error = self._official_index_snapshot(run_date=query_date)
            if snapshot_error == "twse_market_index_no_data":
                query_date += timedelta(days=1)
                continue
            if snapshot is None:
                return None, snapshot_error
            snapshots.append(snapshot)
            query_date += timedelta(days=1)

        if not snapshots or snapshots[-1].get("data_date") != run_date.isoformat():
            return None, "twse_market_index_run_date_missing"
        return snapshots, None

    def _official_index_snapshot(
        self,
        *,
        run_date: date,
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            response = self._official_request_getter(
                TWSE_MI_INDEX_URL,
                params={
                    "response": "json",
                    "date": run_date.strftime("%Y%m%d"),
                    "type": "ALLBUT0999",
                },
                timeout=self._official_timeout,
                max_attempts=2,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else response
        except Exception:
            return None, "twse_market_index_request_failed"
        return _official_market_index_snapshot(payload, run_date=run_date)

    def _index_config(self, market: str) -> tuple[str, str]:
        return self._index_symbols.get(market.upper(), self._index_symbols["TW"])


def build_market_context_from_technical_payload(
    payload: Mapping[str, Any] | None,
    *,
    run_date: date,
    index_symbol: str,
    yfinance_symbol: str,
) -> dict[str, Any]:
    if payload is None:
        return _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="missing",
            missing_reason="market_index_ohlcv_missing",
            data_date=None,
        )

    data_date = _market_index_data_date(payload)
    if data_date is None:
        return _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="missing",
            missing_reason="market_index_data_date_missing",
            data_date=None,
        )
    if data_date != run_date:
        return _missing_context(
            run_date=run_date,
            index_symbol=index_symbol,
            yfinance_symbol=yfinance_symbol,
            freshness="stale",
            missing_reason="market_index_stale",
            data_date=data_date,
        )

    ohlcv = _mapping(payload.get("ohlcv"))
    indicators = _mapping(payload.get("indicators"))
    close = _float(ohlcv.get("close"))
    previous_close = _float(ohlcv.get("previous_close"))
    ma20 = _float(indicators.get("ma20"))
    ma60 = _float(indicators.get("ma60"))
    atr14 = _float(indicators.get("atr14"))

    above_ma20 = close >= ma20 if close is not None and ma20 is not None else None
    above_ma60 = close >= ma60 if close is not None and ma60 is not None else None
    volatility_state = _volatility_state(close, atr14)
    regime = _regime(
        close=close,
        previous_close=previous_close,
        above_ma20=above_ma20,
        above_ma60=above_ma60,
        volatility_state=volatility_state,
    )
    risk_flags = ["market_weakness"] if regime == "risk_off" else []

    context_data_dates = {"market_index": data_date.isoformat()}
    benchmark_data_dates = dict(context_data_dates)
    raw_volatility_data_date = _mapping(payload.get("data_dates")).get(
        "market_volatility"
    )
    volatility_data_date = _market_volatility_data_date(payload)
    if raw_volatility_data_date is not None:
        normalized_volatility_data_date = (
            volatility_data_date.isoformat()
            if volatility_data_date is not None
            else str(raw_volatility_data_date)
        )
        benchmark_data_dates["market_volatility"] = normalized_volatility_data_date

    market_payload: dict[str, Any] = {
        "index_symbol": index_symbol,
        "yfinance_symbol": yfinance_symbol,
        "regime": regime,
        "freshness": "fresh",
        "data_date": data_date.isoformat(),
        "close": close,
        "previous_close": previous_close,
        "ma20": ma20,
        "ma60": ma60,
        "above_ma20": above_ma20,
        "above_ma60": above_ma60,
        "volatility_state": volatility_state,
        "market_risk_flags": risk_flags,
    }
    if raw_volatility_data_date is not None:
        market_payload["volatility_data_date"] = normalized_volatility_data_date

    return {
        "record_date": run_date.isoformat(),
        "data_dates": context_data_dates,
        "benchmark": {
            "symbol": index_symbol,
            "yfinance_symbol": yfinance_symbol,
            "price_history": _price_history(payload),
            "data_dates": benchmark_data_dates,
        },
        "market": market_payload,
    }


def market_context_refresh_error(
    context: Mapping[str, Any],
    *,
    run_date: date,
) -> dict[str, Any] | None:
    market = _mapping(context.get("market"))
    freshness = str(market.get("freshness") or "missing")
    missing_reason = str(market.get("missing_reason") or "").strip() or None
    if context.get("record_date") != run_date.isoformat():
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": "stale",
            "missing_reason": "market_context_record_date_mismatch",
        }
    if freshness != "fresh":
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": freshness,
            "missing_reason": missing_reason or "market_context_not_fresh",
        }
    data_date_value = market.get("data_date")
    try:
        data_date = date.fromisoformat(str(data_date_value))
    except (TypeError, ValueError):
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": "missing",
            "missing_reason": "market_index_data_date_missing",
        }
    if data_date != run_date:
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": "stale",
            "missing_reason": "market_index_stale",
        }
    volatility_data_date_value = market.get("volatility_data_date")
    if volatility_data_date_value is not None:
        try:
            volatility_data_date = date.fromisoformat(str(volatility_data_date_value))
        except (TypeError, ValueError):
            volatility_data_date = None
        if (
            volatility_data_date is None
            or volatility_data_date > data_date
            or (data_date - volatility_data_date).days > MAX_MARKET_VOLATILITY_LAG_DAYS
        ):
            return {
                "code": "daily_radar_market_context_incomplete",
                "freshness": "stale",
                "missing_reason": "market_index_volatility_date_invalid",
            }
    required_values = (
        market.get("close"),
        market.get("previous_close"),
        market.get("ma20"),
        market.get("ma60"),
    )
    if (
        any(_float(value) is None for value in required_values)
        or market.get("volatility_state") == "unknown"
        or market.get("regime") not in {"constructive", "neutral", "risk_off"}
    ):
        return {
            "code": "daily_radar_market_context_incomplete",
            "freshness": freshness,
            "missing_reason": "market_index_indicators_incomplete",
        }
    return None


def _missing_context(
    *,
    run_date: date,
    index_symbol: str,
    yfinance_symbol: str,
    freshness: str,
    missing_reason: str,
    data_date: date | None,
) -> dict[str, Any]:
    data_dates = {"market_index": data_date.isoformat()} if data_date is not None else {}
    return {
        "record_date": run_date.isoformat(),
        "data_dates": data_dates,
        "market": {
            "index_symbol": index_symbol,
            "yfinance_symbol": yfinance_symbol,
            "regime": "unknown",
            "freshness": freshness,
            "data_date": data_date.isoformat() if data_date is not None else None,
            "missing_reason": missing_reason,
            "volatility_state": "unknown",
            "market_risk_flags": [f"market_context_{freshness}"],
        },
    }


def _market_index_data_date(payload: Mapping[str, Any]) -> date | None:
    data_dates = _mapping(payload.get("data_dates"))
    value = data_dates.get("ohlcv") or data_dates.get("market_index") or data_dates.get("technical_indicators")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _market_volatility_data_date(payload: Mapping[str, Any]) -> date | None:
    value = _mapping(payload.get("data_dates")).get("market_volatility")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _complete_market_index_frame(frame: Any) -> Any:
    if getattr(frame, "empty", False) or not hasattr(frame, "iloc"):
        return frame
    columns = {
        field_name: _matching_column_name(frame, field_name)
        for field_name in MARKET_INDEX_OHLCV_FIELDS
    }
    if any(column_name is None for column_name in columns.values()):
        return frame.iloc[0:0]
    valid_rows = []
    for position in range(len(frame)):
        row = frame.iloc[position]
        values = {
            field_name: _float(row[column_name])
            for field_name, column_name in columns.items()
        }
        valid_rows.append(
            all(
                value is not None
                and (value >= 0 if field_name == "Volume" else value > 0)
                for field_name, value in values.items()
            )
        )
    return frame.loc[valid_rows]


def _matching_column_name(frame: Any, field_name: str) -> Any:
    for column in getattr(frame, "columns", []):
        if str(column).lower() == field_name.lower():
            return column
    return None


def _official_market_index_snapshot(
    payload: Any,
    *,
    run_date: date,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, Mapping):
        return None, "twse_market_index_payload_invalid"
    status = str(payload.get("stat") or "").strip()
    if any(marker in status for marker in TWSE_NO_DATA_MARKERS):
        return None, "twse_market_index_no_data"
    if status != "OK":
        return None, "twse_market_index_payload_invalid"
    if str(payload.get("date") or "") != run_date.strftime("%Y%m%d"):
        return None, "twse_market_index_date_mismatch"
    tables = payload.get("tables")
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        return None, "twse_market_index_payload_invalid"
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        fields = table.get("fields")
        rows = table.get("data")
        if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
            continue
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        field_positions = {str(field): index for index, field in enumerate(fields)}
        required_fields = ("指數", "收盤指數", "漲跌(+/-)", "漲跌點數")
        if any(field not in field_positions for field in required_fields):
            continue
        for row in rows:
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
                continue
            try:
                name = str(row[field_positions["指數"]]).strip()
                close = _market_number(row[field_positions["收盤指數"]])
                direction = _html_text(row[field_positions["漲跌(+/-)"]])
                change = _market_number(row[field_positions["漲跌點數"]])
            except IndexError:
                continue
            if name != TWSE_WEIGHTED_INDEX_NAME:
                continue
            if close is None or close <= 0 or change is None or change < 0:
                return None, "twse_market_index_value_invalid"
            if change == 0:
                previous_close = close
            elif direction == "+":
                previous_close = round(close - change, 2)
            elif direction == "-":
                previous_close = round(close + change, 2)
            else:
                return None, "twse_market_index_direction_invalid"
            if previous_close <= 0:
                return None, "twse_market_index_value_invalid"
            return {
                "data_date": run_date.isoformat(),
                "close": close,
                "previous_close": previous_close,
            }, None
    return None, "twse_market_index_row_missing"


def _official_history(
    payload: Mapping[str, Any],
    *,
    run_date: date,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    history: list[dict[str, Any]] = []
    for item in _price_history(payload):
        try:
            item_date = date.fromisoformat(str(item.get("date")))
        except (TypeError, ValueError):
            continue
        item_close = _float(item.get("close"))
        if item_close is None or item_date >= run_date:
            continue
        history.append({"date": item_date.isoformat(), "close": item_close})
    if len(history) < 60:
        return None, "twse_market_index_history_insufficient"

    history_data_date = date.fromisoformat(history[-1]["date"])
    if (run_date - history_data_date).days > MAX_MARKET_VOLATILITY_LAG_DAYS:
        return None, "twse_market_index_history_stale"
    return history, None


def _payload_with_official_closes(
    payload: Mapping[str, Any],
    *,
    official_snapshots: Sequence[Mapping[str, Any]],
    run_date: date,
) -> tuple[dict[str, Any] | None, str | None]:
    history, history_error = _official_history(
        payload,
        run_date=run_date,
    )
    if history is None:
        return None, history_error

    history_data_date = date.fromisoformat(history[-1]["date"])
    previous_data_date = history_data_date
    previous_close = _float(history[-1].get("close"))
    for snapshot in official_snapshots:
        try:
            snapshot_date = date.fromisoformat(str(snapshot.get("data_date")))
        except (TypeError, ValueError):
            return None, "twse_market_index_date_mismatch"
        official_close = _float(snapshot.get("close"))
        official_previous_close = _float(snapshot.get("previous_close"))
        if (
            previous_close is None
            or official_close is None
            or official_previous_close is None
        ):
            return None, "twse_market_index_value_invalid"
        if snapshot_date <= previous_data_date or snapshot_date > run_date:
            return None, "twse_market_index_date_mismatch"
        tolerance = max(0.1, abs(official_previous_close) * 0.000005)
        if abs(previous_close - official_previous_close) > tolerance:
            return None, "twse_market_index_previous_close_mismatch"
        history.append({"date": snapshot_date.isoformat(), "close": official_close})
        previous_data_date = snapshot_date
        previous_close = official_close

    if previous_data_date != run_date:
        return None, "twse_market_index_run_date_missing"
    official_close = _float(official_snapshots[-1].get("close"))
    official_previous_close = _float(official_snapshots[-1].get("previous_close"))
    if official_close is None or official_previous_close is None:
        return None, "twse_market_index_value_invalid"

    closes = [float(item["close"]) for item in history]
    indicators = dict(_mapping(payload.get("indicators")))
    atr14 = _float(indicators.get("atr14"))
    if atr14 is None:
        return None, "twse_market_index_volatility_history_incomplete"
    indicators.update(
        {
            "ma20": sum(closes[-20:]) / 20,
            "ma60": sum(closes[-60:]) / 60,
            "atr14": atr14,
        }
    )
    return {
        **dict(payload),
        "price_history": history,
        "ohlcv": {
            **dict(_mapping(payload.get("ohlcv"))),
            "close": official_close,
            "previous_close": official_previous_close,
        },
        "indicators": indicators,
        "data_dates": {
            **dict(_mapping(payload.get("data_dates"))),
            "ohlcv": run_date.isoformat(),
            "technical_indicators": run_date.isoformat(),
            "market_index": run_date.isoformat(),
            "market_volatility": history_data_date.isoformat(),
        },
    }, None


def _payload_recency_rank(payload: Mapping[str, Any]) -> tuple[int, int]:
    data_date = _market_index_data_date(payload)
    return (
        data_date.toordinal() if data_date is not None else -1,
        len(_price_history(payload)),
    )


def _trailing_history_gap_detected(
    payload: Mapping[str, Any],
    *,
    run_date: date,
) -> bool:
    history_dates: list[date] = []
    for item in _price_history(payload):
        try:
            item_date = date.fromisoformat(str(item.get("date")))
        except (TypeError, ValueError):
            continue
        if item_date <= run_date and _float(item.get("close")) is not None:
            history_dates.append(item_date)
    history_dates = sorted(set(history_dates))
    if len(history_dates) < 2 or history_dates[-1] != run_date:
        return False
    query_date = history_dates[-2] + timedelta(days=1)
    while query_date < run_date:
        if query_date.weekday() < 5:
            return True
        query_date += timedelta(days=1)
    return False


def _market_number(value: Any) -> float | None:
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    return _float(value)


def _html_text(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def _price_history(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"date": str(item.get("date")), "close": item.get("close")}
        for item in _as_list(payload.get("price_history"))
        if isinstance(item, Mapping)
    ]


def _volatility_state(close: float | None, atr14: float | None) -> str:
    if close is None or close <= 0 or atr14 is None:
        return "unknown"
    atr_pct = atr14 / close
    if atr_pct >= 0.04:
        return "high"
    if atr_pct >= 0.03:
        return "elevated"
    if atr_pct >= 0.02:
        return "stable"
    return "normal"


def _regime(
    *,
    close: float | None,
    previous_close: float | None,
    above_ma20: bool | None,
    above_ma60: bool | None,
    volatility_state: str,
) -> str:
    if above_ma20 is False or above_ma60 is False or volatility_state in {"elevated", "high"}:
        return "risk_off"
    if above_ma20 is True and above_ma60 is True and close is not None and previous_close is not None:
        if close >= previous_close and volatility_state in {"normal", "stable"}:
            return "constructive"
    return "neutral"


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _context_freshness_rank(context: Mapping[str, Any]) -> int:
    freshness = _mapping(context.get("market")).get("freshness")
    return {"fresh": 2, "stale": 1, "missing": 0}.get(str(freshness), -1)


__all__ = [
    "MarketIndexContextProvider",
    "YFinanceMarketIndexContextProvider",
    "build_market_context_from_technical_payload",
    "market_context_refresh_error",
]
