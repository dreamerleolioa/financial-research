from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.forward_validation import number
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, StockRawData


def load_price_series_from_raw_data(
    session: Session,
    *,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
) -> dict[str, list[dict[str, Any]]]:
    normalized_symbols = sorted({str(symbol) for symbol in symbols if symbol})
    if not normalized_symbols:
        return {}
    rows = session.scalars(
        select(StockRawData)
        .where(
            StockRawData.symbol.in_(normalized_symbols),
            StockRawData.record_date >= start_date,
            StockRawData.record_date <= end_date,
            StockRawData.raw_data_is_final.is_(True),
        )
        .order_by(StockRawData.symbol.asc(), StockRawData.record_date.asc())
    ).all()
    raw_rows_by_symbol: dict[str, list[StockRawData]] = {}
    for row in rows:
        raw_rows_by_symbol.setdefault(row.symbol, []).append(row)
    output: dict[str, list[dict[str, Any]]] = {}
    for symbol, symbol_rows in raw_rows_by_symbol.items():
        output[symbol] = completed_price_rows_from_raw_data(
            symbol_rows,
            start_date=start_date,
            end_date=end_date,
        )
    return output


def load_benchmark_prices_from_prepared_market_context(
    session: Session,
    *,
    market: str,
    benchmark_symbol: str,
    as_of_date: date,
    required_dates: Sequence[date] = (),
) -> list[dict[str, Any]]:
    bind = session.get_bind()
    if bind is None or not inspect(bind).has_table("daily_radar_prepared_runs"):
        return []
    prepared_runs = session.scalars(
        select(DailyRadarPreparedRun)
        .where(
            DailyRadarPreparedRun.market == market,
            DailyRadarPreparedRun.run_date <= as_of_date,
        )
        .order_by(
            DailyRadarPreparedRun.run_date.desc(),
            DailyRadarPreparedRun.updated_at.desc(),
            DailyRadarPreparedRun.id.desc(),
        )
        .limit(30)
    ).all()
    required = set(required_dates)
    best_rows: list[dict[str, Any]] = []
    best_coverage = -1
    for prepared in prepared_runs:
        benchmark = _mapping(_mapping(prepared.market_context).get("benchmark"))
        if str(benchmark.get("symbol") or "") != benchmark_symbol:
            continue
        rows: list[dict[str, Any]] = []
        for item in _as_list(benchmark.get("price_history")):
            if not isinstance(item, Mapping):
                continue
            row_date = _parse_date(item.get("date"))
            close = number(item.get("close"))
            if (
                row_date is None
                or row_date > as_of_date
                or close is None
                or close <= 0
            ):
                continue
            rows.append({
                "date": row_date.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
            })
        if not rows:
            continue
        rows = sorted(rows, key=lambda row: str(row["date"]))
        available_dates = {
            row_date
            for row in rows
            for row_date in [_parse_date(row.get("date"))]
            if row_date is not None
        }
        coverage = len(required.intersection(available_dates))
        if coverage > best_coverage or (
            coverage == best_coverage and len(rows) > len(best_rows)
        ):
            best_rows = rows
            best_coverage = coverage
        if required.issubset(available_dates):
            return rows
    return best_rows


def completed_price_rows_from_raw_data(
    rows: Sequence[Any],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Extract prices by proven embedded trade date, never observation date."""
    prices_by_date: dict[date, dict[str, Any]] = {}
    for row in rows:
        if _row_value(row, "raw_data_is_final") is False:
            continue
        technical = _mapping(_row_value(row, "technical"))
        for item in _as_list(technical.get("price_history")):
            if isinstance(item, Mapping):
                _store_completed_price(prices_by_date, item.get("date"), item)

        recent_closes = _as_list(technical.get("recent_closes"))
        recent_dates = _as_list(technical.get("recent_close_dates"))
        if len(recent_closes) == len(recent_dates):
            for value_date, close in zip(recent_dates, recent_closes, strict=True):
                _store_completed_price(prices_by_date, value_date, {"close": close})

        data_dates = _mapping(technical.get("data_dates"))
        ohlcv = _mapping(technical.get("ohlcv") or technical)
        _store_completed_price(prices_by_date, data_dates.get("ohlcv"), ohlcv)

    return [
        prices_by_date[value_date]
        for value_date in sorted(prices_by_date)
        if (start_date is None or value_date >= start_date)
        and (end_date is None or value_date <= end_date)
    ]


def _store_completed_price(
    output: dict[date, dict[str, Any]],
    raw_date: Any,
    price: Mapping[str, Any],
) -> None:
    value_date = _parse_date(raw_date)
    close = number(price.get("close"))
    if value_date is None or close is None or close <= 0:
        return
    existing = output.get(value_date, {})
    candidate = {
        "date": value_date.isoformat(),
        "open": number(price.get("open")),
        "high": number(price.get("high")),
        "low": number(price.get("low")),
        "close": close,
    }
    output[value_date] = {
        key: value
        for key in ("date", "open", "high", "low", "close")
        for value in [candidate.get(key) if candidate.get(key) is not None else existing.get(key)]
    }


def _row_value(row: Any, key: str) -> Any:
    return row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = [
    "completed_price_rows_from_raw_data",
    "load_benchmark_prices_from_prepared_market_context",
    "load_price_series_from_raw_data",
]
