from sqlalchemy import select

from app.core.config import settings
from app.models.account import Account, AccountRole
from app.services.accounts import seed_first_admin
from tests.helpers import create_account


def test_seeded_first_admin_can_log_in(client, db_session):
    """The seeded Admin (see app/services/accounts.py:seed_first_admin,
    invoked from the app's lifespan) exists and authenticates with the
    .env-configured credentials."""
    response = client.post(
        "/auth/login",
        json={"email": settings.first_admin_email, "password": settings.first_admin_password},
    )

    assert response.status_code == 200


def test_seeding_is_idempotent_across_app_startups(client, db_session):
    admins = db_session.scalars(select(Account).where(Account.role == AccountRole.ADMIN)).all()

    assert len(admins) == 1


def test_seed_first_admin_reactivates_an_inactive_sole_admin(client, db_session):
    """Ticket #13: seed_first_admin used to only check whether *any* Admin
    row existed, so an inactive sole Admin left the app with zero working
    Admins and no in-app recovery path (deactivate_account refuses to
    deactivate an Admin, and this round has no way to create a second one,
    so this state isn't reachable through the app today — but startup
    seeding should recover from it if it's ever reached some other way,
    e.g. a manual data edit)."""
    admin = db_session.scalar(select(Account).where(Account.role == AccountRole.ADMIN))
    assert admin is not None, "the client fixture's own startup should have seeded one"
    admin.is_active = False
    db_session.add(admin)
    db_session.commit()

    seed_first_admin(db_session)
    db_session.refresh(admin)

    assert admin.is_active is True

    response = client.post(
        "/auth/login",
        json={"email": admin.email, "password": settings.first_admin_password},
    )
    assert response.status_code == 200


def test_seed_first_admin_does_not_crash_on_an_email_conflict(client, db_session):
    """If the inactive Admin's email is also held by a different, active
    Account (only reachable via a manual data edit — the app's own
    account-creation paths already refuse to create a duplicate for an
    existing email, active or not, per ticket #15), reactivating it would
    violate the partial unique index scoped to active accounts. This must
    not raise: seed_first_admin runs unconditionally in the app's startup
    lifespan with nothing to catch it, so an unhandled error here would
    crash the whole app, not just this recovery path."""
    admin = db_session.scalar(select(Account).where(Account.role == AccountRole.ADMIN))
    assert admin is not None
    admin.is_active = False
    db_session.add(admin)
    db_session.commit()

    create_account(db_session, email=admin.email, role=AccountRole.USER, is_active=True)

    seed_first_admin(db_session)  # must not raise
    db_session.refresh(admin)

    assert admin.is_active is False
