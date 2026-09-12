from fastapi import APIRouter, Depends

from app.api.deps import get_current_account
from app.models.account import Account
from app.schemas.account import AccountOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/me", response_model=AccountOut)
def read_current_account(account: Account = Depends(get_current_account)) -> Account:
    return account
