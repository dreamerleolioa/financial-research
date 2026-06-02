from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun, StockRawData


PUBLIC_RUN_STATUSES = ("completed", "stale_data")


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
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "PUBLIC_RUN_STATUSES",
    "create_daily_radar_run",
    "get_daily_radar_run_by_date",
    "get_final_raw_data_rows_for_date",
    "get_latest_daily_radar_run",
    "get_symbol_candidate_history",
    "replace_run_candidates",
    "update_daily_radar_run",
]
