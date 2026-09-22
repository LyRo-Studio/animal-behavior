"""analysis_job_tests (ticket #89 / issue #88's Feature B foundation)

`analysis_jobs.test_id` (a job belonged to exactly one Test) is replaced by
`analysis_job_tests`, a stored, indexed multi-Test association — a job can
now span 1-10 Tests. Existing rows are backfilled into the new shape with
their single existing Test before the old column is dropped.

Reversible: downgrade recreates `test_id`, backfilled from each job's first
associated Test (submission order) — lossy only for a job that came to span
more than one Test after this migration shipped, same spirit as other
migrations in this repo accepting a narrower downgrade than the upgrade it
undoes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_job_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_id", sa.String(length=50), nullable=False),
        sa.UniqueConstraint("analysis_id", "test_id", name="uq_analysis_job_tests_analysis_test"),
    )
    op.create_index("ix_analysis_job_tests_analysis_id", "analysis_job_tests", ["analysis_id"])
    op.create_index("ix_analysis_job_tests_test_id", "analysis_job_tests", ["test_id"])

    op.execute(
        sa.text(
            "INSERT INTO analysis_job_tests (analysis_id, test_id) "
            "SELECT id, test_id FROM analysis_jobs"
        )
    )

    op.drop_index("ix_analysis_jobs_test_id", table_name="analysis_jobs")
    op.drop_column("analysis_jobs", "test_id")


def downgrade() -> None:
    op.add_column("analysis_jobs", sa.Column("test_id", sa.String(length=50), nullable=True))
    op.execute(
        sa.text(
            "UPDATE analysis_jobs SET test_id = ("
            "SELECT test_id FROM analysis_job_tests "
            "WHERE analysis_job_tests.analysis_id = analysis_jobs.id "
            "ORDER BY analysis_job_tests.id LIMIT 1"
            ")"
        )
    )
    op.alter_column("analysis_jobs", "test_id", nullable=False)
    op.create_index("ix_analysis_jobs_test_id", "analysis_jobs", ["test_id"])

    op.drop_table("analysis_job_tests")
