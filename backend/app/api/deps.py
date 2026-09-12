import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.account import Account, AccountRole

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Account:
    """Resolve the Account for a valid, unexpired access token.

    401s on anything wrong with the token *and* on a deactivated account —
    the latter is defense-in-depth on top of the refresh-cycle guarantee in
    docs/adr/0001-jwt-access-refresh-tokens.md, not a replacement for it (an
    already-issued access token otherwise can't be revoked before it
    naturally expires).
    """
    unauthenticated = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthenticated

    try:
        payload = decode_access_token(credentials.credentials)
        account_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthenticated from None

    account = db.get(Account, account_id)
    if account is None or not account.is_active:
        raise unauthenticated

    return account


def require_admin(account: Account = Depends(get_current_account)) -> Account:
    """Resolve the current Account and require it to be an Admin.

    403 (not 401 — the caller *is* authenticated, just not authorized) for
    a non-Admin request to an Admin-only endpoint.
    """
    if account.role != AccountRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return account
