"""account_action_tokens; accounts.password_hash nullable (ticket #4: Admin adds a User)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `create_type=False`: created explicitly (once, with checkfirst) in
# upgrade() below — see the identical note on `account_role` in 0002.
account_action_token_purpose = postgresql.ENUM(
    "invite", "password_reset", name="account_action_token_purpose", create_type=False
)


def upgrade() -> None:
    # A User Admin-invited (this ticket) has no password until they follow
    # their invite link and set one (CONTEXT.md's "New-account activation"
    # decision).
    op.alter_column("accounts", "password_hash", existing_type=sa.String(length=255), nullable=True)

    account_action_token_purpose.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "account_action_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("purpose", account_action_token_purpose, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_account_action_tokens_account_id", "account_action_tokens", ["account_id"])
    op.create_index("ix_account_action_tokens_token_hash", "account_action_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_table("account_action_tokens")
    account_action_token_purpose.drop(op.get_bind(), checkfirst=True)
    op.alter_column(
        "accounts", "password_hash", existing_type=sa.String(length=255), nullable=False
    )
