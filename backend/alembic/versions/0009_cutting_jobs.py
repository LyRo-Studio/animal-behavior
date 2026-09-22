"""cutting_jobs and cutting_job_outputs (ticket #94, part of issue #93's
Feature C)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `create_type=False`: created explicitly (once, with checkfirst) in
# upgrade() below — see the identical note on `analysis_job_status` in 0005.
cutting_job_status = postgresql.ENUM(
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    name="cutting_job_status",
    create_type=False,
)
cutting_job_output_status = postgresql.ENUM(
    "pending",
    "succeeded",
    "failed",
    name="cutting_job_output_status",
    create_type=False,
)


def upgrade() -> None:
    cutting_job_status.create(op.get_bind(), checkfirst=True)
    cutting_job_output_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "cutting_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("test_id", sa.String(length=50), nullable=False),
        sa.Column("requested_by_identity", sa.String(length=320), nullable=True),
        sa.Column("status", cutting_job_status, nullable=False, server_default="queued"),
        sa.Column("reference_camera", sa.String(length=10), nullable=False),
        sa.Column("phase_timestamps", postgresql.JSONB(), nullable=False),
        sa.Column("c1_source_path", sa.String(length=1024), nullable=True),
        sa.Column("c2_source_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cutting_jobs_test_id", "cutting_jobs", ["test_id"])

    op.create_table(
        "cutting_job_outputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "cutting_job_id",
            sa.Integer(),
            sa.ForeignKey("cutting_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("camera", sa.String(length=10), nullable=False),
        sa.Column("condition", sa.String(length=10), nullable=False),
        sa.Column("phase", sa.String(length=10), nullable=False),
        sa.Column("status", cutting_job_output_status, nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.UniqueConstraint(
            "cutting_job_id",
            "camera",
            "condition",
            "phase",
            name="uq_cutting_job_outputs_job_camera_condition_phase",
        ),
    )
    op.create_index(
        "ix_cutting_job_outputs_cutting_job_id", "cutting_job_outputs", ["cutting_job_id"]
    )


def downgrade() -> None:
    op.drop_table("cutting_job_outputs")
    op.drop_table("cutting_jobs")
    cutting_job_output_status.drop(op.get_bind(), checkfirst=True)
    cutting_job_status.drop(op.get_bind(), checkfirst=True)
