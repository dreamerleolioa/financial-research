from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.data_sources.fundamental.service import (
    acquire_fundamental_backfill_scheduler_lock,
    backfill_fundamentals,
    create_fundamental_backfill_job,
    fundamental_raw_pool_date_is_completed,
    get_fundamental_backfill_job,
    get_oldest_running_fundamental_backfill_job,
    refresh_official_fundamentals,
    resolve_fundamental_raw_pool_symbols,
    resolve_latest_fundamental_raw_pool_date,
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
    if payload.job_id:
        if payload.symbols or payload.raw_pool_date is not None or payload.resume_running_job:
            raise HTTPException(
                status_code=422,
                detail={"code": "fundamental_backfill_job_arguments_conflict"},
            )
        job = get_fundamental_backfill_job(
            db,
            job_id=payload.job_id,
            for_update=True,
        )
        if job is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "fundamental_backfill_job_not_found"},
            )
        if job.status == "completed":
            raise HTTPException(
                status_code=409,
                detail={"code": "fundamental_backfill_job_completed"},
            )
        if payload.after_symbol != job.next_after_symbol:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "fundamental_backfill_cursor_mismatch",
                    "expected_after_symbol": job.next_after_symbol,
                },
            )
        symbols = list(job.symbols)
        raw_pool_date = job.raw_pool_date
    else:
        if payload.after_symbol:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "fundamental_backfill_job_id_required",
                },
            )
        if payload.resume_running_job and (payload.symbols or payload.raw_pool_date is not None):
            raise HTTPException(
                status_code=422,
                detail={"code": "fundamental_backfill_resume_arguments_conflict"},
            )
        acquire_fundamental_backfill_scheduler_lock(db)
        job = get_oldest_running_fundamental_backfill_job(db, for_update=True)
        if job is not None and not payload.resume_running_job:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "fundamental_backfill_job_running",
                    "job_id": job.id,
                    "expected_after_symbol": job.next_after_symbol,
                },
            )
        if job is not None:
            symbols = list(job.symbols)
            raw_pool_date = job.raw_pool_date
        elif payload.symbols:
            if payload.raw_pool_date is not None:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "fundamental_backfill_arguments_conflict"},
                )
            raw_pool_date = None
            candidates = payload.symbols
        else:
            raw_pool_date = payload.raw_pool_date
            raw_pool_date = raw_pool_date or resolve_latest_fundamental_raw_pool_date(db)
            if raw_pool_date is not None and not fundamental_raw_pool_date_is_completed(
                db,
                record_date=raw_pool_date,
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "fundamental_backfill_raw_pool_not_completed",
                        "raw_pool_date": raw_pool_date.isoformat(),
                    },
                )
            if raw_pool_date is not None and not resolve_fundamental_raw_pool_symbols(
                db,
                record_date=raw_pool_date,
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "fundamental_backfill_raw_pool_not_found",
                        "raw_pool_date": raw_pool_date.isoformat(),
                    },
                )
            candidates = resolve_managed_fundamental_symbols(
                db,
                raw_pool_date=raw_pool_date,
            )
        if job is None:
            symbols = resolve_pending_fundamental_backfill_symbols(db, symbols=candidates)
            job = create_fundamental_backfill_job(
                db,
                symbols=symbols,
                raw_pool_date=raw_pool_date,
            )
    result = backfill_fundamentals(
        db,
        symbols=symbols,
        after_symbol=job.next_after_symbol,
        limit=payload.limit,
    )
    job.next_after_symbol = result.next_after_symbol
    if result.next_after_symbol is None:
        job.status = "completed"
    db.add(job)
    db.commit()
    return {
        **asdict(result),
        "next_after_symbol": job.next_after_symbol,
        "job_id": job.id,
        "raw_pool_date": raw_pool_date,
    }


__all__ = ["FundamentalBackfillRequest", "router"]
