"""Account-level helpers shared across services: display-name derivation and
first-Admin seeding (see CONTEXT.md's "Display name" and "Bootstrap" decisions).
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.account import Account, AccountRole
from app.services.refresh_tokens import revoke_all_refresh_tokens

logger = logging.getLogger(__name__)

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


class DuplicateActiveAccountError(Exception):
    """Raised when an active Account already exists for the given email."""


class DeactivatedAccountExistsError(Exception):
    """Raised when creating an Account whose email matches an existing, but
    deactivated, Account.

    CONTEXT.md's revised "Account removal" decision: an email already
    attached to an Account (active or not) is reused by *reactivating*
    that Account, never by creating a second row for the same address —
    the old behavior (silently minting a new row) let two Accounts end up
    sharing an email if the original one were later reactivated too.
    """

    def __init__(self, account_id: int) -> None:
        self.account_id = account_id
        super().__init__(account_id)


def normalize_email(email: str) -> str:
    """The one normalization rule for an email address across this app:
    stripped, lower-cased. Used everywhere an email is looked up, stored,
    or keyed on (accounts, tokens, rate-limit buckets) so two spellings of
    the same address are always treated as the same address — previously
    duplicated inline at several call sites, which risked exactly the kind
    of silent drift a shared helper prevents.
    """
    return email.strip().lower()


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
    """Create the first Admin account from .env credentials, if none exists
    yet — or reactivate it if it exists but is inactive.

    Idempotent: does nothing once an *active* Admin account exists, so
    repeated application startups never create a second one (CONTEXT.md's
    "Bootstrap" decision — there is exactly one Admin this round).

    Reactivates rather than no-ops on an inactive sole Admin: there's no
    live path to reach that state today (deactivate_account refuses to
    deactivate an Admin, and this round has no way to create a second one
    — see CannotDeactivateAdminError), but silently no-op'ing here would
    otherwise leave the app with zero working Admins and no in-app
    recovery path if that state were ever reached some other way (e.g. a
    manual data edit, or a future round loosening either restriction) —
    see ticket #13 and CONTEXT.md's "Bootstrap" decision.

    Guards against the same email-uniqueness conflict reactivate_account
    does (Account.email's partial unique index only covers active rows,
    so an inactive Admin's email can be held by a different, currently-
    active Account — again only reachable via a manual data edit, the
    same escape hatch that lets the Admin go inactive in the first
    place). Unlike reactivate_account, this doesn't raise on a conflict:
    this function runs unconditionally in the app's startup lifespan with
    nothing to catch it, so raising would crash the entire app on startup
    over a single bad row — a worse failure than the one this recovery
    path exists to fix. It logs a warning and leaves the Admin inactive
    instead, which is the pre-existing (broken) state, not a new one.
    """
    existing_admin = db.scalar(select(Account).where(Account.role == AccountRole.ADMIN))
    if existing_admin is not None:
        if not existing_admin.is_active:
            conflict = db.scalar(
                select(Account).where(
                    Account.email == existing_admin.email,
                    Account.is_active.is_(True),
                    Account.id != existing_admin.id,
                )
            )
            if conflict is not None:
                logger.warning(
                    "seed_first_admin: cannot reactivate inactive sole Admin "
                    "(id=%s): its email is already held by a different, "
                    "active Account (id=%s). Leaving it inactive.",
                    existing_admin.id,
                    conflict.id,
                )
                return
            existing_admin.is_active = True
            db.add(existing_admin)
            db.commit()
        return

    email = normalize_email(settings.first_admin_email)
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


def reactivate_account(db: Session, account_id: int) -> Account:
    """Reactivate a previously deactivated Account, restoring its email.

    Idempotent: reactivating an already-active Account is a no-op that
    just returns it as-is.

    Raises DuplicateActiveAccountError if another Account is somehow
    already active with this same email. That shouldn't be reachable
    through normal app usage — create_invited_account now refuses to
    create a second row for an email that already belongs to any existing
    Account, active or not (ticket #15) — so this check is a
    belt-and-braces safety net against races/manual data edits, the same
    spirit as deactivate_account's own defense-in-depth token revocation.
    """
    account = db.get(Account, account_id)
    if account is None:
        raise AccountNotFoundError
    if account.is_active:
        return account

    conflict = db.scalar(
        select(Account).where(
            Account.email == account.email,
            Account.is_active.is_(True),
            Account.id != account.id,
        )
    )
    if conflict is not None:
        raise DuplicateActiveAccountError

    account.is_active = True
    db.add(account)
    db.commit()
    db.refresh(account)
    return account
