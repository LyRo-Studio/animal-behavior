"""analysis_jobs and analysis_job_videos (ticket #45, part of #44)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `create_type=False`: created explicitly (once, with checkfirst) in
# upgrade() below — see the identical note on `account_role` in 0002.
analysis_job_status = postgresql.ENUM(
    "queued",
    "running",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
    name="analysis_job_status",
    create_type=False,
)
analysis_job_video_status = postgresql.ENUM(
    "pending",
    "processing",
    "succeeded",
    "failed",
    name="analysis_job_video_status",
    create_type=False,
)


def upgrade() -> None:
    analysis_job_status.create(op.get_bind(), checkfirst=True)
    analysis_job_video_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("test_id", sa.String(length=50), nullable=False),
        sa.Column(
            "requested_by",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", analysis_job_status, nullable=False, server_default="queued"),
        sa.Column("dogtrace_version", sa.String(length=50), nullable=True),
        sa.Column("report_s3_prefix", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analysis_jobs_test_id", "analysis_jobs", ["test_id"])
    op.create_index("ix_analysis_jobs_requested_by", "analysis_jobs", ["requested_by"])

    op.create_table(
        "analysis_job_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cut_key", sa.String(length=1024), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", analysis_job_video_status, nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.UniqueConstraint(
            "analysis_id", "position", name="uq_analysis_job_videos_analysis_position"
        ),
    )
    op.create_index("ix_analysis_job_videos_analysis_id", "analysis_job_videos", ["analysis_id"])


def downgrade() -> None:
    op.drop_table("analysis_job_videos")
    op.drop_table("analysis_jobs")
    analysis_job_video_status.drop(op.get_bind(), checkfirst=True)
    analysis_job_status.drop(op.get_bind(), checkfirst=True)
