# backend/src/ai_stock_sentinel/portfolio/history_router.py
from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import DailyAnalysisLog, UserPortfolio
from ai_stock_sentinel.db.session import get_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

LEGACY_ACTION_RISK_LANGUAGE = {
    "Hold": ("stable", "風險狀態穩定"),
    "觀望": ("stable", "風險狀態穩定"),
    "Trim": ("elevated", "風險狀態升高"),
    "減碼": ("elevated", "風險狀態升高"),
    "Exit": ("critical", "防守條件已觸發"),
    "出場": ("critical", "防守條件已觸發"),
}


def _portfolio_history_filters(current_user_id: int, portfolio: UserPortfolio):
    filters = [
        DailyAnalysisLog.user_id == current_user_id,
        DailyAnalysisLog.symbol == portfolio.symbol,
        DailyAnalysisLog.record_date >= portfolio.entry_date,
    ]
    if portfolio.exit_date is not None:
        filters.append(DailyAnalysisLog.record_date <= portfolio.exit_date)
    return filters


def _row_get(row, key: str):
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _history_risk_language(row) -> dict:
    indicators = _row_get(row, "indicators")
    recommended_action = _row_get(row, "recommended_action")
    if isinstance(indicators, dict):
        risk_language = indicators.get("position_risk_language")
        if isinstance(risk_language, dict) and risk_language.get("risk_state"):
            return {
                "risk_state": risk_language.get("risk_state"),
                "risk_state_label": risk_language.get("risk_state_label") or "風險狀態",
                "discipline_triggers": list(risk_language.get("discipline_triggers") or []),
                "risk_control_reference": risk_language.get("risk_control_reference"),
                "compatibility_source": "position_risk_language",
            }
    if recommended_action:
        risk_state, risk_state_label = LEGACY_ACTION_RISK_LANGUAGE.get(
            str(recommended_action),
            ("unknown", "歷史狀態待確認"),
        )
        return {
            "risk_state": risk_state,
            "risk_state_label": risk_state_label,
            "discipline_triggers": [],
            "risk_control_reference": None,
            "compatibility_source": "legacy_recommended_action",
        }
    return {
        "risk_state": "unknown",
        "risk_state_label": "資料不足",
        "discipline_triggers": [],
        "risk_control_reference": None,
        "compatibility_source": "insufficient_history_data",
    }


def _history_record_from_mapping(row: Mapping) -> dict:
    return {
        "record_date":        _row_get(row, "record_date").isoformat(),
        "signal_confidence":  float(_row_get(row, "signal_confidence")) if _row_get(row, "signal_confidence") else None,
        "action_tag":         _row_get(row, "action_tag"),
        "recommended_action": _row_get(row, "recommended_action"),
        "indicators":         _row_get(row, "indicators"),
        "final_verdict":      _row_get(row, "final_verdict"),
        "prev_action_tag":    _row_get(row, "prev_action_tag"),
        "prev_confidence":    float(_row_get(row, "prev_confidence")) if _row_get(row, "prev_confidence") else None,
    } | _history_risk_language(row)


def _history_record_from_log(row: DailyAnalysisLog) -> dict:
    return {
        "record_date":        row.record_date.isoformat(),
        "signal_confidence":  float(row.signal_confidence) if row.signal_confidence else None,
        "action_tag":         row.action_tag,
        "recommended_action": row.recommended_action,
        "indicators":         row.indicators,
        "final_verdict":      row.final_verdict,
        "prev_action_tag":    row.prev_action_tag,
        "prev_confidence":    float(row.prev_confidence) if row.prev_confidence else None,
    } | _history_risk_language(row)


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
        latest_by_portfolio[row["portfolio_id"]] = _history_record_from_mapping(row)

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
            _history_record_from_log(r)
            for r in records
        ],
    }
