from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.repository import (
    BACKGROUND_CONTEXT_CONSUMER_DAILY_RADAR,
    BACKGROUND_CONTEXT_TYPES,
    get_latest_daily_radar_run,
    upsert_shared_background_context,
)


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
) -> dict[str, Any]:
    active_provider = provider or StubBackgroundChipContextProvider()
    selected_symbols = _ordered_unique(symbols or _latest_daily_radar_symbols(session, market=market))
    active_context_types = _ordered_unique(context_types or BACKGROUND_CONTEXT_TYPES)
    if not selected_symbols:
        return {
            "status": "completed",
            "market": market,
            "run_date": run_date.isoformat(),
            "symbol_count": 0,
            "context_types": active_context_types,
            "records_written": 0,
            "errors": [{"code": "no_selected_symbols", "message": "No selected symbols were available for context update."}],
        }

    records_written = 0
    errors: list[dict[str, Any]] = []
    try:
        payloads = active_provider.fetch(
            symbols=selected_symbols,
            context_types=active_context_types,
            run_date=run_date,
            market=market,
        )
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
    "BACKGROUND_CONTEXT_LABELS",
    "BACKGROUND_CONTEXT_MISSING_LABELS",
    "BackgroundChipContextProvider",
    "BackgroundContextPayload",
    "StubBackgroundChipContextProvider",
    "build_background_context_labels",
    "update_background_chip_context_cache",
]
