"""consolidations (ticket #115, part of issue #113's Excel consolidation
feature)

Also extends `audit_action` with CONSOLIDATION_COMPLETED/FAILED/DOWNLOADED
— the first migration to grow this enum after its initial creation (0007),
so via `ALTER TYPE ... ADD VALUE` rather than a fresh CREATE. Not fully
reversible: Postgres has no `DROP VALUE` for an enum type, so downgrade()
can't remove these three values again (same "not fully reversible" caveat
0006's docstring flags for a different reason) — only the new table is
dropped.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `create_type=False`: created explicitly (once, with checkfirst) in
# upgrade() below — see the identical note on `analysis_job_status` in 0005.
consolidation_condition = postgresql.ENUM(
    "ME",
    "ZE",
    "ME_ZE",
    name="consolidation_condition",
    create_type=False,
)
consolidation_status = postgresql.ENUM(
    "completed",
    "failed",
    name="consolidation_status",
    create_type=False,
)


def upgrade() -> None:
    consolidation_condition.create(op.get_bind(), checkfirst=True)
    consolidation_status.create(op.get_bind(), checkfirst=True)

    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction (the
    # new value just can't be *used* in that same transaction, which this
    # migration doesn't need to). docker-compose.yml runs postgres:16-alpine.
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'consolidation_completed'")
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'consolidation_failed'")
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'consolidation_downloaded'")

    op.create_table(
        "consolidations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("condition", consolidation_condition, nullable=False),
        sa.Column("status", consolidation_status, nullable=False),
        sa.Column("requested_by_identity", sa.String(length=320), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("result_storage_key", sa.String(length=1024), nullable=True),
        sa.Column("input_size_bytes", sa.Integer(), nullable=False),
        sa.Column("result_size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("consolidations")
    consolidation_status.drop(op.get_bind(), checkfirst=True)
    consolidation_condition.drop(op.get_bind(), checkfirst=True)
