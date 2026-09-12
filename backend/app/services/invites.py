"""Admin-invites-a-User business logic (ticket #4).

Kept out of the API layer, same reasoning as app/services/auth.py.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import generate_opaque_token, hash_opaque_token
from app.models.account import Account, AccountRole
from app.models.account_action_token import AccountActionToken, AccountActionTokenPurpose
from app.services.accounts import derive_display_name, validate_email_domain
from app.services.mail import MailMessage, MailTransport


class DuplicateActiveAccountError(Exception):
    """Raised when an active Account already exists for the given email."""


def _build_invite_email(account: Account, raw_token: str) -> MailMessage:
    invite_link = f"{settings.frontend_base_url}/set-password?token={raw_token}"
    return MailMessage(
        to=account.email,
        subject="You've been invited to the Animal Behavior application",
        text_body=(
            f"Hi {account.display_name},\n\n"
            "An Admin has created an account for you. Set your password to get started:\n"
            f"{invite_link}\n\n"
            f"This link expires in {settings.invite_token_expire_hours} hours."
        ),
    )


def create_invited_account(db: Session, mail_transport: MailTransport, email: str) -> Account:
    """Create a User Account and email it an invite link to set a password.

    Raises InvalidEmailDomainError / DuplicateActiveAccountError — both
    mapped to specific (not generic) HTTP responses by the API layer, since
    this is an Admin-only endpoint: unlike login, there's no reason to hide
    *why* creation failed from an already-authenticated Admin.
    """
    normalized_email = email.strip().lower()
    validate_email_domain(normalized_email)

    existing_active = db.scalar(
        select(Account).where(Account.email == normalized_email, Account.is_active.is_(True))
    )
    if existing_active is not None:
        raise DuplicateActiveAccountError

    account = Account(
        email=normalized_email,
        # No password until the invite is followed (CONTEXT.md's
        # "New-account activation" decision) — see the Account model's note
        # on why password_hash is nullable.
        password_hash=None,
        display_name=derive_display_name(normalized_email),
        role=AccountRole.USER,
        is_active=True,
    )
    db.add(account)
    db.flush()  # assigns account.id, needed for the token's FK below

    raw_token = generate_opaque_token()
    db.add(
        AccountActionToken(
            account_id=account.id,
            token_hash=hash_opaque_token(raw_token),
            purpose=AccountActionTokenPurpose.INVITE,
            expires_at=datetime.now(UTC) + timedelta(hours=settings.invite_token_expire_hours),
        )
    )
    db.commit()
    db.refresh(account)

    mail_transport.send(_build_invite_email(account, raw_token))

    return account
