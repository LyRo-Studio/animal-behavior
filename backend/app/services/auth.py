"""Login / refresh / logout business logic.

Kept out of the API layer per ENGINEERING-STANDARDS.md ("API endpoints
should primarily handle HTTP concerns. Business logic belongs in service
modules.").
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_opaque_token,
    verify_password,
)
from app.models.account import Account
from app.models.refresh_token import RefreshToken
from app.services.accounts import normalize_email


class AuthenticationError(Exception):
    """Raised for any login/refresh failure.

    Deliberately a single exception type with no distinguishing detail: the
    API layer maps every case (unknown email, wrong password, deactivated
    account, unknown/expired/revoked refresh token) to the same generic
    response, per the engineering standards' password-security guidance
    against revealing whether a specific account exists.
    """


def _find_refresh_token(db: Session, raw_refresh_token: str) -> RefreshToken | None:
    """Look up a RefreshToken row by its raw (unhashed) token value."""
    token_hash = hash_opaque_token(raw_refresh_token)
    return db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))


def _issue_token_pair(db: Session, account: Account) -> tuple[str, str]:
    """Create a fresh access token and a new, persisted refresh token."""
    access_token = create_access_token(account.id, account.role.value)

    raw_refresh_token = generate_opaque_token()
    db.add(
        RefreshToken(
            account_id=account.id,
            token_hash=hash_opaque_token(raw_refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    db.commit()

    return access_token, raw_refresh_token


def login(db: Session, email: str, password: str) -> tuple[str, str]:
    """Authenticate by email + password, returning a fresh (access, refresh) pair."""
    normalized_email = normalize_email(email)
    account = db.scalar(select(Account).where(Account.email == normalized_email))

    # Same generic failure whether the account doesn't exist, the password
    # is wrong, the account is deactivated, or it's an invited User who
    # hasn't set a password yet (password_hash is None until then) — never
    # reveal which.
    if (
        account is None
        or not account.is_active
        or account.password_hash is None
        or not verify_password(password, account.password_hash)
    ):
        raise AuthenticationError

    return _issue_token_pair(db, account)


def refresh(db: Session, raw_refresh_token: str) -> tuple[str, str]:
    """Rotate a valid refresh token for a new (access, refresh) pair."""
    token = _find_refresh_token(db, raw_refresh_token)

    now = datetime.now(UTC)
    if (
        token is None
        or token.revoked_at is not None
        or token.expires_at < now
        or not token.account.is_active
    ):
        raise AuthenticationError

    token.revoked_at = now
    db.add(token)

    return _issue_token_pair(db, token.account)


def logout(db: Session, raw_refresh_token: str) -> None:
    """Revoke the given refresh token, if it exists and isn't already revoked.

    Idempotent and silent on an unknown/already-revoked token — logout
    isn't an oracle for whether a given token was ever valid.
    """
    token = _find_refresh_token(db, raw_refresh_token)

    if token is not None and token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        db.add(token)
        db.commit()
