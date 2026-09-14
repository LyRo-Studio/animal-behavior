from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.account import Account
from app.schemas.admin import AdminAccountOut, AdminCreateAccountRequest
from app.services.accounts import (
    AccountNotFoundError,
    CannotDeactivateAdminError,
    DeactivatedAccountExistsError,
    DuplicateActiveAccountError,
    InvalidEmailDomainError,
    deactivate_account,
    reactivate_account,
)
from app.services.invites import create_invited_account
from app.services.mail import MailTransport, get_mail_transport

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/accounts", response_model=list[AdminAccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.email)))


@router.post("/accounts", response_model=AdminAccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AdminCreateAccountRequest,
    db: Session = Depends(get_db),
    mail_transport: MailTransport = Depends(get_mail_transport),
) -> Account:
    try:
        return create_invited_account(db, mail_transport, payload.email)
    except InvalidEmailDomainError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Email must be a @vives.be or @student.vives.be address.",
        ) from None
    except DuplicateActiveAccountError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active account with this email already exists.",
        ) from None
    except DeactivatedAccountExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "An account with this email already exists but is deactivated. "
                    "Reactivate it instead of creating a new one."
                ),
                "existing_account_id": exc.account_id,
            },
        ) from None


@router.post("/accounts/{account_id}/deactivate", response_model=AdminAccountOut)
def deactivate(account_id: int, db: Session = Depends(get_db)) -> Account:
    try:
        return deactivate_account(db, account_id)
    except AccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account not found."
        ) from None
    except CannotDeactivateAdminError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin accounts cannot be deactivated."
        ) from None


@router.post("/accounts/{account_id}/reactivate", response_model=AdminAccountOut)
def reactivate(account_id: int, db: Session = Depends(get_db)) -> Account:
    try:
        return reactivate_account(db, account_id)
    except AccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account not found."
        ) from None
    except DuplicateActiveAccountError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot reactivate: another active account already uses this email.",
        ) from None
