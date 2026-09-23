"""CONSOLIDATION_DELETED audit action (ticket #118)

Not reversible: Postgres has no `DROP VALUE` for an enum type (same caveat
as 0011-0013), so downgrade() is a no-op.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-23

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction — see
    # 0011.
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'consolidation_deleted'")


def downgrade() -> None:
    pass
