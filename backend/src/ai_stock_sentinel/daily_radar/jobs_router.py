"""Internal durable execution API. Callers submit intent, never arbitrary work."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.work_queue import durable_enabled
from ai_stock_sentinel.daily_radar.source_snapshots import snapshots_enabled
from ai_stock_sentinel.daily_radar.jobs import create_job, job_status, due_jobs
from ai_stock_sentinel.daily_radar.schemas import JobRequest
from ai_stock_sentinel.daily_radar.work_executor import advance_job

router = APIRouter(prefix='/internal/daily-radar', dependencies=[Depends(require_daily_radar_internal_auth)])




def capabilities(db):
    tables = set(inspect(db.get_bind()).get_table_names())
    migrated = {'radar_jobs', 'radar_work_items', 'radar_job_work', 'radar_work_attempts',
                'radar_source_snapshots', 'radar_input_manifests', 'radar_run_inputs'} <= tables
    return {'version': 'radar-durable-v1', 'enabled': durable_enabled() and snapshots_enabled() and migrated,
            'durable_execution': durable_enabled(), 'source_snapshots': snapshots_enabled(), 'migrated': migrated}


def require_enabled(db):
    if not capabilities(db)['enabled']:
        raise HTTPException(status_code=503, detail={'code': 'radar_durable_execution_not_ready'})


@router.get('/capabilities')
def read_capabilities(db: Session = Depends(get_db)):
    return capabilities(db)


@router.post('/jobs', status_code=202)
def register_job(request: JobRequest, db: Session = Depends(get_db)):
    require_enabled(db)
    job = create_job(db, run_date=request.run_date, lanes=request.lanes)
    return job_status(db, job.id) | {'accepted': True}


@router.get('/jobs/due')
def read_due_jobs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    require_enabled(db)
    return {'jobs': due_jobs(db, limit=limit)}


@router.get('/jobs/{job_id}')
def read_job(job_id: int, db: Session = Depends(get_db)):
    require_enabled(db)
    try:
        return job_status(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={'code': str(exc)}) from exc


@router.post('/jobs/{job_id}/advance')
def advance(job_id: int, db: Session = Depends(get_db)):
    require_enabled(db)
    try:
        return advance_job(db, job_id)
    except ValueError as exc:
        if str(exc) != 'radar_job_not_found':
            raise
        raise HTTPException(status_code=404, detail={'code': str(exc)}) from exc
