"""Forgot-password request business logic (ticket #5).

Kept out of the API layer, same reasoning as app/services/auth.py.
Consuming the resulting token is handled by app/services/account_actions.py
(shared with ticket #4's invite tokens) — this module only covers issuing
one.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import generate_opaque_token, hash_opaque_token
from app.models.account import Account
from app.models.account_action_token import AccountActionToken, AccountActionTokenPurpose
from app.services.mail import MailMessage, MailTransport, build_set_password_link

logger = logging.getLogger(__name__)


def _build_reset_email(account: Account, raw_token: str) -> MailMessage:
    reset_link = build_set_password_link(raw_token)
    return MailMessage(
        to=account.email,
        subject="Reset your Animal Behavior password",
        text_body=(
            f"Hi {account.display_name},\n\n"
            "Someone requested a password reset for your account. If this "
            f"was you, choose a new password here:\n{reset_link}\n\n"
            f"This link expires in {settings.password_reset_token_expire_hours} hour(s).\n\n"
            "If you didn't request this, you can safely ignore this email — "
            "your password hasn't been changed."
        ),
    )


def request_password_reset(db: Session, mail_transport: MailTransport, email: str) -> None:
    """Email a reset link, but only when an active Account exists for `email`.

    Always returns None either way — per ENGINEERING-STANDARDS.md's
    guidance against revealing whether a specific account exists, the API
    layer gives the same generic response regardless of outcome (mirrors
    app/services/auth.py's AuthenticationError for the same reason on
    login). This includes a mail-transport failure: letting that raise
    would turn a 500-vs-204 split into exactly the account-existence oracle
    this function exists to prevent, so it's caught and logged, never
    propagated.
    """
    normalized_email = email.strip().lower()
    account = db.scalar(
        select(Account).where(Account.email == normalized_email, Account.is_active.is_(True))
    )
    if account is None:
        return

    raw_token = generate_opaque_token()
    db.add(
        AccountActionToken(
            account_id=account.id,
            token_hash=hash_opaque_token(raw_token),
            purpose=AccountActionTokenPurpose.PASSWORD_RESET,
            expires_at=datetime.now(UTC)
            + timedelta(hours=settings.password_reset_token_expire_hours),
        )
    )
    db.commit()

    try:
        mail_transport.send(_build_reset_email(account, raw_token))
    except Exception:
        # Never let a transport failure (e.g. SMTP down) escape as a
        # different HTTP status than the "no such account" case — see the
        # docstring above. The token is already committed, so the User can
        # still use it if the link somehow reaches them; a retry from
        # scratch just requests a fresh one.
        logger.exception("Failed to send password-reset email for account id %s", account.id)
