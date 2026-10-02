from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.repository import get_latest_daily_radar_run
from ai_stock_sentinel.db.models import DailyRadarCandidate


@dataclass
class ManagedUniverseSymbol:
    symbol: str
    sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Phase1RefreshSymbolSet:
    symbols: list[str]
    skipped_symbol_reasons: dict[str, str] = field(default_factory=dict)


def resolve_phase1_managed_universe(
    session: Session,
    *,
    user_id: int,
    market: str = "TW",
) -> list[ManagedUniverseSymbol]:
    by_symbol: dict[str, ManagedUniverseSymbol] = {}
    latest_run = get_latest_daily_radar_run(session, market=market)
    if latest_run is not None:
        for candidate in sorted(
            (candidate for candidate in latest_run.candidates if candidate.selection_status == "selected"),
            key=_candidate_sort_key,
        ):
            item = _ensure_item(by_symbol, candidate.symbol)
            _append_source(item, "daily_radar_candidate")

    return list(by_symbol.values())


def resolve_phase1_refresh_symbols(
    session: Session,
    *,
    seed_symbols: list[str] | None = None,
) -> list[str]:
    return resolve_phase1_refresh_symbol_set(session, seed_symbols=seed_symbols).symbols


def resolve_phase1_refresh_symbol_set(
    session: Session,
    *,
    seed_symbols: list[str] | None = None,
) -> Phase1RefreshSymbolSet:
    symbols: dict[str, None] = {}
    skipped_symbol_reasons: dict[str, str] = {}
    for symbol in seed_symbols or []:
        _add_refresh_symbol(symbols, skipped_symbol_reasons, symbol)

    return Phase1RefreshSymbolSet(symbols=list(symbols), skipped_symbol_reasons=skipped_symbol_reasons)


def is_phase1_refresh_supported_symbol(symbol: str) -> bool:
    normalized = str(symbol).strip().upper()
    return normalized.endswith(".TW") or normalized.endswith(".TWO")


def _add_refresh_symbol(
    symbols: dict[str, None],
    skipped_symbol_reasons: dict[str, str],
    symbol: str,
) -> None:
    normalized = str(symbol).strip().upper()
    if not normalized:
        return
    if is_phase1_refresh_supported_symbol(normalized):
        symbols[normalized] = None
        skipped_symbol_reasons.pop(normalized, None)
        return
    if normalized not in symbols:
        skipped_symbol_reasons[normalized] = "unsupported_phase1_avwap_market"


def _ensure_item(items: dict[str, ManagedUniverseSymbol], symbol: str) -> ManagedUniverseSymbol:
    normalized = str(symbol).strip().upper()
    if normalized not in items:
        items[normalized] = ManagedUniverseSymbol(symbol=normalized)
    return items[normalized]


def _append_source(item: ManagedUniverseSymbol, source: str) -> None:
    if source not in item.sources:
        item.sources.append(source)


def _candidate_sort_key(candidate: DailyRadarCandidate) -> tuple[int, str]:
    return (-candidate.observation_score, candidate.symbol)


__all__ = [
    "ManagedUniverseSymbol",
    "Phase1RefreshSymbolSet",
    "resolve_phase1_refresh_symbols",
    "resolve_phase1_refresh_symbol_set",
    "is_phase1_refresh_supported_symbol",
    "resolve_phase1_managed_universe",
]
