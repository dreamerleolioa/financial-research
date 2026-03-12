# backend/src/ai_stock_sentinel/portfolio/history_router.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import DailyAnalysisLog, UserPortfolio
from ai_stock_sentinel.db.session import get_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/{portfolio_id}/history")
def get_portfolio_history(
    portfolio_id: int,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    portfolio = db.get(UserPortfolio, portfolio_id)
    if not portfolio or portfolio.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="持倉不存在")

    total = db.execute(
        select(func.count())
        .select_from(DailyAnalysisLog)
        .where(
            DailyAnalysisLog.user_id == current_user.id,
            DailyAnalysisLog.symbol == portfolio.symbol,
        )
    ).scalar()

    records = db.execute(
        select(DailyAnalysisLog)
        .where(
            DailyAnalysisLog.user_id == current_user.id,
            DailyAnalysisLog.symbol == portfolio.symbol,
        )
        .order_by(DailyAnalysisLog.record_date.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return {
        "symbol": portfolio.symbol,
        "total": total,
        "records": [
            {
                "record_date":        r.record_date.isoformat(),
                "signal_confidence":  float(r.signal_confidence) if r.signal_confidence else None,
                "action_tag":         r.action_tag,
                "recommended_action": r.recommended_action,
                "indicators":         r.indicators,
                "final_verdict":      r.final_verdict,
                "prev_action_tag":    r.prev_action_tag,
                "prev_confidence":    float(r.prev_confidence) if r.prev_confidence else None,
            }
            for r in records
        ],
    }
