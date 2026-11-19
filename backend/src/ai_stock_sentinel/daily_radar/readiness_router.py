"""Authenticated operational reads, separate from public research summaries."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.readiness import read_readiness, validation_inventory
from ai_stock_sentinel.daily_radar.schemas import ValidationGapBaseline
from ai_stock_sentinel.radar_gap_contract import COMPARISON_VERSION, baseline_digest
from ai_stock_sentinel.db.session import get_db

router = APIRouter()


@router.get("/internal/daily-radar/readiness", dependencies=[Depends(require_daily_radar_internal_auth)])
def get_readiness(run_date: date, market: Literal["TW"] = "TW",
                  lookback_days: int = Query(default=365, ge=1, le=1095), db: Session = Depends(get_db),
                  include_all_validation_gaps: bool = False, scope: Literal['all', 'publication', 'validation'] = 'all'):
    with db.no_autoflush:
        return read_readiness(db, run_date=run_date, lookback_days=lookback_days,
                              include_all_validation_gaps=include_all_validation_gaps, scope=scope)


@router.post("/internal/daily-radar/readiness/compare", dependencies=[Depends(require_daily_radar_internal_auth)])
def compare_validation_gaps(run_date: date, baseline: ValidationGapBaseline, db: Session = Depends(get_db)):
    hashes = set(baseline.hashes)
    if baseline.as_of_date > run_date or len(hashes) != len(baseline.hashes):
        raise HTTPException(422, detail='invalid_validation_gap_baseline')
    if db.get_bind().dialect.name == 'postgresql':
        db.connection(execution_options={'isolation_level': 'REPEATABLE READ', 'postgresql_readonly': True})
    with db.no_autoflush:
        validation = validation_inventory(db, as_of_date=run_date, sample_limit=5, known_hashes=hashes)
    for lane in validation.values():
        reasons = lane['reasons']
        if len(reasons) > 32:
            top = sorted(reasons, key=lambda reason: (-reasons[reason], reason))[:32]
            lane['reasons'] = {reason: reasons[reason] for reason in top}
            lane['other_reason_count'] = lane['unresolved_count'] - sum(lane['reasons'].values())
    return {'version': COMPARISON_VERSION, 'market': 'TW', 'scope': 'validation',
            'requested_run_date': str(run_date), 'effective_run_date': str(run_date),
            'baseline_digest': baseline_digest(str(baseline.as_of_date), hashes), 'validation': validation}
