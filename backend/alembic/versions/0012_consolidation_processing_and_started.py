"""consolidation `processing` status and CONSOLIDATION_STARTED audit action
(ticket #115)

A Consolidation row is now created as `processing` before the runner
starts, so CONSOLIDATION_STARTED has an id to attribute to. A separate
migration rather than an edit to 0011, since 0011 may already be applied to
a local database.

Not reversible: Postgres has no `DROP VALUE` for an enum type (same caveat
as 0011), so downgrade() is a no-op.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-23

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction — see
    # 0011.
    op.execute("ALTER TYPE consolidation_status ADD VALUE IF NOT EXISTS 'processing'")
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'consolidation_started'")


def downgrade() -> None:
    pass
