"""initial (walking-skeleton) revision

Revision ID: 0001
Revises:
Create Date: 2026-09-12

Intentionally a no-op: this revision exists to prove Alembic is wired up
to the database end-to-end. The Account / refresh-token schema arrives in
ticket #3 (Login & session).
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
