from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_account
from app.db.session import get_db
from app.models.account import Account
from app.schemas.account import AccountOut
from app.schemas.account_actions import SetPasswordRequest
from app.services.account_actions import InvalidActionTokenError
from app.services.account_actions import set_password as set_password_service

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/me", response_model=AccountOut)
def read_current_account(account: Account = Depends(get_current_account)) -> Account:
    return account


@router.post("/set-password", status_code=status.HTTP_204_NO_CONTENT)
def set_password(payload: SetPasswordRequest, db: Session = Depends(get_db)) -> None:
    """Consume an invite/reset token (unauthenticated — the caller has no session yet)."""
    try:
        set_password_service(db, payload.token, payload.password)
    except InvalidActionTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This link is invalid or has expired.",
        ) from None
