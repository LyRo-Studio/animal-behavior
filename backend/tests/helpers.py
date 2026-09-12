from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.account import Account, AccountRole

DEFAULT_PASSWORD = "correct-horse-battery-staple"


def create_account(
    db_session: Session,
    *,
    email: str,
    password: str | None = DEFAULT_PASSWORD,
    display_name: str = "Test",
    role: AccountRole = AccountRole.USER,
    is_active: bool = True,
) -> Account:
    """Insert an Account directly via the DB session, bypassing the API.

    `password=None` mimics an Admin-invited User who hasn't set one yet
    (see the Account model's note on why password_hash is nullable).

    Rolled back automatically at the end of the test along with everything
    else the `db_session` fixture touches.
    """
    account = Account(
        email=email,
        password_hash=hash_password(password) if password is not None else None,
        display_name=display_name,
        role=role,
        is_active=is_active,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def login_headers(
    client: TestClient, email: str, password: str = DEFAULT_PASSWORD
) -> dict[str, str]:
    """Log in via the real endpoint and return an Authorization header for the result."""
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    access_token = response.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}
