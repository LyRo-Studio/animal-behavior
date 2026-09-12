import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AccountRole(str, enum.Enum):
    """An Account's role — see CONTEXT.md's "Account"/"Admin"/"User" entries."""

    ADMIN = "admin"
    USER = "user"


class Account(Base):
    """A stored identity with an email address and password hash, used to log in.

    Every Account has exactly one role, Admin or User (CONTEXT.md).
    """

    __tablename__ = "accounts"
    __table_args__ = (
        # Uniqueness is scoped to *active* accounts only (CONTEXT.md's
        # "Account removal" decision): deactivating an Account frees its
        # email address for reuse by a new Account rather than blocking it
        # forever. A plain unique constraint on the column would prevent
        # that reuse.
        Index(
            "ix_accounts_email_active_unique",
            "email",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not unique at the column level — see the partial index above. Indexed
    # (non-uniquely) regardless, since login must look up an email whether
    # or not that account is currently active (it always returns the same
    # generic failure either way).
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    # Nullable: a User Admin-invited (ticket #4) has no password until they
    # follow their invite link and set one — login always rejects a None
    # hash rather than attempting to verify against it.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[AccountRole] = mapped_column(
        Enum(
            AccountRole,
            name="account_role",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(  # noqa: F821
        back_populates="account", cascade="all, delete-orphan"
    )
    action_tokens: Mapped[list["AccountActionToken"]] = relationship(  # noqa: F821
        back_populates="account", cascade="all, delete-orphan"
    )
