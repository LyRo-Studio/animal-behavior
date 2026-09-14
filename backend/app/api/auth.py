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


def _check_login_budgets(
    limiter: RateLimiter, ip: str | None, email_key: str, *, record: bool
) -> None:
    """Check (and, if `record`, count one attempt against) login's two
    budgets — per-IP and per-email — raising 429 if either is exceeded.

    `record=False` is a peek: reject an already-exhausted caller up front,
    without paying for a password verification (Argon2 is deliberately
    expensive) it would fail anyway. `record=True` is used after a failed
    login — see login()'s docstring for why only failures count, and why
    per-email is safe here.
    """
    method = limiter.hit if record else limiter.peek
    results: list[RateLimitResult] = []
    if ip is not None:
        results.append(
            method(
                f"login:ip:{ip}",
                limit=settings.login_rate_limit_max_failed_attempts_per_ip,
                window_seconds=settings.login_rate_limit_window_seconds,
            )
        )
    results.append(
        method(
            email_key,
            limit=settings.login_rate_limit_max_failed_attempts_per_email,
            window_seconds=settings.login_rate_limit_window_seconds,
        )
    )
    raise_if_throttled(enforce_all(*results))


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> TokenResponse:
    """Rate-limited on two dimensions, both counting only *failed*
    attempts — see app/core/config.py's note on why only failures count,
    and why tracking per-email (as well as per-IP) is safe here.
    """
    ip = client_ip(request)
    email_key = f"login:email:{normalize_email(payload.email)}"

    _check_login_budgets(limiter, ip, email_key, record=False)

    try:
        access_token, refresh_token = auth_service.login(db, payload.email, payload.password)
    except auth_service.AuthenticationError:
        _check_login_budgets(limiter, ip, email_key, record=True)
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
