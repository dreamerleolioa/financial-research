from __future__ import annotations

from dataclasses import dataclass
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
    universe: list[ManagedUniverseSymbol]


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
    universe = [
        ManagedUniverseSymbol(symbol=symbol, sources=[source])
        for symbol in dict.fromkeys(str(symbol).strip().upper() for symbol in symbols)
        if symbol
    ]
    return _refresh_phase1_avwap_snapshots_for_universe(
        session,
        universe=universe,
        data_date=data_date,
        lookback_days=lookback_days,
        provider=provider,
        dataset=dataset,
        adjustment_mode=adjustment_mode,
    )


def _refresh_phase1_avwap_snapshots_for_universe(
    session: Session,
    *,
    universe: list[ManagedUniverseSymbol],
    data_date: date,
    lookback_days: int,
    provider: DailyPriceProvider | None,
    dataset: str,
    adjustment_mode: str,
) -> Phase1AvwapRefreshResult:
    symbols = [item.symbol for item in universe]
    existing = get_phase1_avwap_snapshots(
        session,
        symbols=symbols,
        data_date=data_date,
        dataset=dataset,
        adjustment_mode=adjustment_mode,
    )
    snapshots: list[Phase1AvwapSnapshot] = []
    reused_symbols: list[str] = []
    fetched_symbols: list[str] = []
    missing_symbols: list[str] = []
    active_provider = provider or TwseDailyPriceProvider()

    for item in universe:
        existing_snapshot = existing.get(item.symbol)
        if existing_snapshot is not None and existing_snapshot.freshness == "fresh":
            snapshots.append(existing_snapshot)
            reused_symbols.append(item.symbol)
            continue

        start_date = _history_start_date(item, data_date=data_date, lookback_days=lookback_days)
        source_provider = _provider_metadata(active_provider, "source_provider", item.symbol, default="twse")
        source_dataset = _provider_metadata(active_provider, "source_dataset", item.symbol, default=TWSE_STOCK_DAY_DATASET)
        try:
            bars = active_provider.fetch_history(item.symbol, start_date=start_date, end_date=data_date)
            payload = build_phase1_avwap_payload(
                symbol=item.symbol,
                bars=bars,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                source_provider=source_provider,
                source_dataset=source_dataset,
            )
        except (DailyPriceProviderError, FinMindClientError, KeyError, ValueError) as exc:
            reason = _missing_reason(exc)
            payload = build_missing_phase1_avwap_payload(
                symbol=item.symbol,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                source_provider=source_provider,
                source_dataset=source_dataset,
                missing_reason=reason,
            )
            snapshot = upsert_phase1_avwap_snapshot(
                session,
                symbol=item.symbol,
                data_date=data_date,
                dataset=dataset,
                adjustment_mode=adjustment_mode,
                source_provider=source_provider,
                payload=payload,
                freshness="missing",
                missing_reason=reason,
            )
            snapshots.append(snapshot)
            fetched_symbols.append(item.symbol)
            missing_symbols.append(item.symbol)
            continue

        snapshot = upsert_phase1_avwap_snapshot(
            session,
            symbol=item.symbol,
            data_date=data_date,
            dataset=dataset,
            adjustment_mode=adjustment_mode,
            source_provider=source_provider,
            payload=payload,
            freshness="fresh",
            missing_reason=None,
        )
        snapshots.append(snapshot)
        fetched_symbols.append(item.symbol)

    return Phase1AvwapRefreshResult(
        snapshots=snapshots,
        reused_symbols=reused_symbols,
        fetched_symbols=fetched_symbols,
        missing_symbols=missing_symbols,
        universe=universe,
    )


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
]
