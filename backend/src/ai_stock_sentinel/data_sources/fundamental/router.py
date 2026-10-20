from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.data_sources.fundamental.service import (
    acquire_fundamental_backfill_scheduler_lock,
    backfill_fundamentals,
    create_fundamental_backfill_job,
    get_fundamental_backfill_job,
    get_oldest_running_fundamental_backfill_job,
    refresh_official_fundamentals,
    resolve_managed_fundamental_symbols,
    resolve_pending_fundamental_backfill_symbols,
)
from ai_stock_sentinel.data_sources.fundamental.schemas import (
    FundamentalBackfillRequest,
    FundamentalBackfillResponse,
    FundamentalRefreshResponse,
)
from ai_stock_sentinel.db.session import get_db


router = APIRouter(tags=["fundamentals"])


@router.post(
    "/internal/fundamentals/refresh",
    response_model=FundamentalRefreshResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_fundamentals_endpoint(db: Session = Depends(get_db)) -> dict:
    result = refresh_official_fundamentals(db)
    db.commit()
    return asdict(result)


@router.post(
    "/internal/fundamentals/backfill",
    response_model=FundamentalBackfillResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def backfill_fundamentals_endpoint(
    payload: FundamentalBackfillRequest,
    db: Session = Depends(get_db),
) -> dict:
    if payload.job_id and (payload.symbols or payload.resume_running_job):
        raise HTTPException(status_code=422, detail={"code": "fundamental_backfill_job_arguments_conflict"})
    if not payload.job_id and payload.after_symbol:
        raise HTTPException(status_code=422, detail={"code": "fundamental_backfill_job_id_required"})
    if payload.resume_running_job and payload.symbols:
        raise HTTPException(status_code=422, detail={"code": "fundamental_backfill_resume_arguments_conflict"})

    def validate_job(job, *, expected_cursor):
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "fundamental_backfill_job_not_found"})
        if job.purpose != "maintenance":
            raise HTTPException(status_code=409, detail={"code": "fundamental_backfill_purpose_mismatch"})
        if job.status == "completed":
            raise HTTPException(status_code=409, detail={"code": "fundamental_backfill_job_completed"})
        if job.next_after_symbol != expected_cursor:
            raise HTTPException(status_code=409, detail={"code": "fundamental_backfill_cursor_mismatch",
                "expected_after_symbol": job.next_after_symbol})
        return job

    job = None
    if payload.job_id:
        job = validate_job(get_fundamental_backfill_job(db, job_id=payload.job_id, for_update=True),
                           expected_cursor=payload.after_symbol)
    if job is None:
        # Serialize select/create only; provider I/O remains outside the write transaction.
        acquire_fundamental_backfill_scheduler_lock(db)
        job = get_oldest_running_fundamental_backfill_job(db, for_update=True)
        if job is not None and not payload.resume_running_job:
            raise HTTPException(status_code=409, detail={"code": "fundamental_backfill_job_running",
                "job_id": job.id, "expected_after_symbol": job.next_after_symbol})
        if job is None:
            candidates = payload.symbols if payload.symbols else resolve_managed_fundamental_symbols(db)
            symbols = resolve_pending_fundamental_backfill_symbols(db, symbols=candidates)
            job = create_fundamental_backfill_job(db, symbols=symbols, raw_pool_date=None)
    job_id, cursor, symbols, raw_pool_date = job.id, job.next_after_symbol, list(job.symbols), job.raw_pool_date
    db.commit()

    def guard():
        return validate_job(get_fundamental_backfill_job(db, job_id=job_id, for_update=True),
                            expected_cursor=cursor)

    result = backfill_fundamentals(db, symbols=symbols, after_symbol=cursor,
                                  limit=payload.limit, before_persist=guard)
    job = guard()
    job.next_after_symbol = result.next_after_symbol
    if result.next_after_symbol is None:
        job.status = "completed"
    db.commit()
    return {**asdict(result), "next_after_symbol": result.next_after_symbol,
            "job_id": job_id, "raw_pool_date": raw_pool_date, "purpose": "maintenance"}


__all__ = ["FundamentalBackfillRequest", "router"]
