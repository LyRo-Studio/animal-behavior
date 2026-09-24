"""CUTTING_STARTED/COMPLETED/FAILED audit actions (ticket #99)

Not reversible: Postgres has no `DROP VALUE` for an enum type (same caveat
as 0011-0014), so downgrade() is a no-op.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-24

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction — see
    # 0011.
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'cutting_started'")
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'cutting_completed'")
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'cutting_failed'")


def downgrade() -> None:
    pass
