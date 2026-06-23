from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.repository import (
    BACKGROUND_CONTEXT_CONSUMER_DAILY_RADAR,
    BACKGROUND_CONTEXT_TYPES,
    get_shared_background_context_rows,
    get_latest_daily_radar_run,
    upsert_shared_background_context,
)
from ai_stock_sentinel.db.models import UserPortfolio, UserWatchlist


BACKGROUND_CONTEXT_LABELS: dict[str, str] = {
    "weekly_major_holders": "大戶持股集中背景",
    "lending": "借券空方壓力背景",
    "full_margin": "完整融資融券背景",
}

BACKGROUND_CONTEXT_MISSING_LABELS: dict[str, str] = {
    "weekly_major_holders": "大戶持股背景資料未更新",
    "lending": "借券背景資料未更新",
    "full_margin": "完整融資融券背景資料未更新",
}

BACKGROUND_CONTEXT_ALL_CONSUMERS = (
    "daily_radar",
    "analyze",
    "position_analysis",
    "portfolio_diagnosis",
    "lifecycle_review",
)


@dataclass(frozen=True)
class BackgroundContextPayload:
    symbol: str
    context_type: str
    applicable_consumers: tuple[str, ...]
    source: Mapping[str, Any]
    as_of_date: date | None
    freshness: str
    payload: Mapping[str, Any]
    missing_reason: str | None
    replay_key: str


class BackgroundChipContextProvider(Protocol):
    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        """Return consumer-neutral background context payloads for selected symbols."""


class StubBackgroundChipContextProvider:
    """Foundation provider that records explicit missing context without external calls."""

    provider_name = "stub_background_chip_context_provider"

    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        for symbol in symbols:
            for context_type in context_types:
                yield BackgroundContextPayload(
                    symbol=symbol,
                    context_type=context_type,
                    applicable_consumers=(BACKGROUND_CONTEXT_CONSUMER_DAILY_RADAR,),
                    source={
                        "domain": "background_context",
                        "provider": self.provider_name,
                        "market": market,
                    },
                    as_of_date=None,
                    freshness="missing",
                    payload={},
                    missing_reason="provider_not_configured",
                    replay_key=f"background_context:{symbol}:{context_type}:missing",
                )


def update_background_chip_context_cache(
    session: Session,
    *,
    run_date: date,
    market: str,
    provider: BackgroundChipContextProvider | None = None,
    symbols: Iterable[str] | None = None,
    context_types: Iterable[str] | None = None,
    reuse_same_day_fresh: bool = False,
) -> dict[str, Any]:
    active_provider = provider or StubBackgroundChipContextProvider()
    active_context_types = _ordered_unique(context_types or BACKGROUND_CONTEXT_TYPES)
    source_errors: list[dict[str, Any]] = []
    selected_symbols = _ordered_unique(
        symbols if symbols is not None else _default_refresh_symbols(
            session,
            market=market,
            context_types=active_context_types,
            errors=source_errors,
        )
    )
    if not selected_symbols:
        return {
            "status": "completed" if not source_errors else "failed",
            "market": market,
            "run_date": run_date.isoformat(),
            "symbol_count": 0,
            "context_types": active_context_types,
            "records_written": 0,
            "errors": [
                *source_errors,
                {"code": "no_selected_symbols", "message": "No selected symbols were available for context update."},
            ],
        }

    records_written = 0
    reused_pairs: set[tuple[str, str]] = set()
    errors: list[dict[str, Any]] = list(source_errors)
    fetch_symbols_by_context_type = {context_type: list(selected_symbols) for context_type in active_context_types}
    if reuse_same_day_fresh:
        fresh_rows = get_shared_background_context_rows(
            session,
            symbols=selected_symbols,
            context_types=active_context_types,
            reference_date=run_date,
            point_in_time=True,
        )
        reused_pairs = {
            (row.symbol, row.context_type)
            for row in fresh_rows
            if row.as_of_date == run_date and row.freshness == "fresh"
        }
        fetch_symbols_by_context_type = {
            context_type: [
                symbol
                for symbol in selected_symbols
                if (symbol, context_type) not in reused_pairs
            ]
            for context_type in active_context_types
        }
    try:
        if reuse_same_day_fresh:
            payloads_by_batch = (
                active_provider.fetch(
                    symbols=fetch_symbols,
                    context_types=[context_type],
                    run_date=run_date,
                    market=market,
                )
                for context_type in active_context_types
                if (fetch_symbols := fetch_symbols_by_context_type[context_type])
            )
        else:
            payloads_by_batch = (
                active_provider.fetch(
                    symbols=selected_symbols,
                    context_types=active_context_types,
                    run_date=run_date,
                    market=market,
                ),
            )
        for payloads in payloads_by_batch:
            for payload in payloads:
                upsert_shared_background_context(
                    session,
                    symbol=payload.symbol,
                    context_type=payload.context_type,
                    applicable_consumers=payload.applicable_consumers,
                    source=payload.source,
                    as_of_date=payload.as_of_date,
                    freshness=payload.freshness,
                    payload=payload.payload,
                    missing_reason=payload.missing_reason,
                    replay_key=payload.replay_key,
                )
                records_written += 1
    except Exception as exc:
        errors.append(
            {
                "code": "background_context_provider_failed",
                "message": str(exc),
                "error_type": exc.__class__.__name__,
            }
        )

    return {
        "status": "completed" if not errors else "failed",
        "market": market,
        "run_date": run_date.isoformat(),
        "symbol_count": len(selected_symbols),
        "context_types": active_context_types,
        "records_written": records_written,
        "reused_symbols": sorted({symbol for symbol, _context_type in reused_pairs}),
        "errors": errors,
    }


def build_background_context_labels(
    contexts: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for context in contexts:
        context_type = str(context.get("context_type") or "").strip()
        if not context_type:
            continue
        freshness = str(context.get("freshness") or "unknown")
        missing_reason = context.get("missing_reason")
        is_missing = freshness == "missing" or missing_reason is not None
        label = (
            BACKGROUND_CONTEXT_MISSING_LABELS.get(context_type)
            if is_missing
            else BACKGROUND_CONTEXT_LABELS.get(context_type)
        ) or f"背景脈絡：{context_type}"
        labels.append(
            {
                "context_type": context_type,
                "label": label,
                "source": dict(_mapping(context.get("source"))),
                "as_of_date": context.get("as_of_date"),
                "freshness": freshness,
                "missing_reason": str(missing_reason) if missing_reason is not None else None,
                "replay_key": str(context.get("replay_key") or ""),
                "applicable_consumers": _list_of_strings(context.get("applicable_consumers")),
            }
        )
    return labels


def _latest_daily_radar_symbols(session: Session, *, market: str) -> list[str]:
    latest_run = get_latest_daily_radar_run(session, market=market)
    if latest_run is None:
        return []
    return [candidate.symbol for candidate in latest_run.candidates]


def _default_refresh_symbols(
    session: Session,
    *,
    market: str,
    context_types: list[str],
    errors: list[dict[str, Any]],
) -> list[str]:
    if "weekly_major_holders" not in context_types:
        return _collect_symbols(
            errors,
            source="latest_daily_radar_candidates",
            getter=lambda: _latest_daily_radar_symbols(session, market=market),
        )

    symbols: list[str] = []
    for source, getter in (
        ("active_portfolio_holdings", lambda: _active_portfolio_symbols(session)),
        ("watchlist", lambda: _watchlist_symbols(session)),
        ("latest_daily_radar_candidates", lambda: _latest_daily_radar_symbols(session, market=market)),
    ):
        symbols.extend(_collect_symbols(errors, source=source, getter=getter))
    return symbols


def _collect_symbols(
    errors: list[dict[str, Any]],
    *,
    source: str,
    getter: Callable[[], Iterable[str]],
) -> list[str]:
    try:
        return list(getter())
    except Exception as exc:
        errors.append(
            {
                "code": "background_context_symbol_source_failed",
                "source": source,
                "message": str(exc),
                "error_type": exc.__class__.__name__,
            }
        )
        return []


def _active_portfolio_symbols(session: Session) -> list[str]:
    return list(
        session.scalars(
            sa.select(UserPortfolio.symbol)
            .where(UserPortfolio.is_active.is_(True))
            .order_by(UserPortfolio.symbol.asc())
        ).all()
    )


def _watchlist_symbols(session: Session) -> list[str]:
    return list(
        session.scalars(
            sa.select(UserWatchlist.symbol)
            .order_by(UserWatchlist.symbol.asc())
        ).all()
    )


def _ordered_unique(values: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return ordered


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


__all__ = [
    "BACKGROUND_CONTEXT_ALL_CONSUMERS",
    "BACKGROUND_CONTEXT_LABELS",
    "BACKGROUND_CONTEXT_MISSING_LABELS",
    "BackgroundChipContextProvider",
    "BackgroundContextPayload",
    "StubBackgroundChipContextProvider",
    "build_background_context_labels",
    "update_background_chip_context_cache",
]
