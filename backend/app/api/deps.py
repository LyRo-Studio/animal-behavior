import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.account import Account, AccountRole
from app.services.rate_limit import RateLimitResult

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


def client_ip(request: Request) -> str | None:
    """The connecting client's address, for IP-keyed rate limiting, or
    None when it genuinely can't be determined.

    No reverse-proxy header (X-Forwarded-For etc.) support: this app has
    no proxy in front of it in any current deployment (see
    docker-compose.yml) and blindly trusting such a header from an
    untrusted client would let it be spoofed to dodge the limit entirely.
    Revisit if/when a reverse proxy is introduced.

    Returns None rather than a fallback placeholder string when
    `request.client` is unset: collapsing every such caller onto one
    shared "unknown" bucket would let any one of them exhaust the budget
    for all the others — exactly the "DoS against legitimate users" this
    feature exists to prevent. Callers skip the IP-keyed check entirely
    when this is None (falling back to whatever other dimension — e.g.
    per-email — they also check), rather than share a bucket. In
    practice `request.client` is always set for this app's real
    deployment (uvicorn over TCP, per docker-compose.yml); this only
    matters for unusual ASGI transports.
    """
    return request.client.host if request.client is not None else None


def raise_if_throttled(result: RateLimitResult | None) -> None:
    """Raise 429 for a rate_limit.enforce_all(...) result that found
    something exceeded; a no-op when nothing was (result is None). An HTTP
    concern (the raised status code), so this lives here rather than in
    app/services/rate_limit.py itself.
    """
    if result is None:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please try again later.",
        headers={"Retry-After": str(int(result.retry_after_seconds) + 1)},
    )
