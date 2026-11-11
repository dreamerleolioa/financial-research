from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.finmind_client import FinMindClientError
from ai_stock_sentinel.db.models import Phase1AvwapSnapshot
from ai_stock_sentinel.phase1_avwap.calculator import (
    DailyPriceBar,
    Phase1AvwapDataError,
    build_missing_phase1_avwap_payload,
    build_phase1_avwap_payload,
)
from ai_stock_sentinel.phase1_avwap.provider import (
    DailyPriceProviderError,
    InsufficientListedHistory,
    DEFAULT_ADJUSTMENT_MODE,
    DEFAULT_PHASE1_DATASET,
    TWSE_STOCK_DAY_DATASET,
    TwseDailyPriceProvider,
)
from ai_stock_sentinel.phase1_avwap.repository import (
    get_phase1_avwap_snapshots,
    upsert_phase1_avwap_snapshot,
)
from ai_stock_sentinel.phase1_avwap.universe import ManagedUniverseSymbol, resolve_phase1_managed_universe


class DailyPriceProvider(Protocol):
    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        ...


@dataclass(frozen=True)
class Phase1AvwapRefreshResult:
    snapshots: list[Phase1AvwapSnapshot]
    reused_symbols: list[str]
    fetched_symbols: list[str]
    missing_symbols: list[str]
    missing_symbol_reasons: dict[str, str]
    universe: list[ManagedUniverseSymbol]
    skipped_symbol_reasons: dict[str, str] = field(default_factory=dict)


def refresh_phase1_avwap_snapshots(
    session: Session,
    *,
    user_id: int,
    data_date: date,
    market: str = "TW",
    lookback_days: int = 120,
    provider: DailyPriceProvider | None = None,
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
) -> Phase1AvwapRefreshResult:
    universe = resolve_phase1_managed_universe(session, user_id=user_id, market=market)
    return _refresh_phase1_avwap_snapshots_for_universe(
        session,
        universe=universe,
        data_date=data_date,
        lookback_days=lookback_days,
        provider=provider,
        dataset=dataset,
        adjustment_mode=adjustment_mode,
    )


def refresh_phase1_avwap_snapshots_for_symbols(
    session: Session,
    *,
    symbols: list[str],
    data_date: date,
    source: str = "daily_radar_candidate",
    lookback_days: int = 120,
    provider: DailyPriceProvider | None = None,
    dataset: str = DEFAULT_PHASE1_DATASET,
    adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
) -> Phase1AvwapRefreshResult:
    universe = _symbol_universe(symbols, source=source)
    return _refresh_phase1_avwap_snapshots_for_universe(
        session,
        universe=universe,
        data_date=data_date,
        lookback_days=lookback_days,
        provider=provider,
        dataset=dataset,
        adjustment_mode=adjustment_mode,
    )


@dataclass(frozen=True)
class Phase1AvwapRefreshPreparation:
    universe: list[ManagedUniverseSymbol]
    data_date: date
    lookback_days: int
    dataset: str
    adjustment_mode: str
    provider: DailyPriceProvider
    reused_symbols: tuple[str, ...]


@dataclass(frozen=True)
class _CollectedSnapshot:
    symbol: str
    source_provider: str
    payload: dict
    freshness: str
    missing_reason: str | None
    skipped: bool = False


@dataclass(frozen=True)
class Phase1AvwapRefreshCollection:
    universe: list[ManagedUniverseSymbol]
    data_date: date
    dataset: str
    adjustment_mode: str
    reused_symbols: tuple[str, ...]
    records: dict[str, _CollectedSnapshot]


def prepare_phase1_avwap_refresh_for_symbols(
    session: Session, *, symbols: list[str], data_date: date,
    source: str = "daily_radar_candidate", lookback_days: int = 120,
    provider: DailyPriceProvider | None = None,
    dataset: str = DEFAULT_PHASE1_DATASET, adjustment_mode: str = DEFAULT_ADJUSTMENT_MODE,
) -> Phase1AvwapRefreshPreparation:
    """DB-only phase. Its owner may end its read transaction after this returns."""
    universe = _symbol_universe(symbols, source=source)
    return _prepare_refresh(session, universe=universe, data_date=data_date,
        lookback_days=lookback_days, provider=provider, dataset=dataset, adjustment_mode=adjustment_mode)


def _symbol_universe(symbols, *, source):
    return [ManagedUniverseSymbol(symbol=symbol, sources=[source])
        for symbol in dict.fromkeys(str(symbol).strip().upper() for symbol in symbols) if symbol]


def _prepare_refresh(session, *, universe, data_date, lookback_days, provider, dataset, adjustment_mode):
    existing = get_phase1_avwap_snapshots(session, symbols=[item.symbol for item in universe],
        data_date=data_date, dataset=dataset, adjustment_mode=adjustment_mode)
    # Only plain identities cross the read/network boundary; expired ORM rows
    # must not reopen a transaction in the middle of remote collection.
    reused = tuple(item.symbol for item in universe
        if item.symbol in existing and existing[item.symbol].freshness == "fresh")
    active_provider = provider or TwseDailyPriceProvider()
    reset = getattr(active_provider, "begin_refresh", None)
    if callable(reset):
        reset()
    prepare = getattr(active_provider, "prepare_fetch", None)
    if callable(prepare):
        prepare([item.symbol for item in universe if item.symbol not in reused],
            start_date=data_date - timedelta(days=lookback_days), end_date=data_date)
    return Phase1AvwapRefreshPreparation(universe, data_date, lookback_days,
        dataset, adjustment_mode, active_provider, reused)


def collect_phase1_avwap_refresh(preparation: Phase1AvwapRefreshPreparation) -> Phase1AvwapRefreshCollection:
    """Collect the entire batch without writing a snapshot or controlling a session."""
    records = {item.symbol: _collect_snapshot(item, preparation) for item in preparation.universe
               if item.symbol not in preparation.reused_symbols}
    return Phase1AvwapRefreshCollection(preparation.universe, preparation.data_date,
        preparation.dataset, preparation.adjustment_mode, preparation.reused_symbols, records)


def _collect_snapshot(item, preparation):
    provider, data_date = preparation.provider, preparation.data_date
    start_date = _history_start_date(item, data_date=data_date, lookback_days=preparation.lookback_days)
    source_provider = _provider_metadata(provider, "source_provider", item.symbol, default="twse")
    source_dataset = _provider_metadata(provider, "source_dataset", item.symbol, default=TWSE_STOCK_DAY_DATASET)
    source_trace = {}
    try:
        fetch_result = getattr(provider, "fetch_history_result", None)
        if callable(fetch_result):
            history = fetch_result(item.symbol, start_date=start_date, end_date=data_date)
            bars, source_provider, source_dataset = history.bars, history.source_provider, history.source_dataset
            source_trace = getattr(history, "source_trace", {})
        else:
            bars = provider.fetch_history(item.symbol, start_date=start_date, end_date=data_date)
        payload = build_phase1_avwap_payload(symbol=item.symbol, bars=bars, data_date=data_date,
            dataset=preparation.dataset, adjustment_mode=preparation.adjustment_mode,
            source_provider=source_provider, source_dataset=source_dataset)
        if source_trace:
            payload["source"]["trace"] = source_trace
        return _CollectedSnapshot(item.symbol, source_provider, payload, "fresh", None)
    except (DailyPriceProviderError, FinMindClientError, KeyError, ValueError) as exc:
        reason = _missing_reason(exc)
        source_provider = getattr(exc, "source_provider", None) or source_provider
        source_dataset = getattr(exc, "source_dataset", None) or source_dataset
        source_trace = getattr(exc, "source_trace", None) or source_trace
        payload = build_missing_phase1_avwap_payload(symbol=item.symbol, data_date=data_date,
            dataset=preparation.dataset, adjustment_mode=preparation.adjustment_mode,
            source_provider=source_provider, source_dataset=source_dataset, missing_reason=reason)
        if source_trace:
            payload["source"]["trace"] = source_trace
        return _CollectedSnapshot(item.symbol, source_provider, payload, "missing", reason,
            skipped=isinstance(exc, InsufficientListedHistory))


def persist_phase1_avwap_refresh(session: Session, collection: Phase1AvwapRefreshCollection) -> Phase1AvwapRefreshResult:
    """Write only; committing or rolling back remains the caller's responsibility."""
    existing = get_phase1_avwap_snapshots(session, symbols=[item.symbol for item in collection.universe],
        data_date=collection.data_date, dataset=collection.dataset, adjustment_mode=collection.adjustment_mode)
    snapshots, reused, fetched, missing = [], [], [], []
    reasons, skipped = {}, {}
    for item in collection.universe:
        current = existing.get(item.symbol)
        if current is not None and current.freshness == "fresh":
            snapshots.append(current)
            reused.append(item.symbol)
            continue
        if item.symbol in collection.reused_symbols:
            raise DailyPriceProviderError("avwap_reused_snapshot_changed")
        record = collection.records[item.symbol]
        snapshots.append(upsert_phase1_avwap_snapshot(session, symbol=record.symbol, data_date=collection.data_date,
            dataset=collection.dataset, adjustment_mode=collection.adjustment_mode,
            source_provider=record.source_provider, payload=record.payload,
            freshness=record.freshness, missing_reason=record.missing_reason))
        fetched.append(item.symbol)
        if record.skipped:
            skipped[item.symbol] = record.missing_reason
        elif record.missing_reason:
            missing.append(item.symbol)
            reasons[item.symbol] = record.missing_reason
    return Phase1AvwapRefreshResult(snapshots, reused, fetched, missing, reasons, collection.universe, skipped)


def _refresh_phase1_avwap_snapshots_for_universe(
    session: Session, *, universe: list[ManagedUniverseSymbol], data_date: date, lookback_days: int,
    provider: DailyPriceProvider | None, dataset: str, adjustment_mode: str,
) -> Phase1AvwapRefreshResult:
    preparation = _prepare_refresh(session, universe=universe, data_date=data_date,
        lookback_days=lookback_days, provider=provider, dataset=dataset, adjustment_mode=adjustment_mode)
    return persist_phase1_avwap_refresh(session, collect_phase1_avwap_refresh(preparation))


def _history_start_date(item: ManagedUniverseSymbol, *, data_date: date, lookback_days: int) -> date:
    return data_date - timedelta(days=lookback_days)


def _missing_reason(exc: Exception) -> str:
    if isinstance(exc, Phase1AvwapDataError):
        return exc.reason
    if isinstance(exc, DailyPriceProviderError):
        return exc.code
    if isinstance(exc, FinMindClientError):
        return exc.code
    return "daily_price_history_unavailable"


def _provider_metadata(provider: object, name: str, symbol: str, *, default: str) -> str:
    value = getattr(provider, name, None)
    if callable(value):
        return str(value(symbol))
    if value:
        return str(value)
    return default


__all__ = [
    "DailyPriceProvider",
    "Phase1AvwapRefreshResult",
    "refresh_phase1_avwap_snapshots",
    "refresh_phase1_avwap_snapshots_for_symbols",
    "prepare_phase1_avwap_refresh_for_symbols",
    "collect_phase1_avwap_refresh",
    "persist_phase1_avwap_refresh",
]
