"""accounts and refresh_tokens (ticket #3: Login & session)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `create_type=False`: the type is created explicitly (once, with
# checkfirst) in upgrade() below, rather than implicitly by create_table —
# implicit creation doesn't check-first and would fail with "already
# exists" against the explicit create() call.
account_role = postgresql.ENUM("admin", "user", name="account_role", create_type=False)


def upgrade() -> None:
    account_role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("role", account_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_accounts_email", "accounts", ["email"])
    # Uniqueness scoped to active accounts only, so a deactivated account's
    # email can be reused by a new account (CONTEXT.md's "Account removal"
    # decision).
    op.create_index(
        "ix_accounts_email_active_unique",
        "accounts",
        ["email"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_refresh_tokens_account_id", "refresh_tokens", ["account_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("accounts")
    account_role.drop(op.get_bind(), checkfirst=True)
