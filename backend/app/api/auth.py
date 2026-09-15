from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import client_ip, raise_if_throttled
from app.core.config import settings
from app.db.session import get_db
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
)
from app.services import auth as auth_service
from app.services.accounts import normalize_email
from app.services.mail import MailTransport, get_mail_transport
from app.services.password_reset import request_password_reset
from app.services.rate_limit import RateLimiter, RateLimitResult, enforce_all, get_rate_limiter

router = APIRouter(prefix="/auth", tags=["auth"])

_GENERIC_LOGIN_FAILURE = "Invalid email or password"
_GENERIC_REFRESH_FAILURE = "Invalid or expired refresh token"


def _check_login_ip_budget(
    limiter: RateLimiter, ip: str | None, *, record: bool
) -> RateLimitResult | None:
    """Login's per-IP budget. Peeked (`record=False`) before authentication
    runs, to reject an already-exhausted IP up front without paying for a
    password verification (Argon2 is deliberately expensive) it would fail
    anyway; hit (`record=True`) after a confirmed failure.

    Safe to gate on pre-auth, unlike the per-email budget below: exhausting
    it only ever blocks requests from one particular IP, never a specific
    account regardless of which IP its real owner next logs in from.
    """
    if ip is None:
        return None
    method = limiter.hit if record else limiter.peek
    return method(
        f"login:ip:{ip}",
        limit=settings.login_rate_limit_max_failed_attempts_per_ip,
        window_seconds=settings.login_rate_limit_window_seconds,
    )


def _hit_login_email_budget(limiter: RateLimiter, email_key: str) -> RateLimitResult:
    """Login's per-email budget. Only ever `hit` *after* a confirmed
    authentication failure — deliberately never `peek`ed pre-auth — so a
    correct password always succeeds no matter how many other (necessarily
    wrong) attempts have recently failed against this account, e.g.
    distributed across many source IPs that each stay under the per-IP
    budget above.

    Peeking this dimension pre-auth, like the per-IP one, would turn it
    into an account-specific denial-of-service gate: an attacker could
    exhaust it with wrong guesses spread across IPs that never trip the
    per-IP budget, and the real owner's next attempt — even with the
    correct password — would then be rejected by the peek before
    authentication ever ran. See ticket #7's requirement that throttling
    not lock out legitimate users.
    """
    return limiter.hit(
        email_key,
        limit=settings.login_rate_limit_max_failed_attempts_per_email,
        window_seconds=settings.login_rate_limit_window_seconds,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> TokenResponse:
    """Rate-limited on two dimensions, both counting only *failed*
    attempts. Correct credentials always succeed regardless of either
    budget's state: the per-email dimension is never checked until a
    failure is confirmed (see `_hit_login_email_budget`), and the per-IP
    dimension is bypassed entirely once `auth_service.login` succeeds.
    """
    ip = client_ip(request)
    email_key = f"login:email:{normalize_email(payload.email)}"

    ip_peek = _check_login_ip_budget(limiter, ip, record=False)
    if ip_peek is not None:
        raise_if_throttled(enforce_all(ip_peek))

    try:
        access_token, refresh_token = auth_service.login(db, payload.email, payload.password)
    except auth_service.AuthenticationError:
        results = [_hit_login_email_budget(limiter, email_key)]
        ip_hit = _check_login_ip_budget(limiter, ip, record=True)
        if ip_hit is not None:
            results.append(ip_hit)
        raise_if_throttled(enforce_all(*results))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_FAILURE
        ) from None

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


def _check_refresh_budget(limiter: RateLimiter, ip: str | None, *, record: bool) -> None:
    """Same peek-then-hit-on-failure shape as _check_login_budgets, but
    IP-only — there's no account identifier in a refresh request to key on
    in the first place (just an opaque token)."""
    if ip is None:
        return
    method = limiter.hit if record else limiter.peek
    result = method(
        f"refresh:ip:{ip}",
        limit=settings.refresh_rate_limit_max_failed_attempts_per_ip,
        window_seconds=settings.refresh_rate_limit_window_seconds,
    )
    raise_if_throttled(enforce_all(result))


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> TokenResponse:
    ip = client_ip(request)
    _check_refresh_budget(limiter, ip, record=False)

    try:
        access_token, refresh_token = auth_service.refresh(db, payload.refresh_token)
    except auth_service.AuthenticationError:
        _check_refresh_budget(limiter, ip, record=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_REFRESH_FAILURE
        ) from None

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> None:
    auth_service.logout(db, payload.refresh_token)


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    mail_transport: MailTransport = Depends(get_mail_transport),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    """Always the same empty 204, whether or not the email matches an
    active account — see request_password_reset's docstring.

    Rate-limited on every request (not just "failures" — there's no such
    distinction visible here, the response is always the same), both
    per-IP and per-email. Per-email is safe here in a way it wouldn't be
    for login if login counted every request too: exceeding it never locks
    anyone out of logging in, only out of requesting more reset emails for
    a bit — see app/core/config.py.
    """
    ip = client_ip(request)
    email_key = f"forgot-password:email:{normalize_email(payload.email)}"

    results: list[RateLimitResult] = []
    if ip is not None:
        results.append(
            limiter.hit(
                f"forgot-password:ip:{ip}",
                limit=settings.forgot_password_rate_limit_max_attempts_per_ip,
                window_seconds=settings.forgot_password_rate_limit_window_seconds,
            )
        )
    results.append(
        limiter.hit(
            email_key,
            limit=settings.forgot_password_rate_limit_max_attempts_per_email,
            window_seconds=settings.forgot_password_rate_limit_window_seconds,
        )
    )
    raise_if_throttled(enforce_all(*results))

    request_password_reset(db, mail_transport, payload.email)
