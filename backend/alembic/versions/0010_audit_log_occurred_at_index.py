"""audit_log occurred_at index (ticket #87)

The daily retention prune's `DELETE ... WHERE occurred_at < cutoff`
(app/services/audit_log.py's prune_old_audit_events) would otherwise be a
full table scan on every run, competing with audit_log's own frequent
inserts for the duration — caught in review.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-22

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_audit_log_occurred_at", "audit_log", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_occurred_at", table_name="audit_log")
