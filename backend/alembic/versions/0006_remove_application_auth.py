"""remove application-level authentication (ticket #72)

Mechatronics now authenticates everyone before a request reaches the app
(docs/adr/0004-trust-mega-tronics-remove-application-auth.md), so the account
system goes: `analysis_jobs.requested_by` (FK to `accounts`) becomes
`requested_by_identity`, a plain nullable string holding the raw identity
header value, and the `accounts`, `refresh_tokens` and `account_action_tokens`
tables (plus their enum types) are dropped.

Each existing analysis keeps its "run by" attribution: the new column is
backfilled from the email of the account it used to belong to, *before* the
old column and the tables it pointed at are dropped.

Irreversible: the dropped tables' rows (accounts, password hashes, tokens) are
gone for good, so there is nothing a downgrade could restore.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

account_role = postgresql.ENUM("admin", "user", name="account_role", create_type=False)
account_action_token_purpose = postgresql.ENUM(
    "invite", "password_reset", name="account_action_token_purpose", create_type=False
)


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        # Same length as `accounts.email` — sized for an email address, the
        # shape of identity Mechatronics is expected to forward.
        sa.Column("requested_by_identity", sa.String(length=320), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE analysis_jobs SET requested_by_identity = accounts.email "
            "FROM accounts WHERE accounts.id = analysis_jobs.requested_by"
        )
    )

    # Dropping the column also drops its foreign key to `accounts`.
    op.drop_index("ix_analysis_jobs_requested_by", table_name="analysis_jobs")
    op.drop_column("analysis_jobs", "requested_by")

    # Both token tables reference `accounts`, so they go first.
    op.drop_table("account_action_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("accounts")

    account_action_token_purpose.drop(op.get_bind(), checkfirst=True)
    account_role.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError(
        "0006 drops the accounts, refresh_tokens and account_action_tokens tables "
        "(and their data) for good; there is nothing to restore them from."
    )
