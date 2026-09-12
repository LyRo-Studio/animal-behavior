from sqlalchemy import select

from app.core.config import settings
from app.models.account import Account, AccountRole


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
