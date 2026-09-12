"""Account-level helpers shared across services: display-name derivation and
first-Admin seeding (see CONTEXT.md's "Display name" and "Bootstrap" decisions).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.account import Account, AccountRole

ALLOWED_EMAIL_DOMAINS = frozenset({"vives.be", "student.vives.be"})


class InvalidEmailDomainError(Exception):
    """Raised when an email's domain isn't in ALLOWED_EMAIL_DOMAINS."""


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
