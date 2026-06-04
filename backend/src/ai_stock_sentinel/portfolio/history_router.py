# backend/src/ai_stock_sentinel/portfolio/history_router.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import DailyAnalysisLog, UserPortfolio
from ai_stock_sentinel.db.session import get_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _portfolio_history_filters(current_user_id: int, portfolio: UserPortfolio):
    filters = [
        DailyAnalysisLog.user_id == current_user_id,
        DailyAnalysisLog.symbol == portfolio.symbol,
        DailyAnalysisLog.record_date >= portfolio.entry_date,
    ]
    if portfolio.exit_date is not None:
        filters.append(DailyAnalysisLog.record_date <= portfolio.exit_date)
    return filters


@router.get("/latest-history")
def get_portfolio_latest_history(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """回傳當前用戶所有 active 持倉的最新一筆 DailyAnalysisLog（bulk，單一 query）。"""
    portfolios = db.execute(
        select(
            UserPortfolio.id,
            UserPortfolio.symbol,
            UserPortfolio.entry_date,
            UserPortfolio.exit_date,
        ).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,  # noqa: E712
        )
    ).all()

    if not portfolios:
        return {}

    subq = (
        select(
            UserPortfolio.id.label("portfolio_id"),
            DailyAnalysisLog,
            func.row_number()
            .over(
                partition_by=UserPortfolio.id,
                order_by=DailyAnalysisLog.record_date.desc(),
            )
            .label("rn"),
        )
        .join(
            UserPortfolio,
            and_(
                DailyAnalysisLog.user_id == UserPortfolio.user_id,
                DailyAnalysisLog.symbol == UserPortfolio.symbol,
                DailyAnalysisLog.record_date >= UserPortfolio.entry_date,
                or_(
                    UserPortfolio.exit_date.is_(None),
                    DailyAnalysisLog.record_date <= UserPortfolio.exit_date,
                ),
            ),
        )
        .where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,  # noqa: E712
            DailyAnalysisLog.user_id == current_user.id,
        )
        .subquery()
    )

    rows = db.execute(
        select(subq).where(subq.c.rn == 1)
    ).mappings().all()

    latest_by_portfolio: dict[int, dict] = {}
    for row in rows:
        latest_by_portfolio[row["portfolio_id"]] = {
            "record_date":        row["record_date"].isoformat(),
            "signal_confidence":  float(row["signal_confidence"]) if row["signal_confidence"] else None,
            "action_tag":         row["action_tag"],
            "recommended_action": row["recommended_action"],
            "indicators":         row["indicators"],
            "final_verdict":      row["final_verdict"],
            "prev_action_tag":    row["prev_action_tag"],
            "prev_confidence":    float(row["prev_confidence"]) if row["prev_confidence"] else None,
        }

    return {
        p.id: latest_by_portfolio.get(p.id)
        for p in portfolios
    }


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
        .where(*_portfolio_history_filters(current_user.id, portfolio))
    ).scalar()

    records = db.execute(
        select(DailyAnalysisLog)
        .where(*_portfolio_history_filters(current_user.id, portfolio))
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
