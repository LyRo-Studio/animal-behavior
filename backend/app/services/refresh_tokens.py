"""Shared refresh-token revocation helper.

Used wherever an Account's outstanding sessions must be forced to
re-authenticate: completing a set-password flow (invite or reset —
app/services/account_actions.py) and deactivating an Account
(app/services/accounts.py).
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.refresh_token import RefreshToken


def revoke_all_refresh_tokens(db: Session, account_id: int, revoked_at: datetime) -> None:
    """Mark every not-yet-revoked RefreshToken for this Account as revoked.

    Does not commit — the caller commits as part of its own transaction
    alongside whatever else it changed (a password, an is_active flag, ...).
    """
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.account_id == account_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=revoked_at)
    )
