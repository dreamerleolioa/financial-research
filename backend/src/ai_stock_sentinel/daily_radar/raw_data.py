from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
import math
from typing import Any, Protocol

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.repository import get_final_raw_data_rows_for_symbols
from ai_stock_sentinel.db.models import StockRawData


class BatchTechnicalFetcher(Protocol):
    def fetch(self, symbols: Sequence[str], *, run_date: date) -> Mapping[str, Mapping[str, Any]]:
        ...


class YFinanceBatchTechnicalFetcher:
    def fetch(self, symbols: Sequence[str], *, run_date: date) -> Mapping[str, Mapping[str, Any]]:
        ordered_symbols = _ordered_unique_symbols(symbols)
        if not ordered_symbols:
            return {}

        start_date = run_date - timedelta(days=120)
        end_date = run_date + timedelta(days=1)
        history = yf.download(
            ordered_symbols,
            group_by="ticker",
            start=start_date,
            end=end_date,
            interval="1d",
            threads=True,
            progress=False,
        )
        payloads: dict[str, Mapping[str, Any]] = {}
        for symbol in ordered_symbols:
            frame = _frame_on_or_before_run_date(_symbol_frame(history, symbol), run_date=run_date)
            payload = _build_technical_payload(symbol, frame, run_date=run_date)
            if payload is not None:
                payloads[symbol] = payload
        return payloads


def ensure_daily_radar_raw_rows(
    session: Session,
    run_date: date,
    symbols: Iterable[str],
    *,
    technical_fetcher: BatchTechnicalFetcher | None = None,
    institutional_payloads_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[StockRawData]:
    ordered_symbols = _ordered_unique_symbols(symbols)
    if not ordered_symbols:
        return []

    institutional_payloads = institutional_payloads_by_symbol or {}
    existing_final = get_final_raw_data_rows_for_symbols(session, run_date=run_date, symbols=ordered_symbols)
    existing_final_symbols = {row.symbol for row in existing_final}
    missing_symbols = [symbol for symbol in ordered_symbols if symbol not in existing_final_symbols]
    if missing_symbols:
        fetcher = technical_fetcher or YFinanceBatchTechnicalFetcher()
        fetched_payloads = fetcher.fetch(missing_symbols, run_date=run_date)
        _store_missing_rows(
            session,
            run_date=run_date,
            symbols=missing_symbols,
            fetched_payloads=fetched_payloads,
            institutional_payloads_by_symbol=institutional_payloads,
        )
    _apply_institutional_payloads(
        session,
        run_date=run_date,
        symbols=ordered_symbols,
        institutional_payloads_by_symbol=institutional_payloads,
    )
    if missing_symbols or institutional_payloads:
        session.flush()

    return get_final_raw_data_rows_for_symbols(session, run_date=run_date, symbols=ordered_symbols)


def _apply_institutional_payloads(
    session: Session,
    *,
    run_date: date,
    symbols: Sequence[str],
    institutional_payloads_by_symbol: Mapping[str, Mapping[str, Any]],
) -> None:
    payload_symbols = [symbol for symbol in symbols if symbol in institutional_payloads_by_symbol]
    if not payload_symbols:
        return

    rows = session.scalars(
        select(StockRawData).where(
            StockRawData.record_date == run_date,
            StockRawData.symbol.in_(payload_symbols),
        )
    ).all()
    for row in rows:
        row.institutional = dict(institutional_payloads_by_symbol[row.symbol])


def _store_missing_rows(
    session: Session,
    *,
    run_date: date,
    symbols: Sequence[str],
    fetched_payloads: Mapping[str, Mapping[str, Any]],
    institutional_payloads_by_symbol: Mapping[str, Mapping[str, Any]],
) -> None:
    stored_rows = session.scalars(
        select(StockRawData).where(
            StockRawData.record_date == run_date,
            StockRawData.symbol.in_(symbols),
        )
    ).all()
    stored_by_symbol = {row.symbol: row for row in stored_rows}

    for symbol in symbols:
        payload = fetched_payloads.get(symbol)
        if payload is None:
            continue
        technical = _normalize_technical_payload(symbol, payload, run_date=run_date)
        institutional = dict(institutional_payloads_by_symbol.get(symbol) or {})
        row = stored_by_symbol.get(symbol)
        if row is None:
            row = StockRawData(symbol=symbol, record_date=run_date)
            session.add(row)
        row.technical = technical
        row.institutional = institutional
        row.fundamental = {"margin": {}, "data_dates": {"margin": run_date.isoformat()}}
        row.raw_data_is_final = True


def _normalize_technical_payload(symbol: str, payload: Mapping[str, Any], *, run_date: date) -> dict[str, Any]:
    technical = dict(payload)
    if "ohlcv" not in technical:
        technical["ohlcv"] = {}
    if "indicators" not in technical:
        technical["indicators"] = {}
    technical["name"] = str(technical.get("name") or symbol)
    technical["ohlcv"] = dict(_mapping(technical.get("ohlcv")))
    technical["indicators"] = dict(_mapping(technical.get("indicators")))
    technical["data_dates"] = {
        "ohlcv": run_date.isoformat(),
        "technical_indicators": run_date.isoformat(),
        **{key: str(value) for key, value in _mapping(technical.get("data_dates")).items()},
    }
    return technical


def _build_technical_payload(symbol: str, frame: Any, *, run_date: date) -> dict[str, Any] | None:
    if not _has_required_ohlcv_data(frame):
        return None

    closes = _series_numbers(frame, "Close")
    opens = _series_numbers(frame, "Open")
    highs = _series_numbers(frame, "High")
    lows = _series_numbers(frame, "Low")
    volumes = _series_numbers(frame, "Volume")
    close = _last(closes)
    previous_close = closes[-2] if len(closes) >= 2 else close
    open_price = _last(opens)
    high = _last(highs)
    low = _last(lows)
    volume = int(_last(volumes))
    avg_volume_20 = _mean(volumes[-20:])
    ma5 = _mean(closes[-5:])
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])
    support = min(lows[-20:]) if lows else low
    resistance = max(highs[-20:]) if highs else high
    macd = _macd(closes)
    kd = _kd(highs, lows, closes)
    obv, obv_trend = _obv(volumes, closes)
    data_date = _last_index_date(frame) or run_date.isoformat()

    return {
        "name": symbol,
        "price_history": _price_history(frame),
        "ohlcv": {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "previous_close": previous_close,
            "volume": volume,
            "avg_volume_20": avg_volume_20,
        },
        "indicators": {
            "ma5": ma5,
            "ma20": ma20,
            "ma60": ma60,
            "rsi14": _rsi(closes, 14),
            "bias20": ((close - ma20) / ma20 * 100) if ma20 else 0.0,
            "volume_ratio": (volume / avg_volume_20) if avg_volume_20 else 0.0,
            "missing_trading_days_60": max(0, 60 - len(closes[-60:])),
            "mfi14": _mfi(highs, lows, closes, volumes, 14),
            "macd": macd["macd"],
            "macd_signal": macd["signal"],
            "macd_histogram": macd["histogram"],
            "kd_k": kd["k"],
            "kd_d": kd["d"],
            "atr14": _atr(highs, lows, closes, 14),
            "support": support,
            "resistance": resistance,
            "support_level": support,
            "resistance_level": resistance,
            "obv": obv,
            "obv_trend": obv_trend,
        },
        "data_dates": {
            "ohlcv": data_date,
            "technical_indicators": data_date,
        },
    }


def _symbol_frame(history: Any, symbol: str) -> Any:
    columns = getattr(history, "columns", None)
    if columns is None:
        return history
    nlevels = getattr(columns, "nlevels", 1)
    if nlevels < 2:
        return history
    level_zero = set(str(value) for value in columns.get_level_values(0))
    if symbol in level_zero:
        return history[symbol]
    level_one = set(str(value) for value in columns.get_level_values(1))
    if symbol in level_one:
        return history.xs(symbol, axis=1, level=1)
    if hasattr(history, "iloc"):
        return history.iloc[0:0]
    return history


def _frame_on_or_before_run_date(frame: Any, *, run_date: date) -> Any:
    index = getattr(frame, "index", None)
    if index is None or len(index) == 0:
        return frame
    mask = [_index_value_date(value) <= run_date for value in index]
    if hasattr(frame, "loc"):
        return frame.loc[mask]
    return frame


def _has_required_ohlcv_data(frame: Any) -> bool:
    if getattr(frame, "empty", False):
        return False
    return all(_series_numbers(frame, field_name) for field_name in ("Open", "High", "Low", "Close", "Volume"))


def _series_numbers(frame: Any, field_name: str) -> list[float]:
    column_name = _matching_column_name(frame, field_name)
    if column_name is None:
        return []
    series = frame[column_name]
    if hasattr(series, "dropna"):
        series = series.dropna()
    values = series.tolist() if hasattr(series, "tolist") else list(series)
    return [_to_float(value) for value in values if _to_float(value) is not None]


def _matching_column_name(frame: Any, field_name: str) -> Any:
    columns = getattr(frame, "columns", [])
    for column in columns:
        if str(column).lower() == field_name.lower():
            return column
    return None


def _last_index_date(frame: Any) -> str | None:
    index = getattr(frame, "index", None)
    if index is None or len(index) == 0:
        return None
    value = index[-1]
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10] or None


def _price_history(frame: Any) -> list[dict[str, Any]]:
    column_name = _matching_column_name(frame, "Close")
    index = getattr(frame, "index", None)
    if column_name is None or index is None:
        return []
    series = frame[column_name]
    if hasattr(series, "items"):
        items = list(series.items())
    else:
        items = list(zip(index, list(series)))

    history: list[dict[str, Any]] = []
    for index_value, close_value in items[-80:]:
        close = _to_float(close_value)
        if close is None:
            continue
        history.append({"date": _index_value_date(index_value).isoformat(), "close": close})
    return history


def _index_value_date(value: Any) -> date:
    if hasattr(value, "date"):
        return value.date()
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return date.max


def _last(values: Sequence[float]) -> float:
    return float(values[-1]) if values else 0.0


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _rsi(closes: Sequence[float], period: int) -> float:
    if len(closes) < 2:
        return 50.0
    deltas = [closes[index] - closes[index - 1] for index in range(1, len(closes))][-period:]
    gains = [delta for delta in deltas if delta > 0]
    losses = [-delta for delta in deltas if delta < 0]
    avg_gain = _mean(gains) if gains else 0.0
    avg_loss = _mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))


def _mfi(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    period: int,
) -> float:
    length = min(len(highs), len(lows), len(closes), len(volumes))
    if length < 2:
        return 50.0
    typical_prices = [(highs[index] + lows[index] + closes[index]) / 3 for index in range(length)]
    positive_flow = 0.0
    negative_flow = 0.0
    start = max(1, length - period)
    for index in range(start, length):
        flow = typical_prices[index] * volumes[index]
        if typical_prices[index] >= typical_prices[index - 1]:
            positive_flow += flow
        else:
            negative_flow += flow
    if negative_flow == 0:
        return 100.0 if positive_flow > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + (positive_flow / negative_flow)))


def _macd(closes: Sequence[float]) -> dict[str, float]:
    if not closes:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    ema12 = _ema_values(closes, 12)
    ema26 = _ema_values(closes, 26)
    macd_values = [fast - slow for fast, slow in zip(ema12, ema26)]
    macd_line = macd_values[-1]
    signal = _ema(macd_values, 9)
    return {"macd": macd_line, "signal": signal, "histogram": macd_line - signal}


def _ema(values: Sequence[float], period: int) -> float:
    ema_values = _ema_values(values, period)
    return ema_values[-1] if ema_values else 0.0


def _ema_values(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    smoothing = 2 / (period + 1)
    ema = values[0]
    ema_values = [ema]
    for value in values[1:]:
        ema = (value * smoothing) + (ema * (1 - smoothing))
        ema_values.append(ema)
    return ema_values


def _kd(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> dict[str, float]:
    length = min(len(highs), len(lows), len(closes))
    if length == 0:
        return {"k": 50.0, "d": 50.0}
    start = max(0, length - 9)
    lowest_low = min(lows[start:length])
    highest_high = max(highs[start:length])
    if highest_high == lowest_low:
        return {"k": 50.0, "d": 50.0}
    k = (closes[length - 1] - lowest_low) / (highest_high - lowest_low) * 100
    return {"k": k, "d": k}


def _atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int) -> float:
    length = min(len(highs), len(lows), len(closes))
    if length == 0:
        return 0.0
    true_ranges: list[float] = []
    for index in range(length):
        previous_close = closes[index - 1] if index > 0 else closes[index]
        true_ranges.append(max(highs[index] - lows[index], abs(highs[index] - previous_close), abs(lows[index] - previous_close)))
    return _mean(true_ranges[-period:])


def _obv(volumes: Sequence[float], closes: Sequence[float]) -> tuple[float, str]:
    length = min(len(volumes), len(closes))
    if length < 2:
        return 0.0, "flat"
    obv = 0.0
    recent_changes: list[float] = []
    for index in range(1, length):
        change = 0.0
        if closes[index] > closes[index - 1]:
            change = volumes[index]
        elif closes[index] < closes[index - 1]:
            change = -volumes[index]
        obv += change
        recent_changes.append(change)
    recent_sum = sum(recent_changes[-5:])
    if recent_sum > 0:
        return obv, "rising"
    if recent_sum < 0:
        return obv, "falling"
    return obv, "flat"


def _ordered_unique_symbols(symbols: Iterable[str]) -> list[str]:
    ordered_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = str(symbol).strip()
        if not normalized or normalized in seen:
            continue
        ordered_symbols.append(normalized)
        seen.add(normalized)
    return ordered_symbols


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


__all__ = [
    "BatchTechnicalFetcher",
    "YFinanceBatchTechnicalFetcher",
    "ensure_daily_radar_raw_rows",
]
