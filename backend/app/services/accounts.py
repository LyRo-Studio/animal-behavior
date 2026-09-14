"""Account-level helpers shared across services: display-name derivation and
first-Admin seeding (see CONTEXT.md's "Display name" and "Bootstrap" decisions).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.account import Account, AccountRole
from app.services.refresh_tokens import revoke_all_refresh_tokens

ALLOWED_EMAIL_DOMAINS = frozenset({"vives.be", "student.vives.be"})


class InvalidEmailDomainError(Exception):
    """Raised when an email's domain isn't in ALLOWED_EMAIL_DOMAINS."""


class AccountNotFoundError(Exception):
    """Raised when an Admin operates on an Account id that doesn't exist."""


class CannotDeactivateAdminError(Exception):
    """Raised when attempting to deactivate an Admin Account.

    CONTEXT.md's "Admin creation" decision: only User accounts can be
    created/deactivated through the app this round.
    """


def validate_email_domain(email: str) -> None:
    """Enforce CONTEXT.md's "Allowed email domains" decision (case-insensitive)."""
    domain = email.rsplit("@", 1)[-1].lower()
    if domain not in ALLOWED_EMAIL_DOMAINS:
        raise InvalidEmailDomainError(domain)


def derive_display_name(email: str) -> str:
    """Derive a display name from an email's local-part.

    The token before the first `.` (e.g. `jan.peeters@vives.be` -> "Jan"),
    title-cased. CONTEXT.md's "Display name" decision.
    """
    local_part = email.split("@", 1)[0]
    first_token = local_part.split(".", 1)[0]
    return first_token.title()


def seed_first_admin(db: Session) -> None:
    """Create the first Admin account from .env credentials, if none exists yet.

    Idempotent: does nothing once any Admin account exists, so repeated
    application startups never create a second one (CONTEXT.md's
    "Bootstrap" decision — there is exactly one Admin this round).
    """
    existing_admin = db.scalar(select(Account).where(Account.role == AccountRole.ADMIN))
    if existing_admin is not None:
        return

    email = settings.first_admin_email.strip().lower()
    admin = Account(
        email=email,
        password_hash=hash_password(settings.first_admin_password),
        display_name=derive_display_name(email),
        role=AccountRole.ADMIN,
        is_active=True,
    )
    db.add(admin)
    db.commit()


def deactivate_account(db: Session, account_id: int) -> Account:
    """Deactivate a User Account and immediately revoke its outstanding refresh tokens.

    Per issue #6's acceptance criteria: `is_active = False` locks the
    Account out of login right away (app/services/auth.py's login already
    rejects any inactive account), and revoking its refresh tokens here is
    defense-in-depth on top of /auth/refresh's own is_active check — the
    same belt-and-braces pattern as set_password's post-reset revocation
    (app/services/account_actions.py) rather than waiting on the account's
    next refresh cycle.

    Restricted to User-role Accounts (CannotDeactivateAdminError otherwise):
    this round has exactly one Admin, and deactivating it would permanently
    lock the application out of account management — see
    CannotDeactivateAdminError's docstring.

    Idempotent: deactivating an already-inactive User just re-affirms
    is_active = False and revokes any tokens somehow still outstanding.
    """
    account = db.get(Account, account_id)
    if account is None:
        raise AccountNotFoundError
    if account.role != AccountRole.USER:
        raise CannotDeactivateAdminError

    account.is_active = False
    db.add(account)

    revoke_all_refresh_tokens(db, account.id, datetime.now(UTC))

    db.commit()
    db.refresh(account)
    return account
