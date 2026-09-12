from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RefreshToken(Base):
    """An opaque, rotating refresh token issued at login.

    Checked against the database on every /auth/refresh call (see
    docs/adr/0001-jwt-access-refresh-tokens.md) — this is what makes a
    deactivated Account get locked out within one refresh cycle instead of
    only once its access token naturally expires.

    Only a hash of the raw token is ever persisted, mirroring password
    handling: a leaked row must not itself be a usable credential.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    account: Mapped["Account"] = relationship(back_populates="refresh_tokens")  # noqa: F821
