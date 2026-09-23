"""CONSOLIDATION_RENAMED audit action (ticket #117)

Not reversible: Postgres has no `DROP VALUE` for an enum type (same caveat
as 0011/0012), so downgrade() is a no-op.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-23

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction — see
    # 0011.
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'consolidation_renamed'")


def downgrade() -> None:
    pass
