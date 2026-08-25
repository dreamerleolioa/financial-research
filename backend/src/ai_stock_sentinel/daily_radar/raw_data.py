from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
import math
import os
from typing import Any, Protocol

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.daily_radar.data_quality import missing_daily_radar_candidate_technical_fields
from ai_stock_sentinel.daily_radar.market_bar_repository import get_taiwan_daily_bars
from ai_stock_sentinel.daily_radar.repository import get_final_raw_data_rows_for_symbols
from ai_stock_sentinel.db.models import StockRawData, TaiwanDailyBar
from ai_stock_sentinel.technical.profile import build_technical_profile_payload


REQUIRED_TECHNICAL_ADJUSTMENT_MODE = "adjusted"


class BatchTechnicalFetcher(Protocol):
    def fetch(self, symbols: Sequence[str], *, run_date: date) -> Mapping[str, Mapping[str, Any]]:
        ...


class YFinanceBatchTechnicalFetcher:
    def __init__(self, *, name_resolver: Callable[[str], str | None] = resolve_symbol_name) -> None:
        self._name_resolver = name_resolver

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
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        payloads: dict[str, Mapping[str, Any]] = {}
        for symbol in ordered_symbols:
            frame = _frame_on_or_before_run_date(_symbol_frame(history, symbol), run_date=run_date)
            frame = _trim_trailing_incomplete_ohlcv_rows(frame)
            payload = _build_technical_payload(
                symbol,
                frame,
                run_date=run_date,
                name=self._safe_resolve_name(symbol),
            )
            if payload is not None:
                payloads[symbol] = payload
        return payloads

    def _safe_resolve_name(self, symbol: str) -> str | None:
        try:
            return self._name_resolver(symbol)
        except Exception:
            return None


class LocalFirstBatchTechnicalFetcher:
    """Use an adjusted TW/TWO archive when available; never mix in unadjusted bars."""

    _MODES = {"yfinance_only", "official_first", "official_only"}

    def __init__(
        self,
        session: Session,
        *,
        fallback_fetcher: BatchTechnicalFetcher | None = None,
        provider_mode: str | None = None,
        min_trading_bars: int = 60,
        name_resolver: Callable[[str], str | None] = resolve_symbol_name,
    ) -> None:
        self._session = session
        self._fallback_fetcher = fallback_fetcher or YFinanceBatchTechnicalFetcher(
            name_resolver=name_resolver
        )
        self._provider_mode = provider_mode or os.getenv(
            "DAILY_RADAR_TW_OHLCV_PROVIDER_MODE",
            "yfinance_only",
        )
        if self._provider_mode not in self._MODES:
            raise ValueError("invalid DAILY_RADAR_TW_OHLCV_PROVIDER_MODE")
        self._min_trading_bars = max(1, min_trading_bars)
        self._name_resolver = name_resolver

    def fetch(self, symbols: Sequence[str], *, run_date: date) -> Mapping[str, Mapping[str, Any]]:
        ordered_symbols = _ordered_unique_symbols(symbols)
        if not ordered_symbols:
            return {}
        if self._provider_mode == "yfinance_only":
            return self._fallback_fetcher.fetch(ordered_symbols, run_date=run_date)

        supported_symbols = [symbol for symbol in ordered_symbols if _is_supported_local_bar_symbol(symbol)]
        rows = get_taiwan_daily_bars(
            self._session,
            symbols=supported_symbols,
            start_date=run_date - timedelta(days=120),
            end_date=run_date,
            adjustment_mode=REQUIRED_TECHNICAL_ADJUSTMENT_MODE,
        )
        rows_by_symbol: dict[str, list[TaiwanDailyBar]] = {}
        for row in rows:
            rows_by_symbol.setdefault(row.symbol, []).append(row)

        payloads: dict[str, Mapping[str, Any]] = {}
        for symbol in supported_symbols:
            symbol_rows = rows_by_symbol.get(symbol, [])
            if (
                len(symbol_rows) < self._min_trading_bars
                or not symbol_rows
                or symbol_rows[-1].trade_date != run_date
            ):
                continue
            payload = _build_local_technical_payload(
                symbol,
                symbol_rows,
                run_date=run_date,
                name=symbol_rows[-1].name or self._safe_resolve_name(symbol),
            )
            if payload is not None:
                payloads[symbol] = payload

        if self._provider_mode == "official_first":
            fallback_symbols = [symbol for symbol in ordered_symbols if symbol not in payloads]
            if fallback_symbols:
                payloads.update(self._fallback_fetcher.fetch(fallback_symbols, run_date=run_date))
        return payloads

    def _safe_resolve_name(self, symbol: str) -> str | None:
        try:
            return self._name_resolver(symbol)
        except Exception:
            return None


def ensure_daily_radar_raw_rows(
    session: Session,
    run_date: date,
    symbols: Iterable[str],
    *,
    technical_fetcher: BatchTechnicalFetcher | None = None,
    institutional_payloads_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
    margin_contexts_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[StockRawData]:
    ordered_symbols = _ordered_unique_symbols(symbols)
    if not ordered_symbols:
        return []

    institutional_payloads = institutional_payloads_by_symbol or {}
    existing_final = get_final_raw_data_rows_for_symbols(session, run_date=run_date, symbols=ordered_symbols)
    reusable_final_symbols = {row.symbol for row in reusable_daily_radar_raw_rows(existing_final)}
    missing_symbols = [symbol for symbol in ordered_symbols if symbol not in reusable_final_symbols]
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
        # Production sessions disable autoflush, so projection queries below
        # cannot see newly added rows until they are explicitly flushed.
        session.flush()
    _apply_institutional_payloads(
        session,
        run_date=run_date,
        symbols=ordered_symbols,
        institutional_payloads_by_symbol=institutional_payloads,
    )
    if margin_contexts_by_symbol is not None:
        _apply_margin_contexts(
            session,
            run_date=run_date,
            symbols=ordered_symbols,
            margin_contexts_by_symbol=margin_contexts_by_symbol,
        )
    if missing_symbols or institutional_payloads or margin_contexts_by_symbol is not None:
        session.flush()

    final_rows = get_final_raw_data_rows_for_symbols(session, run_date=run_date, symbols=ordered_symbols)
    return reusable_daily_radar_raw_rows(final_rows)


def reusable_daily_radar_raw_rows(rows: Iterable[StockRawData]) -> list[StockRawData]:
    return [
        row
        for row in rows
        if not missing_daily_radar_candidate_technical_fields(
            _mapping(row.technical),
            record_date=row.record_date,
        )
    ]


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


def _apply_margin_contexts(
    session: Session,
    *,
    run_date: date,
    symbols: Sequence[str],
    margin_contexts_by_symbol: Mapping[str, Mapping[str, Any]],
) -> None:
    context_symbols = [
        symbol
        for symbol in symbols
        if _is_fresh_full_margin_context(margin_contexts_by_symbol.get(symbol))
    ]
    if not context_symbols:
        return

    rows = session.scalars(
        select(StockRawData).where(
            StockRawData.record_date == run_date,
            StockRawData.symbol.in_(context_symbols),
        )
    ).all()
    for row in rows:
        context = _mapping(margin_contexts_by_symbol.get(row.symbol))
        fundamental = dict(_mapping(row.fundamental))
        data_dates = dict(_mapping(fundamental.get("data_dates")))
        fundamental["margin"] = _project_margin_context(context, technical=_mapping(row.technical))
        as_of_date = context.get("as_of_date")
        if as_of_date is None:
            data_dates.pop("margin", None)
        else:
            data_dates["margin"] = str(as_of_date)
        fundamental["data_dates"] = data_dates
        row.fundamental = fundamental


def _is_fresh_full_margin_context(value: Any) -> bool:
    context = _mapping(value)
    return context.get("context_type") == "full_margin" and context.get("freshness") == "fresh"


def _project_margin_context(
    context: Mapping[str, Any],
    *,
    technical: Mapping[str, Any],
) -> dict[str, Any]:
    if context.get("context_type") != "full_margin" or context.get("freshness") != "fresh":
        return {}

    payload = _mapping(context.get("payload"))
    margin_balance = _to_float(payload.get("latest_margin_balance"))
    volume = _to_float(_mapping(technical.get("ohlcv")).get("volume"))
    margin_to_volume = (
        margin_balance * 1_000 / volume
        if margin_balance is not None and volume is not None and volume > 0
        else None
    )
    projected = {
        "margin_balance": margin_balance,
        "short_balance": _to_float(payload.get("latest_short_balance")),
        "margin_delta": _to_float(payload.get("margin_balance_delta")),
        "margin_delta_pct": _to_float(payload.get("margin_balance_delta_pct")),
        "margin_delta_pct_unavailable_reason": payload.get(
            "margin_balance_delta_pct_unavailable_reason"
        ),
        "short_delta": _to_float(payload.get("short_balance_delta")),
        "short_delta_pct": _to_float(payload.get("short_balance_delta_pct")),
        "margin_to_volume": margin_to_volume,
        "risk_flags": [],
    }
    return {key: value for key, value in projected.items() if value is not None}


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
        row = stored_by_symbol.get(symbol)
        if row is None:
            row = StockRawData(symbol=symbol, record_date=run_date)
            row.institutional = dict(institutional_payloads_by_symbol.get(symbol) or {})
            row.fundamental = {"margin": {}, "data_dates": {"margin": run_date.isoformat()}}
            session.add(row)
        row.technical = technical
        row.raw_data_is_final = True


def _normalize_technical_payload(symbol: str, payload: Mapping[str, Any], *, run_date: date) -> dict[str, Any]:
    technical = dict(payload)
    if "ohlcv" not in technical:
        technical["ohlcv"] = {}
    if "indicators" not in technical:
        technical["indicators"] = {}
    if "technical_profile" not in technical:
        technical["technical_profile"] = {}
    technical["name"] = str(technical.get("name") or symbol)
    technical["ohlcv"] = dict(_mapping(technical.get("ohlcv")))
    technical["indicators"] = dict(_mapping(technical.get("indicators")))
    technical["technical_profile"] = dict(_mapping(technical.get("technical_profile")))
    technical["data_dates"] = {
        "ohlcv": run_date.isoformat(),
        "technical_indicators": run_date.isoformat(),
        **{key: str(value) for key, value in _mapping(technical.get("data_dates")).items()},
    }
    if technical["technical_profile"] and "technical_profile" not in technical["data_dates"]:
        technical["data_dates"]["technical_profile"] = run_date.isoformat()
    return technical


def _build_technical_payload(symbol: str, frame: Any, *, run_date: date, name: str | None = None) -> dict[str, Any] | None:
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
    data_date = _last_index_date(frame) or run_date.isoformat()
    profile_payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        current_price=close,
        data_date=data_date,
        is_final=True,
    )
    if profile_payload is None:
        return None
    technical_indicators = dict(_mapping(profile_payload.get("technical_indicators")))
    technical_profile = dict(_mapping(profile_payload.get("technical_profile")))

    return {
        "name": name or symbol,
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
        "indicators": _daily_radar_indicators_from_profile(
            technical_indicators,
            technical_profile=technical_profile,
            lookback_days=len(closes),
        ),
        "technical_profile": technical_profile,
        "data_dates": {
            "ohlcv": data_date,
            "technical_indicators": data_date,
            "technical_profile": data_date,
        },
    }


def _build_local_technical_payload(
    symbol: str,
    rows: Sequence[TaiwanDailyBar],
    *,
    run_date: date,
    name: str | None,
) -> dict[str, Any] | None:
    if not rows:
        return None
    closes = [float(row.close) for row in rows]
    opens = [float(row.open) for row in rows]
    highs = [float(row.high) for row in rows]
    lows = [float(row.low) for row in rows]
    volumes = [float(row.volume) for row in rows]
    data_date = rows[-1].trade_date.isoformat()
    close = closes[-1]
    profile_payload = build_technical_profile_payload(
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        current_price=close,
        data_date=data_date,
        is_final=True,
    )
    if profile_payload is None:
        return None
    technical_indicators = dict(_mapping(profile_payload.get("technical_indicators")))
    technical_profile = dict(_mapping(profile_payload.get("technical_profile")))
    return {
        "name": name or symbol,
        "price_history": [
            {"date": row.trade_date.isoformat(), "close": float(row.close)}
            for row in rows[-80:]
        ],
        "ohlcv": {
            "open": opens[-1],
            "high": highs[-1],
            "low": lows[-1],
            "close": close,
            "previous_close": closes[-2] if len(closes) >= 2 else close,
            "volume": int(volumes[-1]),
            "avg_volume_20": _mean(volumes[-20:]),
        },
        "indicators": _daily_radar_indicators_from_profile(
            technical_indicators,
            technical_profile=technical_profile,
            lookback_days=len(closes),
        ),
        "technical_profile": technical_profile,
        "data_dates": {
            "ohlcv": data_date,
            "technical_indicators": data_date,
            "technical_profile": data_date,
        },
        "source_provider": "taiwan_daily_bars",
    }


def _daily_radar_indicators_from_profile(
    technical_indicators: Mapping[str, Any],
    *,
    technical_profile: Mapping[str, Any],
    lookback_days: int,
) -> dict[str, Any]:
    has_ohlc_price_levels = _has_ohlc_price_levels(technical_profile)
    support = technical_indicators.get("low_20d") if has_ohlc_price_levels else None
    resistance = technical_indicators.get("high_20d") if has_ohlc_price_levels else None
    return {
        "ma5": technical_indicators.get("ma5"),
        "ma20": technical_indicators.get("ma20"),
        "ma60": technical_indicators.get("ma60"),
        "rsi14": technical_indicators.get("rsi14"),
        "bias20": technical_indicators.get("bias20"),
        "volume_ratio": technical_indicators.get("volume_ratio"),
        "missing_trading_days_60": max(0, 60 - lookback_days),
        "mfi14": technical_indicators.get("mfi"),
        "macd": technical_indicators.get("macd_line"),
        "macd_signal": technical_indicators.get("macd_signal"),
        "macd_histogram": technical_indicators.get("macd_hist"),
        "kd_k": technical_indicators.get("kd_k"),
        "kd_d": technical_indicators.get("kd_d"),
        "atr14": technical_indicators.get("atr"),
        "support": support,
        "resistance": resistance,
        "support_level": support,
        "resistance_level": resistance,
        "obv": technical_indicators.get("obv"),
        "obv_trend": technical_indicators.get("obv_trend_20d"),
    }


def _has_ohlc_price_levels(technical_profile: Mapping[str, Any]) -> bool:
    data_quality = _mapping(technical_profile.get("data_quality"))
    return (
        data_quality.get("ohlcv_aligned") is True
        and data_quality.get("price_level_basis") == "ohlc_high_low"
    )


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


def _trim_trailing_incomplete_ohlcv_rows(frame: Any) -> Any:
    if getattr(frame, "empty", False) or not hasattr(frame, "iloc"):
        return frame
    columns = {
        field_name: _matching_column_name(frame, field_name)
        for field_name in ("Open", "High", "Low", "Close", "Volume")
    }
    if any(column_name is None for column_name in columns.values()):
        return frame.iloc[0:0]
    for position in range(len(frame) - 1, -1, -1):
        row = frame.iloc[position]
        values = {
            field_name: _to_float(row[column_name])
            for field_name, column_name in columns.items()
        }
        if all(
            value is not None
            and (value >= 0 if field_name == "Volume" else value > 0)
            for field_name, value in values.items()
        ):
            return frame.iloc[: position + 1]
    return frame.iloc[0:0]


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


def _is_supported_local_bar_symbol(symbol: str) -> bool:
    normalized = symbol.upper()
    if normalized.endswith(".TWO"):
        stock_id = normalized.removesuffix(".TWO")
    elif normalized.endswith(".TW"):
        stock_id = normalized.removesuffix(".TW")
    else:
        return False
    return len(stock_id) == 4 and stock_id.isdigit() and not stock_id.startswith("0")


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
    "LocalFirstBatchTechnicalFetcher",
    "YFinanceBatchTechnicalFetcher",
    "ensure_daily_radar_raw_rows",
    "reusable_daily_radar_raw_rows",
]
