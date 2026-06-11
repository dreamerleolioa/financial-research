from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.background_context import build_background_context_labels
from ai_stock_sentinel.daily_radar.repository import get_shared_background_context_trace_by_symbol


logger = logging.getLogger(__name__)

SHARED_CONTEXT_READ_VERSION = "shared-context-read-v1"
SHARED_CONTEXT_CONSUMER_ANALYZE = "analyze"
SHARED_CONTEXT_CONSUMER_POSITION = "position_analysis"
SHARED_CONTEXT_CONSUMER_PORTFOLIO = "portfolio_diagnosis"
SHARED_CONTEXT_CONSUMER_LIFECYCLE = "lifecycle_review"


def read_shared_context_for_symbol(
    db: Session,
    *,
    symbol: str,
    consumer: str,
    reference_date: date | None = None,
    point_in_time: bool = False,
) -> dict[str, Any]:
    """Read shared background context as response/evidence-only caveats."""
    try:
        traces_by_symbol = get_shared_background_context_trace_by_symbol(
            db,
            symbols=[symbol],
            consumer=consumer,
        )
        contexts = traces_by_symbol.get(symbol) or []
    except Exception as exc:
        logger.warning(
            "shared_context_read_failed",
            extra={
                "symbol": symbol,
                "consumer": consumer,
                "error_type": exc.__class__.__name__,
            },
        )
        contexts = [
            _missing_context(
                symbol=symbol,
                context_type="shared_background_context",
                consumer=consumer,
                missing_reason="context_cache_read_failed",
                source_status="read_failed",
                reference_date=reference_date,
            )
        ]

    if not isinstance(contexts, list):
        contexts = []
    if point_in_time and reference_date is not None:
        contexts = [
            _point_in_time_context(
                context,
                symbol=symbol,
                consumer=consumer,
                reference_date=reference_date,
            )
            for context in contexts
        ]

    caveats = build_background_context_labels(contexts)
    return {
        "version": SHARED_CONTEXT_READ_VERSION,
        "symbol": symbol,
        "consumer": consumer,
        "reference_date": reference_date.isoformat() if reference_date is not None else None,
        "point_in_time": point_in_time,
        "contexts": contexts,
        "caveats": caveats,
        "data_quality": shared_context_data_quality(caveats, point_in_time=point_in_time),
    }


def shared_context_data_quality(
    caveats: list[dict[str, Any]],
    *,
    point_in_time: bool = False,
) -> dict[str, Any]:
    counts = {"fresh": 0, "stale": 0, "missing": 0, "unknown": 0}
    missing_reasons: list[str] = []
    for caveat in caveats:
        freshness = str(caveat.get("freshness") or "unknown")
        if freshness not in counts:
            freshness = "unknown"
        counts[freshness] += 1
        missing_reason = caveat.get("missing_reason")
        if missing_reason is not None:
            reason = str(missing_reason)
            if reason not in missing_reasons:
                missing_reasons.append(reason)

    if counts["missing"] == len(caveats) and caveats:
        status = "missing"
    elif counts["stale"] > 0 or counts["missing"] > 0:
        status = "partial"
    elif counts["fresh"] == len(caveats) and caveats:
        status = "fresh"
    else:
        status = "unknown"

    return {
        "status": status,
        "freshness_counts": counts,
        "missing_reasons": missing_reasons,
        "blocking": False,
        "point_in_time": point_in_time,
    }


def aggregate_shared_context_quality(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"fresh": 0, "stale": 0, "missing": 0, "unknown": 0}
    missing_reasons: list[str] = []
    for payload in payloads:
        quality = payload.get("data_quality") if isinstance(payload, dict) else None
        if not isinstance(quality, dict):
            continue
        quality_counts = quality.get("freshness_counts")
        if isinstance(quality_counts, dict):
            for key in counts:
                counts[key] += int(quality_counts.get(key) or 0)
        for reason in quality.get("missing_reasons") or []:
            reason_text = str(reason)
            if reason_text not in missing_reasons:
                missing_reasons.append(reason_text)

    total = sum(counts.values())
    if counts["missing"] == total and total > 0:
        status = "missing"
    elif counts["stale"] > 0 or counts["missing"] > 0:
        status = "partial"
    elif counts["fresh"] == total and total > 0:
        status = "fresh"
    else:
        status = "unknown"
    return {
        "status": status,
        "freshness_counts": counts,
        "missing_reasons": missing_reasons,
        "blocking": False,
        "point_in_time": True,
    }


def _point_in_time_context(
    context: dict[str, Any],
    *,
    symbol: str,
    consumer: str,
    reference_date: date,
) -> dict[str, Any]:
    freshness = str(context.get("freshness") or "unknown")
    missing_reason = context.get("missing_reason")
    if freshness == "missing" or missing_reason is not None:
        return context

    as_of_date = _parse_date(context.get("as_of_date"))
    context_type = str(context.get("context_type") or "shared_background_context")
    if as_of_date is None:
        return _missing_context(
            symbol=symbol,
            context_type=context_type,
            consumer=consumer,
            missing_reason="context_as_of_date_missing",
            source_status="point_in_time_missing_as_of_date",
            reference_date=reference_date,
        )
    if as_of_date > reference_date:
        return _missing_context(
            symbol=symbol,
            context_type=context_type,
            consumer=consumer,
            missing_reason="future_context_excluded",
            source_status="point_in_time_excluded",
            reference_date=reference_date,
            excluded_as_of_date=as_of_date,
            original_replay_key=str(context.get("replay_key") or ""),
        )
    return context


def _missing_context(
    *,
    symbol: str,
    context_type: str,
    consumer: str,
    missing_reason: str,
    source_status: str,
    reference_date: date | None,
    excluded_as_of_date: date | None = None,
    original_replay_key: str | None = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "domain": "background_context",
        "provider": "shared_background_context_cache",
        "status": source_status,
    }
    if reference_date is not None:
        source["reference_date"] = reference_date.isoformat()
    if excluded_as_of_date is not None:
        source["excluded_as_of_date"] = excluded_as_of_date.isoformat()
    if original_replay_key:
        source["original_replay_key"] = original_replay_key
    return {
        "context_type": context_type,
        "source": source,
        "as_of_date": None,
        "freshness": "missing",
        "missing_reason": missing_reason,
        "replay_key": _missing_replay_key(symbol, context_type, missing_reason, reference_date),
        "applicable_consumers": [consumer],
        "payload": {},
    }


def _missing_replay_key(
    symbol: str,
    context_type: str,
    missing_reason: str,
    reference_date: date | None,
) -> str:
    date_part = reference_date.isoformat() if reference_date is not None else "missing"
    return f"background_context:{symbol}:{context_type}:{date_part}:{missing_reason}"


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
    "SHARED_CONTEXT_CONSUMER_ANALYZE",
    "SHARED_CONTEXT_CONSUMER_LIFECYCLE",
    "SHARED_CONTEXT_CONSUMER_PORTFOLIO",
    "SHARED_CONTEXT_CONSUMER_POSITION",
    "SHARED_CONTEXT_READ_VERSION",
    "aggregate_shared_context_quality",
    "read_shared_context_for_symbol",
    "shared_context_data_quality",
]
