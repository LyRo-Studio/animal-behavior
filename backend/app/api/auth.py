from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, TokenResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_GENERIC_LOGIN_FAILURE = "Invalid email or password"
_GENERIC_REFRESH_FAILURE = "Invalid or expired refresh token"


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        access_token, refresh_token = auth_service.login(db, payload.email, payload.password)
    except auth_service.AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_FAILURE
        ) from None

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        access_token, refresh_token = auth_service.refresh(db, payload.refresh_token)
    except auth_service.AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_REFRESH_FAILURE
        ) from None

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> None:
    auth_service.logout(db, payload.refresh_token)
