"""remove the consolidation Condition (ticket #149)

One upload now produces every Consolidation level (with_owner,
without_owner, combined) in one result workbook, so a Consolidation no
longer has a Condition. Every existing Consolidation was made under the old
per-Condition format and is deleted; their stored results are removed
separately by `python -m app.commands.remove_unreferenced_consolidation_results`,
since a migration has no storage client. The audit log is untouched: its
rows reference a consolidation by id only, with no foreign key.

Irreversible: the deleted rows are gone. downgrade() restores only the
column and its enum type. A row created after the upgrade holds every
level, so it gets ME_ZE, the old Condition covering both.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `create_type=False`: created explicitly in downgrade() — see the identical
# note in 0011.
consolidation_condition = postgresql.ENUM(
    "ME",
    "ZE",
    "ME_ZE",
    name="consolidation_condition",
    create_type=False,
)


def upgrade() -> None:
    op.execute("DELETE FROM consolidations")
    op.drop_column("consolidations", "condition")
    consolidation_condition.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    consolidation_condition.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "consolidations",
        sa.Column("condition", consolidation_condition, nullable=False, server_default="ME_ZE"),
    )
    # The default only fills existing rows; 0011's column has none.
    op.alter_column("consolidations", "condition", server_default=None)
