from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.forward_validation import number, number_or_default
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
    output: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        price = _price_row_from_raw_data(row)
        if price is not None:
            output.setdefault(row.symbol, []).append(price)
    return output


def load_benchmark_prices_from_prepared_market_context(
    session: Session,
    *,
    market: str,
    benchmark_symbol: str,
    as_of_date: date,
) -> list[dict[str, Any]]:
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
        if rows:
            return sorted(rows, key=lambda row: str(row["date"]))
    return []


def _price_row_from_raw_data(row: StockRawData) -> dict[str, Any] | None:
    technical = _mapping(row.technical)
    ohlcv = _mapping(technical.get("ohlcv") or technical)
    close = number(ohlcv.get("close"))
    if close is None or close <= 0:
        return None
    return {
        "date": row.record_date.isoformat(),
        "open": number_or_default(ohlcv.get("open"), close),
        "high": number_or_default(ohlcv.get("high"), close),
        "low": number_or_default(ohlcv.get("low"), close),
        "close": close,
    }


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
    "load_benchmark_prices_from_prepared_market_context",
    "load_price_series_from_raw_data",
]
