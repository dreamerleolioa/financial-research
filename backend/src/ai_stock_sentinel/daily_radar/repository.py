from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun, SharedBackgroundContext, StockRawData


PUBLIC_RUN_STATUSES = ("completed", "stale_data")
BACKGROUND_CONTEXT_TYPES = ("weekly_major_holders", "lending", "full_margin")
BACKGROUND_CONTEXT_CONSUMER_DAILY_RADAR = "daily_radar"


def create_daily_radar_run(
    session: Session,
    *,
    run_date: date,
    market: str,
    status: str = "running",
) -> DailyRadarRun:
    run = DailyRadarRun(
        run_date=run_date,
        market=market,
        status=status,
        started_at=_utc_now(),
        universe_count=0,
        prefilter_count=0,
        candidate_count=0,
        errors=[],
    )
    session.add(run)
    session.flush()
    return run


def update_daily_radar_run(
    session: Session,
    run: DailyRadarRun,
    *,
    status: str,
    universe_count: int | None = None,
    prefilter_count: int | None = None,
    candidate_count: int | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> DailyRadarRun:
    run.status = status
    if status != "running":
        run.finished_at = _utc_now()
    if universe_count is not None:
        run.universe_count = universe_count
    if prefilter_count is not None:
        run.prefilter_count = prefilter_count
    if candidate_count is not None:
        run.candidate_count = candidate_count
    if errors is not None:
        run.errors = list(errors)
    session.add(run)
    session.flush()
    return run


def replace_run_candidates(
    session: Session,
    run: DailyRadarRun,
    candidates: Iterable[Mapping[str, Any]],
) -> list[DailyRadarCandidate]:
    session.execute(delete(DailyRadarCandidate).where(DailyRadarCandidate.run_id == run.id))
    persisted: list[DailyRadarCandidate] = []
    for payload in candidates:
        candidate = DailyRadarCandidate(
            run_id=run.id,
            symbol=str(payload["symbol"]),
            name=str(payload["name"]),
            primary_bucket=str(payload["primary_bucket"]),
            secondary_buckets=list(payload.get("secondary_buckets") or []),
            observation_score=int(payload["observation_score"]),
            bucket_scores=dict(_mapping(payload.get("bucket_scores"))),
            risk_labels=list(payload.get("risk_labels") or []),
            matched_rules=list(payload.get("matched_rules") or []),
            explanation=str(payload["explanation"]),
            repeat_status=str(payload["repeat_status"]),
            score_breakdown=dict(_mapping(payload.get("score_breakdown"))),
            input_snapshot=dict(_mapping(payload.get("input_snapshot"))),
            data_dates=dict(_mapping(payload.get("data_dates"))),
        )
        session.add(candidate)
        persisted.append(candidate)
    session.flush()
    return persisted


def get_latest_daily_radar_run(
    session: Session,
    *,
    market: str,
    statuses: tuple[str, ...] = PUBLIC_RUN_STATUSES,
) -> DailyRadarRun | None:
    return session.execute(
        _public_run_query(market=market, statuses=statuses)
        .order_by(
            DailyRadarRun.run_date.desc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def get_daily_radar_run_by_date(
    session: Session,
    *,
    run_date: date,
    market: str,
    statuses: tuple[str, ...] = PUBLIC_RUN_STATUSES,
) -> DailyRadarRun | None:
    return session.execute(
        _public_run_query(market=market, statuses=statuses)
        .where(DailyRadarRun.run_date == run_date)
        .order_by(DailyRadarRun.created_at.desc(), DailyRadarRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_symbol_candidate_history(
    session: Session,
    *,
    symbols: Iterable[str],
    before_date: date,
    lookback_days: int = 5,
    market: str,
) -> list[dict[str, Any]]:
    symbol_set = {symbol for symbol in symbols}
    if not symbol_set:
        return []

    earliest_date = before_date - timedelta(days=lookback_days)
    rows = session.execute(
        select(DailyRadarCandidate, DailyRadarRun)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .where(
            DailyRadarRun.market == market,
            DailyRadarRun.status.in_(PUBLIC_RUN_STATUSES),
            DailyRadarRun.run_date >= earliest_date,
            DailyRadarRun.run_date < before_date,
            DailyRadarCandidate.symbol.in_(symbol_set),
        )
        .order_by(
            DailyRadarRun.run_date.desc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
            DailyRadarCandidate.symbol.asc(),
        )
    ).all()
    return [_candidate_history_dict(candidate, run) for candidate, run in rows]


def get_final_raw_data_rows_for_date(session: Session, *, run_date: date) -> list[StockRawData]:
    return session.scalars(
        select(StockRawData)
        .where(
            StockRawData.record_date == run_date,
            StockRawData.raw_data_is_final.is_(True),
        )
        .order_by(StockRawData.symbol.asc())
    ).all()


def get_final_raw_data_rows_for_symbols(
    session: Session,
    *,
    run_date: date,
    symbols: Iterable[str],
) -> list[StockRawData]:
    ordered_symbols = _ordered_unique_symbols(symbols)
    if not ordered_symbols:
        return []

    rows = session.scalars(
        select(StockRawData).where(
            StockRawData.record_date == run_date,
            StockRawData.raw_data_is_final.is_(True),
            StockRawData.symbol.in_(ordered_symbols),
        )
    ).all()
    rows_by_symbol = {row.symbol: row for row in rows}
    return [rows_by_symbol[symbol] for symbol in ordered_symbols if symbol in rows_by_symbol]


def upsert_shared_background_context(
    session: Session,
    *,
    symbol: str,
    context_type: str,
    applicable_consumers: Iterable[str],
    source: Mapping[str, Any],
    as_of_date: date | None,
    freshness: str,
    payload: Mapping[str, Any] | None = None,
    missing_reason: str | None = None,
    replay_key: str | None = None,
) -> SharedBackgroundContext:
    normalized_symbol = _normalize_symbol(symbol)
    normalized_context_type = str(context_type).strip()
    if not normalized_symbol:
        raise ValueError("symbol is required")
    if not normalized_context_type:
        raise ValueError("context_type is required")

    record = session.execute(
        select(SharedBackgroundContext).where(
            SharedBackgroundContext.symbol == normalized_symbol,
            SharedBackgroundContext.context_type == normalized_context_type,
        )
    ).scalar_one_or_none()
    if record is None:
        record = SharedBackgroundContext(
            symbol=normalized_symbol,
            context_type=normalized_context_type,
            applicable_consumers=_ordered_unique_strings(applicable_consumers),
            source=dict(source),
            as_of_date=as_of_date,
            freshness=str(freshness),
            payload=dict(payload or {}),
            missing_reason=missing_reason,
            replay_key=replay_key or _background_context_replay_key(
                normalized_symbol,
                normalized_context_type,
                as_of_date=as_of_date,
                freshness=freshness,
            ),
        )
    else:
        record.applicable_consumers = _ordered_unique_strings(applicable_consumers)
        record.source = dict(source)
        record.as_of_date = as_of_date
        record.freshness = str(freshness)
        record.payload = dict(payload or {})
        record.missing_reason = missing_reason
        record.replay_key = replay_key or _background_context_replay_key(
            normalized_symbol,
            normalized_context_type,
            as_of_date=as_of_date,
            freshness=freshness,
        )
    session.add(record)
    session.flush()
    return record


def get_shared_background_context_rows(
    session: Session,
    *,
    symbols: Iterable[str],
    context_types: Iterable[str] | None = None,
) -> list[SharedBackgroundContext]:
    ordered_symbols = _ordered_unique_symbols(symbols)
    if not ordered_symbols:
        return []
    active_context_types = _ordered_unique_strings(context_types or BACKGROUND_CONTEXT_TYPES)
    if not active_context_types:
        return []
    return session.scalars(
        select(SharedBackgroundContext)
        .where(
            SharedBackgroundContext.symbol.in_(ordered_symbols),
            SharedBackgroundContext.context_type.in_(active_context_types),
        )
        .order_by(
            SharedBackgroundContext.symbol.asc(),
            SharedBackgroundContext.context_type.asc(),
        )
    ).all()


def get_shared_background_context_trace_by_symbol(
    session: Session,
    *,
    symbols: Iterable[str],
    context_types: Iterable[str] | None = None,
    consumer: str = BACKGROUND_CONTEXT_CONSUMER_DAILY_RADAR,
) -> dict[str, list[dict[str, Any]]]:
    ordered_symbols = _ordered_unique_symbols(symbols)
    active_context_types = _ordered_unique_strings(context_types or BACKGROUND_CONTEXT_TYPES)
    traces = {symbol: [] for symbol in ordered_symbols}
    rows = get_shared_background_context_rows(
        session,
        symbols=ordered_symbols,
        context_types=active_context_types,
    )
    rows_by_key = {(row.symbol, row.context_type): row for row in rows}
    for symbol in ordered_symbols:
        for context_type in active_context_types:
            row = rows_by_key.get((symbol, context_type))
            if row is None:
                traces[symbol].append(
                    _missing_background_context_trace(
                        symbol,
                        context_type,
                        consumer=consumer,
                        missing_reason="context_cache_missing",
                    )
                )
            else:
                traces[symbol].append(_background_context_trace(row))
    return traces


def _public_run_query(*, market: str, statuses: tuple[str, ...]):
    return (
        select(DailyRadarRun)
        .options(selectinload(DailyRadarRun.candidates))
        .where(DailyRadarRun.market == market, DailyRadarRun.status.in_(statuses))
    )


def _candidate_history_dict(candidate: DailyRadarCandidate, run: DailyRadarRun) -> dict[str, Any]:
    return {
        "symbol": candidate.symbol,
        "name": candidate.name,
        "record_date": run.run_date.isoformat(),
        "primary_bucket": candidate.primary_bucket,
        "secondary_buckets": list(candidate.secondary_buckets or []),
        "observation_score": candidate.observation_score,
        "risk_labels": list(candidate.risk_labels or []),
        "repeat_status": candidate.repeat_status,
        "bucket_scores": dict(candidate.bucket_scores or {}),
        "matched_rules": list(candidate.matched_rules or []),
        "score_breakdown": dict(candidate.score_breakdown or {}),
        "input_snapshot": dict(candidate.input_snapshot or {}),
        "data_dates": dict(candidate.data_dates or {}),
        "scoring_version": _trace_version(candidate.score_breakdown, "scoring_version"),
        "rule_version": _trace_version(candidate.score_breakdown, "rule_version"),
    }


def _background_context_trace(row: SharedBackgroundContext) -> dict[str, Any]:
    return {
        "context_type": row.context_type,
        "source": dict(row.source or {}),
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date is not None else None,
        "freshness": row.freshness,
        "missing_reason": row.missing_reason,
        "replay_key": row.replay_key,
        "applicable_consumers": list(row.applicable_consumers or []),
        "payload": dict(row.payload or {}),
    }


def _missing_background_context_trace(
    symbol: str,
    context_type: str,
    *,
    consumer: str,
    missing_reason: str,
) -> dict[str, Any]:
    return {
        "context_type": context_type,
        "source": {
            "domain": "background_context",
            "provider": "shared_background_context_cache",
            "status": "cache_miss",
        },
        "as_of_date": None,
        "freshness": "missing",
        "missing_reason": missing_reason,
        "replay_key": _background_context_replay_key(
            symbol,
            context_type,
            as_of_date=None,
            freshness="missing",
        ),
        "applicable_consumers": [consumer],
        "payload": {},
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _trace_version(payload: Any, key: str) -> str | None:
    if isinstance(payload, Mapping) and payload.get(key) is not None:
        return str(payload[key])
    return None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip()


def _ordered_unique_symbols(symbols: Iterable[str]) -> list[str]:
    ordered_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        ordered_symbols.append(normalized)
        seen.add(normalized)
    return ordered_symbols


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    ordered_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        ordered_values.append(normalized)
        seen.add(normalized)
    return ordered_values


def _background_context_replay_key(
    symbol: str,
    context_type: str,
    *,
    as_of_date: date | None,
    freshness: str,
) -> str:
    date_part = as_of_date.isoformat() if as_of_date is not None else str(freshness or "missing")
    return f"background_context:{symbol}:{context_type}:{date_part}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "BACKGROUND_CONTEXT_CONSUMER_DAILY_RADAR",
    "BACKGROUND_CONTEXT_TYPES",
    "PUBLIC_RUN_STATUSES",
    "create_daily_radar_run",
    "get_daily_radar_run_by_date",
    "get_final_raw_data_rows_for_date",
    "get_final_raw_data_rows_for_symbols",
    "get_latest_daily_radar_run",
    "get_shared_background_context_rows",
    "get_shared_background_context_trace_by_symbol",
    "get_symbol_candidate_history",
    "replace_run_candidates",
    "upsert_shared_background_context",
    "update_daily_radar_run",
]
