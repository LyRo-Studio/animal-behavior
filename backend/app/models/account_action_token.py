import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AccountActionTokenPurpose(str, enum.Enum):
    """What an AccountActionToken authorizes — see issue #1's "Tokens" decision.

    A single table serves both flows since they're functionally identical:
    a one-time, expiring, emailed link that lets an Account set a new
    password. PASSWORD_RESET is unused until ticket #5 (Forgot-password
    reset) but the shape is shared now rather than adding a second table
    later.
    """

    INVITE = "invite"
    PASSWORD_RESET = "password_reset"


class AccountActionToken(Base):
    """A one-time, expiring token authorizing an Account to set a password.

    Only a hash of the raw token is ever persisted — mirroring password and
    refresh-token handling, the raw value is emailed and never stored.
    """

    __tablename__ = "account_action_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    purpose: Mapped[AccountActionTokenPurpose] = mapped_column(
        Enum(
            AccountActionTokenPurpose,
            name="account_action_token_purpose",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    account: Mapped["Account"] = relationship(back_populates="action_tokens")  # noqa: F821
