from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from itertools import groupby
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.forward_validation import (
    OHLC_PRICE_FIELDS,
    PRICE_CONFLICT_FIELDS_KEY,
    number,
    price_conflict_fields,
)
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, StockRawData


RAW_PRICE_BATCH_SIZE = 128


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
    rows = session.execute(
        select(StockRawData.symbol, StockRawData.technical)
        .where(
            StockRawData.symbol.in_(normalized_symbols),
            StockRawData.record_date >= start_date,
            StockRawData.record_date <= end_date,
            StockRawData.raw_data_is_final.is_(True),
        )
        .order_by(StockRawData.symbol.asc(), StockRawData.record_date.asc())
        .execution_options(yield_per=RAW_PRICE_BATCH_SIZE)
    ).mappings()
    output: dict[str, list[dict[str, Any]]] = {}
    try:
        for symbol, symbol_rows in groupby(rows, key=lambda row: row["symbol"]):
            output[symbol] = completed_price_rows_from_raw_data(
                symbol_rows,
                start_date=start_date,
                end_date=end_date,
            )
    finally:
        rows.close()
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
    prepared_runs = session.execute(
        select(DailyRadarPreparedRun.market_context["benchmark"].as_json())
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
        .execution_options(yield_per=1)
    ).scalars()
    required = set(required_dates)
    best_rows: list[dict[str, Any]] = []
    best_coverage = -1
    try:
        for prepared_benchmark in prepared_runs:
            benchmark = _mapping(prepared_benchmark)
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
    finally:
        prepared_runs.close()
    return best_rows


def load_benchmark_calendar_from_prepared_market_context(
    session: Session,
    *,
    market: str,
    benchmark_symbol: str,
    start_date: date,
    as_of_date: date,
) -> list[date]:
    """Union saved trade dates across the research range, without merging prices.

    A prepared payload holds only a rolling price history. Research confidence
    needs older dates too, so stream all in-range histories rather than choosing
    one price snapshot or limiting discovery to the newest thirty runs.
    """
    bind = session.get_bind()
    if bind is None or not inspect(bind).has_table("daily_radar_prepared_runs"):
        return []
    rows = session.execute(
        select(
            DailyRadarPreparedRun.run_date,
            DailyRadarPreparedRun.market_context["benchmark"]["price_history"].as_json(),
        )
        .where(
            DailyRadarPreparedRun.market == market,
            DailyRadarPreparedRun.run_date >= start_date,
            DailyRadarPreparedRun.run_date <= as_of_date,
            DailyRadarPreparedRun.market_context["benchmark"]["symbol"].as_string() == benchmark_symbol,
        )
        .execution_options(yield_per=1)
    )
    days: set[date] = set()
    try:
        for run_date, history in rows:
            for item in _as_list(history):
                if not isinstance(item, Mapping):
                    continue
                day = _parse_date(item.get("date"))
                close = number(item.get("close"))
                if day is not None and start_date <= day <= run_date and close is not None and close > 0:
                    days.add(day)
    finally:
        rows.close()
    return sorted(days)


def completed_price_rows_from_raw_data(
    rows: Iterable[Any],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Extract prices by proven embedded trade date, never observation date."""
    prices_by_date: dict[date, dict[str, Any]] = {}
    evidence_by_date: dict[date, dict[str, Any]] = {}

    def store(raw_date: Any, price: Mapping[str, Any]) -> None:
        value_date = _parse_date(raw_date)
        if (value_date is None or (start_date is not None and value_date < start_date)
                or (end_date is not None and value_date > end_date)):
            return
        _store_completed_price(prices_by_date, value_date, price, evidence_by_date=evidence_by_date)

    for row in rows:
        if _row_value(row, "raw_data_is_final") is False:
            continue
        technical = _mapping(_row_value(row, "technical"))
        for item in _as_list(technical.get("price_history")):
            if isinstance(item, Mapping):
                store(item.get("date"), item)

        recent_closes = _as_list(technical.get("recent_closes"))
        recent_dates = _as_list(technical.get("recent_close_dates"))
        if len(recent_closes) == len(recent_dates):
            for value_date, close in zip(recent_dates, recent_closes, strict=True):
                store(value_date, {"close": close})

        data_dates = _mapping(technical.get("data_dates"))
        ohlcv = _mapping(technical.get("ohlcv") or technical)
        store(data_dates.get("ohlcv"), ohlcv)

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
    *,
    evidence_by_date: dict[date, dict[str, Any]],
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
    # Keep valid comparison evidence separate from the legacy numeric projection:
    # an intervening zero/negative field must not hide two differing valid values.
    previous_evidence = evidence_by_date.get(value_date, {})
    conflicts = price_conflict_fields(previous_evidence, price)
    evidence_by_date[value_date] = previous_evidence | {
        field: value for field in OHLC_PRICE_FIELDS
        if (value := candidate.get(field)) is not None and value > 0
    }
    if conflicts:
        evidence_by_date[value_date][PRICE_CONFLICT_FIELDS_KEY] = conflicts
        output[value_date][PRICE_CONFLICT_FIELDS_KEY] = conflicts


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
    "load_benchmark_calendar_from_prepared_market_context",
    "load_benchmark_prices_from_prepared_market_context",
    "load_price_series_from_raw_data",
]
