"""Live, uncached admission checks shared by Google login and existing sessions."""
from __future__ import annotations

import json
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ai_stock_sentinel.user_models.user import AllowedGoogleAccount

logger = logging.getLogger(__name__)


def require_allowed_account(db: Session, google_sub: str, *, stage: str) -> None:
    # Select a scalar each time: an ORM identity-map hit must not cache permission.
    # PostgreSQL's READ COMMITTED sees membership committed before this statement.
    try:
        allowed = db.scalar(select(AllowedGoogleAccount.google_sub).where(
            AllowedGoogleAccount.google_sub == google_sub,
        ))
    except SQLAlchemyError:
        logger.error(json.dumps({"event": "auth_access_check_unavailable", "stage": stage}))
        raise HTTPException(status_code=503, detail={
            "code": "ACCESS_CHECK_UNAVAILABLE", "message": "目前無法確認使用權限，請稍後再試。",
        }, headers={"Retry-After": "60"}) from None
    if allowed is None:
        logger.warning(json.dumps({
            "event": "auth_access_denied", "reason": "not_allowlisted", "stage": stage,
        }))
        raise HTTPException(status_code=403, detail={
            "code": "ACCOUNT_NOT_ALLOWLISTED", "message": "此帳號尚未取得使用權限，請聯絡管理者。",
        })
