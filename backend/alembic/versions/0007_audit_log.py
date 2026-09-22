"""audit_log (Feature A, ticket #82, issue #79)

The immutable accountability record and its `action` enum. Reversible —
unlike ticket #72's migration, nothing here is destructive to reconstruct.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `create_type=False`: created explicitly (once, with checkfirst) in
# upgrade() below — see the identical note on `analysis_job_status` in 0005.
audit_action = postgresql.ENUM(
    "analysis_started",
    "analysis_completed",
    "analysis_completed_with_errors",
    "analysis_failed",
    "analysis_cancelled",
    "report_downloaded",
    "cut_play_requested",
    "cut_download_requested",
    name="audit_action",
    create_type=False,
)


def upgrade() -> None:
    audit_action.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("identity", sa.String(length=320), nullable=True),
        sa.Column("identity_verified", sa.Boolean(), nullable=False),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target", sa.String(length=1024), nullable=False),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    audit_action.drop(op.get_bind(), checkfirst=True)
