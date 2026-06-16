from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.db.models import DailyRadarCandidate, StockRawData

SymbolNameResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class DailyRadarNameBackfillResult:
    scanned: int
    updated_candidates: int
    updated_raw_rows: int
    unresolved_symbols: list[str]


def backfill_daily_radar_symbol_names(
    session: Session,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    name_resolver: SymbolNameResolver = resolve_symbol_name,
) -> DailyRadarNameBackfillResult:
    candidates = _candidate_rows_needing_name(session, limit=limit)
    symbols = _ordered_unique(candidate.symbol for candidate in candidates)
    resolved_names: dict[str, str] = {}
    unresolved_symbols: list[str] = []

    for symbol in symbols:
        resolved = _resolved_display_name(symbol, name_resolver)
        if resolved is None:
            unresolved_symbols.append(symbol)
            continue
        resolved_names[symbol] = resolved

    updated_candidates = 0
    for candidate in candidates:
        resolved = resolved_names.get(_normalize_symbol(candidate.symbol))
        if resolved is None:
            continue
        updated_candidates += 1
        if not dry_run:
            candidate.name = resolved

    updated_raw_rows = _backfill_raw_data_names(
        session,
        resolved_names=resolved_names,
        dry_run=dry_run,
    )

    if not dry_run and (updated_candidates or updated_raw_rows):
        session.flush()

    return DailyRadarNameBackfillResult(
        scanned=len(candidates),
        updated_candidates=updated_candidates,
        updated_raw_rows=updated_raw_rows,
        unresolved_symbols=unresolved_symbols,
    )


def _candidate_rows_needing_name(session: Session, *, limit: int | None) -> list[DailyRadarCandidate]:
    statement = (
        select(DailyRadarCandidate)
        .where(
            or_(
                DailyRadarCandidate.name.is_(None),
                DailyRadarCandidate.name == "",
                DailyRadarCandidate.name == DailyRadarCandidate.symbol,
            )
        )
        .order_by(DailyRadarCandidate.id.asc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all())


def _backfill_raw_data_names(
    session: Session,
    *,
    resolved_names: Mapping[str, str],
    dry_run: bool,
) -> int:
    if not resolved_names:
        return 0

    rows = session.scalars(
        select(StockRawData)
        .where(StockRawData.symbol.in_(resolved_names.keys()))
        .order_by(StockRawData.id.asc())
    ).all()
    updated_rows = 0

    for row in rows:
        technical = dict(row.technical or {})
        if not _needs_name_repair(row.symbol, technical.get("name")):
            continue
        resolved_name = resolved_names.get(_normalize_symbol(row.symbol))
        if resolved_name is None:
            continue
        updated_rows += 1
        if not dry_run:
            technical["name"] = resolved_name
            row.technical = technical

    return updated_rows


def get_daily_radar_symbol_name_resolver() -> SymbolNameResolver:
    return resolve_symbol_name


def _resolved_display_name(symbol: str, name_resolver: SymbolNameResolver) -> str | None:
    resolved = name_resolver(symbol)
    if not resolved:
        return None
    resolved = str(resolved).strip()
    if not resolved or resolved.upper() == symbol.upper():
        return None
    return resolved


def _needs_name_repair(symbol: str, name: object) -> bool:
    normalized_name = str(name or "").strip()
    return not normalized_name or normalized_name.upper() == _normalize_symbol(symbol)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def _ordered_unique(symbols: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return ordered


__all__ = [
    "DailyRadarNameBackfillResult",
    "SymbolNameResolver",
    "backfill_daily_radar_symbol_names",
    "get_daily_radar_symbol_name_resolver",
]
