from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.auth.allowlist import require_allowed_account
from ai_stock_sentinel.auth.google_verifier import exchange_google_auth_code, verify_google_id_token
from ai_stock_sentinel.auth.jwt_handler import create_access_token
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    id_token: str


class GoogleCodeRequest(BaseModel):
    code: str
    redirect_uri: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    avatar_url: str | None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


@router.post("/google", response_model=TokenResponse)
def google_login(payload: GoogleLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        google_info = verify_google_id_token(payload.id_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    require_allowed_account(db, google_info.sub, stage="google_token")
    user = db.query(User).filter(User.google_sub == google_info.sub).first()
    if user is not None and (not user.is_active or user.deleted_at is not None):
        raise HTTPException(status_code=401, detail="User not found or inactive")
    if user is None:
        user = User(
            google_sub=google_info.sub,
            email=google_info.email,
            name=google_info.name,
            avatar_url=google_info.picture,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.name = google_info.name
        user.avatar_url = google_info.picture
        db.commit()
        db.refresh(user)

    token = create_access_token(user_id=user.id, email=user.email)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/google/code", response_model=TokenResponse)
def google_login_with_code(payload: GoogleCodeRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        google_info = exchange_google_auth_code(payload.code, payload.redirect_uri)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    require_allowed_account(db, google_info.sub, stage="google_code")
    user = db.query(User).filter(User.google_sub == google_info.sub).first()
    if user is not None and (not user.is_active or user.deleted_at is not None):
        raise HTTPException(status_code=401, detail="User not found or inactive")
    if user is None:
        user = User(
            google_sub=google_info.sub,
            email=google_info.email,
            name=google_info.name,
            avatar_url=google_info.picture,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.name = google_info.name
        user.avatar_url = google_info.picture
        db.commit()
        db.refresh(user)

    token = create_access_token(user_id=user.id, email=user.email)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)
