"""Consuming an account-action token (invite or password-reset) to set a password.

Shared by ticket #4 (invite) and ticket #5 (forgot-password reset) — see
issue #1's "Tokens" decision: setting a password via either token type
follows the same rule (unexpired + unused token; mark it used; revoke every
outstanding refresh token for that Account).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_opaque_token, hash_password
from app.models.account_action_token import AccountActionToken
from app.services.refresh_tokens import revoke_all_refresh_tokens


class InvalidActionTokenError(Exception):
    """Raised for an unknown, expired, or already-used action token."""


def _find_action_token(db: Session, raw_token: str) -> AccountActionToken | None:
    token_hash = hash_opaque_token(raw_token)
    return db.scalar(select(AccountActionToken).where(AccountActionToken.token_hash == token_hash))


def set_password(db: Session, raw_token: str, new_password: str) -> None:
    """Validate an action token and set the new password it authorizes."""
    token = _find_action_token(db, raw_token)

    now = datetime.now(UTC)
    if token is None or token.used_at is not None or token.expires_at < now:
        raise InvalidActionTokenError

    token.used_at = now
    db.add(token)

    account = token.account
    account.password_hash = hash_password(new_password)
    db.add(account)

    # Revoke every outstanding refresh token for this Account — matters
    # most for a password reset (ticket #5): forces re-login on every other
    # device/session. A no-op for a fresh invite, which has none yet.
    revoke_all_refresh_tokens(db, account.id, now)

    db.commit()
