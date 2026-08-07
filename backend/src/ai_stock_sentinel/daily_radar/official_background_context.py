from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
import re
from typing import Any

from ai_stock_sentinel.daily_radar.background_context import (
    BACKGROUND_CONTEXT_ALL_CONSUMERS,
    BackgroundContextPayload,
)


TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TPEX_MARGIN_URL = "https://www.tpex.org.tw/www/zh-tw/margin/balance"
TWSE_LENDING_URL = "https://www.twse.com.tw/rwd/zh/lending/t13sa710"

OFFICIAL_BACKGROUND_CONTEXT_CONSUMERS = BACKGROUND_CONTEXT_ALL_CONSUMERS
SUPPORTED_CONTEXT_TYPES = {"full_margin", "lending"}

RequestGetter = Callable[..., Any]


class OfficialBackgroundContextError(RuntimeError):
    def __init__(self, code: str, *, dataset: str) -> None:
        super().__init__(code)
        self.code = code
        self.dataset = dataset


class OfficialBackgroundChipContextProvider:
    """Fetch market-wide TWSE/TPEX chip context without per-symbol requests."""

    provider_name = "official_background_chip_context_provider"

    def __init__(
        self,
        *,
        request_get: RequestGetter | None = None,
        lookback_trading_days: int = 10,
        max_lookback_calendar_days: int = 37,
        lending_window_days: int = 7,
        stale_after_days: int = 5,
        timeout: int = 30,
    ) -> None:
        self._request_get = request_get
        self._lookback_trading_days = max(1, lookback_trading_days)
        self._max_lookback_calendar_days = max(
            self._lookback_trading_days,
            max_lookback_calendar_days,
        )
        self._lending_window_days = max(1, lending_window_days)
        self._stale_after_days = max(0, stale_after_days)
        self._timeout = timeout

    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        requested_symbols = _ordered_taiwan_symbols(symbols)
        supported_symbols = [
            symbol for symbol in requested_symbols if is_official_background_supported_symbol(symbol)
        ]
        unsupported_symbols = [
            symbol for symbol in requested_symbols if symbol not in supported_symbols
        ]
        for context_type in context_types:
            for symbol in unsupported_symbols:
                yield self._missing_payload(
                    symbol=symbol,
                    context_type=context_type,
                    run_date=run_date,
                    market=market,
                    missing_reason="unsupported_official_symbol",
                )
            if context_type not in SUPPORTED_CONTEXT_TYPES:
                for symbol in supported_symbols:
                    yield self._missing_payload(
                        symbol=symbol,
                        context_type=context_type,
                        run_date=run_date,
                        market=market,
                        missing_reason="unsupported_context_type",
                    )
                continue
            if context_type == "full_margin":
                yield from self._fetch_full_margin(
                    symbols=supported_symbols,
                    run_date=run_date,
                    market=market,
                )
            else:
                yield from self._fetch_lending(
                    symbols=supported_symbols,
                    run_date=run_date,
                    market=market,
                )

    def _fetch_full_margin(
        self,
        *,
        symbols: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        by_market = {
            "TW": [symbol for symbol in symbols if symbol.endswith(".TW")],
            "TWO": [symbol for symbol in symbols if symbol.endswith(".TWO")],
        }
        observations: dict[str, list[tuple[date, Mapping[str, Any]]]] = defaultdict(list)
        market_dates: dict[str, set[date]] = defaultdict(set)
        request_get = self._request_get or _import_requests_get()

        for market_code, market_symbols in by_market.items():
            if not market_symbols:
                continue
            requested_ids = {_stock_id(symbol): symbol for symbol in market_symbols}
            for offset in range(self._max_lookback_calendar_days):
                if len(market_dates[market_code]) >= self._lookback_trading_days:
                    break
                query_date = run_date - timedelta(days=offset)
                payload = _request_json(
                    request_get,
                    _margin_url(market_code),
                    params=_margin_params(market_code, query_date),
                    timeout=self._timeout,
                    dataset=_margin_dataset(market_code),
                )
                parsed = _parse_margin_payload(payload, market_code=market_code)
                if parsed is None:
                    continue
                payload_date, rows = parsed
                if payload_date > run_date or not rows or payload_date in market_dates[market_code]:
                    continue
                market_dates[market_code].add(payload_date)
                for stock_id, row in rows.items():
                    symbol = requested_ids.get(stock_id)
                    if symbol is not None:
                        observations[symbol].append((payload_date, row))

            if not market_dates[market_code]:
                raise OfficialBackgroundContextError(
                    "official_margin_market_date_unavailable",
                    dataset=_margin_dataset(market_code),
                )

        for symbol in symbols:
            rows = sorted(observations.get(symbol, []), key=lambda item: item[0])
            if not rows:
                yield self._missing_payload(
                    symbol=symbol,
                    context_type="full_margin",
                    run_date=run_date,
                    market=market,
                    missing_reason="official_no_data",
                    dataset=_margin_dataset(_symbol_market(symbol)),
                )
                continue
            latest_date, latest = rows[-1]
            _first_date, first = rows[0]
            freshness, missing_reason = self._freshness(as_of_date=latest_date, run_date=run_date)
            margin_latest = _optional_float(latest.get("margin_balance"))
            margin_start = _optional_float(first.get("margin_previous_balance"))
            short_latest = _optional_float(latest.get("short_balance"))
            short_start = _optional_float(first.get("short_previous_balance"))
            dataset = _margin_dataset(_symbol_market(symbol))
            yield BackgroundContextPayload(
                symbol=symbol,
                context_type="full_margin",
                applicable_consumers=OFFICIAL_BACKGROUND_CONTEXT_CONSUMERS,
                source=self._source(market=_symbol_market(symbol), dataset=dataset),
                as_of_date=latest_date,
                freshness=freshness,
                payload={
                    "provider": self.provider_name,
                    "dataset": dataset,
                    "lookback_trading_days": self._lookback_trading_days,
                    "row_count": len(rows),
                    "unit": "trading_lots",
                    "latest_margin_balance": margin_latest,
                    "latest_short_balance": short_latest,
                    "margin_balance_delta": _delta(margin_latest, margin_start),
                    "margin_balance_delta_pct": _delta_pct(margin_latest, margin_start),
                    "short_balance_delta": _delta(short_latest, short_start),
                    "short_balance_delta_pct": _delta_pct(short_latest, short_start),
                    "data_dates": [row_date.isoformat() for row_date, _row in rows],
                },
                missing_reason=missing_reason,
                replay_key=_replay_key(symbol, "full_margin", latest_date),
            )

    def _fetch_lending(
        self,
        *,
        symbols: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        request_get = self._request_get or _import_requests_get()
        requested_ids = {_stock_id(symbol): symbol for symbol in symbols}
        daily: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
        start_date = run_date - timedelta(days=self._max_lookback_calendar_days - 1)
        cursor = start_date
        while cursor <= run_date:
            window_end = min(run_date, cursor + timedelta(days=self._lending_window_days - 1))
            payload = _request_json(
                request_get,
                TWSE_LENDING_URL,
                params={
                    "startDate": cursor.strftime("%Y%m%d"),
                    "endDate": window_end.strftime("%Y%m%d"),
                    "response": "json",
                },
                timeout=self._timeout,
                dataset="TWSE_t13sa710",
            )
            for row_date, stock_id, volume in _parse_lending_payload(payload):
                if row_date > run_date:
                    continue
                symbol = requested_ids.get(stock_id)
                if symbol is not None:
                    daily[symbol][row_date] += volume
            cursor = window_end + timedelta(days=1)

        for symbol in symbols:
            daily_points = sorted(daily.get(symbol, {}).items())[-self._lookback_trading_days :]
            if not daily_points:
                yield self._missing_payload(
                    symbol=symbol,
                    context_type="lending",
                    run_date=run_date,
                    market=market,
                    missing_reason="official_no_data",
                    dataset="TWSE_t13sa710",
                )
                continue
            latest_date, latest_volume = daily_points[-1]
            first_volume = daily_points[0][1]
            freshness, missing_reason = self._freshness(as_of_date=latest_date, run_date=run_date)
            yield BackgroundContextPayload(
                symbol=symbol,
                context_type="lending",
                applicable_consumers=OFFICIAL_BACKGROUND_CONTEXT_CONSUMERS,
                source=self._source(market=_symbol_market(symbol), dataset="TWSE_t13sa710"),
                as_of_date=latest_date,
                freshness=freshness,
                payload={
                    "provider": self.provider_name,
                    "dataset": "TWSE_t13sa710",
                    "lookback_trading_days": self._lookback_trading_days,
                    "row_count": len(daily_points),
                    "daily_point_count": len(daily_points),
                    "unit": "twse_lending_trading_unit",
                    "latest_daily_lending_volume": latest_volume,
                    "period_lending_volume": sum(volume for _dt, volume in daily_points),
                    "lending_volume_delta": (
                        latest_volume - first_volume if len(daily_points) >= 2 else None
                    ),
                    "data_dates": [row_date.isoformat() for row_date, _volume in daily_points],
                },
                missing_reason=missing_reason,
                replay_key=_replay_key(symbol, "lending", latest_date),
            )

    def _freshness(self, *, as_of_date: date, run_date: date) -> tuple[str, str | None]:
        if (run_date - as_of_date).days <= self._stale_after_days:
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
        dataset: str | None = None,
    ) -> BackgroundContextPayload:
        return BackgroundContextPayload(
            symbol=symbol,
            context_type=context_type,
            applicable_consumers=OFFICIAL_BACKGROUND_CONTEXT_CONSUMERS,
            source=self._source(market=_symbol_market(symbol), dataset=dataset),
            as_of_date=None,
            freshness="missing",
            payload={},
            missing_reason=missing_reason,
            replay_key=(
                f"background_context:{symbol}:{context_type}:{run_date.isoformat()}:"
                f"missing:{missing_reason}"
            ),
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


def _request_json(
    request_get: RequestGetter,
    url: str,
    *,
    params: Mapping[str, Any],
    timeout: int,
    dataset: str,
) -> Mapping[str, Any]:
    try:
        response = request_get(
            url,
            params=dict(params),
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else response
    except Exception as exc:
        raise OfficialBackgroundContextError(
            "official_request_failed",
            dataset=dataset,
        ) from exc
    if not isinstance(payload, Mapping):
        raise OfficialBackgroundContextError(
            "official_response_invalid",
            dataset=dataset,
        )
    return payload


def _parse_margin_payload(
    payload: Mapping[str, Any],
    *,
    market_code: str,
) -> tuple[date, dict[str, dict[str, float | None]]] | None:
    stat = str(payload.get("stat") or "").strip().lower()
    if stat not in {"ok"}:
        if _is_no_data_status(stat):
            return None
        raise OfficialBackgroundContextError(
            "official_margin_response_error",
            dataset=_margin_dataset(market_code),
        )
    raw_date = str(payload.get("date") or "").strip()
    try:
        payload_date = _compact_date(raw_date)
    except ValueError as exc:
        raise OfficialBackgroundContextError(
            "official_margin_date_invalid",
            dataset=_margin_dataset(market_code),
        ) from exc
    table = _first_margin_table(payload, market_code=market_code)
    data = table.get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise OfficialBackgroundContextError(
            "official_margin_rows_invalid",
            dataset=_margin_dataset(market_code),
        )
    rows: dict[str, dict[str, float | None]] = {}
    indexes = (0, 5, 6, 11, 12) if market_code == "TW" else (0, 2, 6, 10, 14)
    for row in data:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        if max(indexes) >= len(row):
            raise OfficialBackgroundContextError(
                "official_margin_schema_changed",
                dataset=_margin_dataset(market_code),
            )
        stock_id = str(row[indexes[0]] or "").strip()
        if not stock_id:
            continue
        rows[stock_id] = {
            "margin_previous_balance": _optional_float(row[indexes[1]]),
            "margin_balance": _optional_float(row[indexes[2]]),
            "short_previous_balance": _optional_float(row[indexes[3]]),
            "short_balance": _optional_float(row[indexes[4]]),
        }
    return payload_date, rows


def _first_margin_table(payload: Mapping[str, Any], *, market_code: str) -> Mapping[str, Any]:
    tables = payload.get("tables")
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        raise OfficialBackgroundContextError(
            "official_margin_tables_invalid",
            dataset=_margin_dataset(market_code),
        )
    if market_code == "TW":
        for table in tables:
            if not isinstance(table, Mapping):
                continue
            fields = table.get("fields")
            if isinstance(fields, Sequence) and not isinstance(fields, (str, bytes)):
                normalized = [str(field) for field in fields]
                if "代號" in normalized and normalized.count("今日餘額") >= 2:
                    return table
    elif tables and isinstance(tables[0], Mapping):
        return tables[0]
    raise OfficialBackgroundContextError(
        "official_margin_table_not_found",
        dataset=_margin_dataset(market_code),
    )


def _parse_lending_payload(payload: Mapping[str, Any]) -> list[tuple[date, str, float]]:
    stat = str(payload.get("stat") or "").strip().lower()
    if stat not in {"ok"}:
        if _is_no_data_status(stat):
            return []
        raise OfficialBackgroundContextError(
            "official_lending_response_error",
            dataset="TWSE_t13sa710",
        )
    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise OfficialBackgroundContextError(
            "official_lending_fields_invalid",
            dataset="TWSE_t13sa710",
        )
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise OfficialBackgroundContextError(
            "official_lending_rows_invalid",
            dataset="TWSE_t13sa710",
        )
    indexes = {str(field): index for index, field in enumerate(fields)}
    required = ("成交日期", "證券代號名稱", "成交數量(交易單位)")
    if any(field not in indexes for field in required):
        raise OfficialBackgroundContextError(
            "official_lending_schema_changed",
            dataset="TWSE_t13sa710",
        )
    normalized: list[tuple[date, str, float]] = []
    for row in data:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        if max(indexes[field] for field in required) >= len(row):
            continue
        try:
            row_date = _roc_text_date(str(row[indexes["成交日期"]]))
            stock_id = str(row[indexes["證券代號名稱"]]).strip().split(maxsplit=1)[0]
            volume = _required_float(row[indexes["成交數量(交易單位)"]])
        except (ValueError, IndexError):
            continue
        if stock_id:
            normalized.append((row_date, stock_id, volume))
    return normalized


def _margin_url(market_code: str) -> str:
    return TWSE_MARGIN_URL if market_code == "TW" else TPEX_MARGIN_URL


def _margin_dataset(market_code: str) -> str:
    return "TWSE_MI_MARGN" if market_code == "TW" else "TPEX_margin_balance"


def _margin_params(market_code: str, query_date: date) -> dict[str, str]:
    params = {
        "date": (
            query_date.strftime("%Y%m%d")
            if market_code == "TW"
            else query_date.strftime("%Y/%m/%d")
        ),
        "response": "json",
    }
    if market_code == "TW":
        params["selectType"] = "ALL"
    return params


def _symbol_market(symbol: str) -> str:
    return "TWO" if symbol.endswith(".TWO") else "TW"


def _stock_id(symbol: str) -> str:
    return symbol.upper().removesuffix(".TWO").removesuffix(".TW")


def is_official_background_supported_symbol(symbol: str) -> bool:
    normalized = str(symbol).strip().upper()
    stock_id = _stock_id(normalized)
    return (
        normalized.endswith((".TW", ".TWO"))
        and re.fullmatch(r"[1-9]\d{3}", stock_id) is not None
    )


def _ordered_taiwan_symbols(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if symbol in seen or not (symbol.endswith(".TW") or symbol.endswith(".TWO")):
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _compact_date(value: str) -> date:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) != 8:
        raise ValueError("invalid compact date")
    return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))


def _roc_text_date(value: str) -> date:
    digits: list[str] = []
    current = ""
    for char in value:
        if char.isdigit():
            current += char
        elif current:
            digits.append(current)
            current = ""
    if current:
        digits.append(current)
    if len(digits) < 3:
        raise ValueError("invalid ROC date")
    return date(int(digits[0]) + 1911, int(digits[1]), int(digits[2]))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    normalized = str(value).replace(",", "").strip()
    if normalized in {"", "--", "-"}:
        return 0.0
    try:
        return float(normalized)
    except (TypeError, ValueError):
        return None


def _required_float(value: Any) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        raise ValueError("invalid number")
    return parsed


def _is_no_data_status(stat: str) -> bool:
    return any(marker in stat for marker in ("沒有符合條件", "查無資料", "no data"))


def _delta(latest: float | None, start: float | None) -> float | None:
    if latest is None or start is None:
        return None
    return latest - start


def _delta_pct(latest: float | None, start: float | None) -> float | None:
    if latest is None or start in (None, 0):
        return None
    return (latest - start) / start * 100


def _replay_key(symbol: str, context_type: str, as_of_date: date) -> str:
    return f"background_context:{symbol}:{context_type}:{as_of_date.isoformat()}"


def _import_requests_get() -> RequestGetter:
    try:
        import requests
    except ImportError as exc:
        raise OfficialBackgroundContextError(
            "missing_dependency",
            dataset="official_background_context",
        ) from exc
    return requests.get


__all__ = [
    "OFFICIAL_BACKGROUND_CONTEXT_CONSUMERS",
    "OfficialBackgroundChipContextProvider",
    "OfficialBackgroundContextError",
    "TPEX_MARGIN_URL",
    "TWSE_LENDING_URL",
    "TWSE_MARGIN_URL",
    "is_official_background_supported_symbol",
]
