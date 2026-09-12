from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.account import Account, AccountRole

DEFAULT_PASSWORD = "correct-horse-battery-staple"


def create_account(
    db_session: Session,
    *,
    email: str,
    password: str = DEFAULT_PASSWORD,
    display_name: str = "Test",
    role: AccountRole = AccountRole.USER,
    is_active: bool = True,
) -> Account:
    """Insert an Account directly via the DB session, bypassing the API.

    Rolled back automatically at the end of the test along with everything
    else the `db_session` fixture touches.
    """
    account = Account(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role=role,
        is_active=is_active,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account
