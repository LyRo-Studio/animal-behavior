"""clear the source paths of succeeded cutting jobs (issue #172)

A cutting job's `c1_source_path`/`c2_source_path` now mean "this source is
still on disk" (`CuttingJob.source_retained`): every delete clears a path
once its upload directory is gone. Until now the cutting-worker deleted a
succeeded job's source but left its paths set, so every existing succeeded
job would claim a source it no longer has. Every other job keeps its paths:
a failed job's source is kept for a retry, and a queued or running one
still needs it.

Irreversible: downgrade() is a no-op, since the deleted directories can't
be pointed at again.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE cutting_jobs SET c1_source_path = NULL, c2_source_path = NULL "
        "WHERE status = 'succeeded'"
    )


def downgrade() -> None:
    pass
